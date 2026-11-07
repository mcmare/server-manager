import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from .compose import ComposeProject,all_containers,compose_command
from .models import OperationResult

@dataclass(frozen=True)
class CleanupPlan:
    project: ComposeProject
    container_ids: tuple[str,...]
    image_candidates: tuple[str,...]
    blockers: tuple[str,...]
    fingerprint: str
    protected_mounts: tuple[str,...]

def _snapshot(project,runner):
    objects=all_containers(runner)
    members=[o for o in objects if (o.get('Config') or {}).get('Labels',{}).get('com.docker.compose.project')==project.name]
    members.sort(key=lambda o:o['Id'])
    return objects,members

def _fingerprint(members):
    return hashlib.sha256(json.dumps(members,sort_keys=True,ensure_ascii=True).encode()).hexdigest()

def inspect_cleanup(project:ComposeProject,runner)->CleanupPlan:
    objects,members=_snapshot(project,runner)
    blockers=[]
    ids=tuple(o['Id'] for o in members)
    if not ids or ids!=tuple(sorted(project.container_ids)):
        blockers.append('Неполная или изменённая инвентаризация контейнеров проекта')
    if not project.config_file or ',' in project.config_file or not Path(project.config_file).is_absolute():
        blockers.append('Неоднозначный Compose-файл')
    names={((o.get('Config') or {}).get('Labels') or {}).get('com.docker.compose.service') for o in members}
    if names!=set(project.services):blockers.append('Несовпадение сервисов')
    mounts=[]
    volume_records=[m for o in members for m in o.get('Mounts',[]) if m.get('Type')=='volume']
    named_volumes=set()
    if volume_records:
        try:
            config=runner.run(['docker','compose','-f',project.config_file,'-p',project.name,'config','--format','json'],timeout=30)
            if config.returncode:raise RuntimeError(config.stderr)
            top=json.loads(config.stdout).get('volumes',{})
            if not isinstance(top,dict):raise ValueError('bad volumes')
            for short,detail in top.items():
                detail=detail or {}
                if not isinstance(detail,dict):raise ValueError('bad volume')
                named_volumes.add(str(detail.get('name') or (project.name+'_'+short)))
        except (RuntimeError,ValueError,TypeError,KeyError):
            blockers.append('Нельзя подтвердить тип Docker volumes по Compose config')
    for o in members:
        for mount in o.get('Mounts',[]):
            if mount.get('Type')=='volume' and mount.get('Name') not in named_volumes:
                blockers.append('Возможный анонимный Docker volume: '+str(mount.get('Name')))
            mounts.append(str(mount.get('Source') or mount.get('Name') or '?')+' → '+str(mount.get('Destination') or '?'))
    critical_prefixes=('/data','/config','/var/lib/postgresql','/var/lib/mysql',
                       '/var/lib/mariadb','/var/lib/redis','/app/data','/home')
    for o in members:
        changes=runner.run(['docker','diff',o['Id']],timeout=30)
        if changes.returncode:
            blockers.append('Нельзя проверить writable layer контейнера '+o['Id'])
            continue
        mounted=[str(m.get('Destination','')).rstrip('/') for m in o.get('Mounts',[])]
        for line in changes.stdout.splitlines():
            if len(line)<3 or line[:2] not in ('A ','C ','D '):continue
            path=line[2:].strip()
            if any(path==prefix or path.startswith(prefix+'/') for prefix in critical_prefixes):
                if not any(path==dest or path.startswith(dest+'/') for dest in mounted if dest):
                    blockers.append('Важные изменения writable layer: '+o['Id']+': '+path)
                    break
    other_images={o['Image'] for o in objects if o['Id'] not in ids}
    images={o.get('Image') for o in members if o.get('Image')}
    candidates=[]
    # Only remove images recoverable from a verified remote manifest; local builds,
    # dangling tags and ambiguous multi-tag images are intentionally preserved.
    for img in sorted(images-other_images):
        if not img:continue
        inspect=runner.run(['docker','image','inspect',img],timeout=30)
        if inspect.returncode:continue
        try:
            metadata=json.loads(inspect.stdout)[0]
            tags=metadata.get('RepoTags') or []
            if len(tags)!=1 or tags[0]=='<none>:<none>':continue
            ref=tags[0]
            manifest=runner.run(['docker','manifest','inspect',ref],timeout=60)
            if manifest.returncode or not manifest.stdout.strip():continue
            index=json.loads(manifest.stdout)
            if not isinstance(index,dict) or not any(k in index for k in ('schemaVersion','manifests')):continue
            candidates.append(img)
        except (ValueError,IndexError,TypeError,KeyError):continue
    return CleanupPlan(project,ids,tuple(candidates),tuple(sorted(set(blockers))),_fingerprint(members),tuple(mounts))

def apply_cleanup(plan:CleanupPlan,runner,state,confirmed:bool,dry_run:bool=False,accept_writable_layer:bool=False)->OperationResult:
    if not confirmed:return OperationResult(False,'Требуется отдельное подтверждение удаления контейнеров')
    if plan.blockers:return OperationResult(False,'Очистка заблокирована: '+'; '.join(plan.blockers))
    if not accept_writable_layer:return OperationResult(False,'Нужно подтвердить сохранность данных writable layer')
    if dry_run:return OperationResult(True,'DRY RUN: удаление '+', '.join(plan.container_ids))
    if not Path(plan.project.config_file).is_file():return OperationResult(False,'Compose-файл отсутствует')
    objects,members=_snapshot(plan.project,runner)
    if _fingerprint(members)!=plan.fingerprint or tuple(o['Id'] for o in members)!=plan.container_ids:
        return OperationResult(False,'Состав проекта изменился после предварительной проверки')
    # Only stop/rm services belonging to the full inspected project, no volume flags.
    services=plan.project.services
    stop=runner.run(compose_command(plan.project,'stop',services),timeout=180)
    if stop.returncode:
        if state:state.record('cleanup-stop',plan.project.name,{'ok':False,'message':stop.stderr})
        return OperationResult(False,'Не удалось остановить проект: '+stop.stderr,True)
    rm=runner.run(compose_command(plan.project,'rm',services),timeout=180)
    if state:state.record('cleanup-rm',plan.project.name,{'ok':rm.returncode==0,'stderr':rm.stderr,'containers':plan.container_ids})
    if rm.returncode:return OperationResult(False,'Контейнеры могли быть удалены частично: '+rm.stderr,True)
    removed=[]; skipped=[]
    for img in plan.image_candidates:
        # Recheck references after container removal. Never force shared image deletion.
        try:
            current=all_containers(runner)
        except (RuntimeError,ValueError):
            skipped.append(img+': не удалось проверить ссылки');continue
        if any(o.get('Image')==img for o in current):
            skipped.append(img+': используется контейнером');continue
        result=runner.run(['docker','image','rm',img],timeout=90)
        if result.returncode:skipped.append(img+': '+result.stderr)
        else:removed.append(img)
        if state:state.record('cleanup-image',img,{'ok':result.returncode==0,'stderr':result.stderr})
    return OperationResult(not skipped,'Контейнеры удалены; images удалены: '+str(len(removed))+'; пропущены: '+str(skipped),bool(skipped))

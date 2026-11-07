import json
import re
from dataclasses import dataclass
from pathlib import Path
from .models import OperationResult

_SAFE=re.compile(r'^[a-zA-Z0-9][\w.-]*$')

@dataclass(frozen=True)
class ComposeProject:
    name: str
    workdir: str
    config_file: str
    services: tuple[str,...]
    container_ids: tuple[str,...]
    states: tuple[tuple[str, str, str], ...] = ()
    image_ids: tuple[str,...] = ()

@dataclass(frozen=True)
class ComposeAction:
    project: ComposeProject
    services: tuple[str,...]
    verb: str

def all_containers(runner):
    result=runner.run(['docker','ps','-aq','--no-trunc'])
    if result.returncode:raise RuntimeError(result.stderr)
    ids=result.stdout.split()
    if not ids:return []
    output=[]
    for offset in range(0,len(ids),50):
        res=runner.run(['docker','inspect']+ids[offset:offset+50])
        if res.returncode:raise RuntimeError(res.stderr)
        output+=json.loads(res.stdout)
    return output

def discover_compose(runner)->list[ComposeProject]:
    groups={}
    for obj in all_containers(runner):
        labels=(obj.get('Config') or {}).get('Labels') or {}
        name=labels.get('com.docker.compose.project'); service=labels.get('com.docker.compose.service')
        workdir=labels.get('com.docker.compose.project.working_dir','')
        files=labels.get('com.docker.compose.project.config_files','')
        if not name or not service: continue
        key=(name,workdir,files)
        entry=groups.setdefault(key,{'services':set(),'ids':set(),'states':[],'images':set()})
        entry['services'].add(service);entry['ids'].add(obj['Id'])
        state=obj.get('State') or {}
        health=(state.get('Health') or {}).get('Status') or ''
        status=state.get('Status') or ('running' if state.get('Running') else 'unknown')
        entry['states'].append((service,status,health))
        if obj.get('Image'): entry['images'].add(obj['Image'])
    result=[]
    for (name,workdir,files),info in groups.items():
        result.append(ComposeProject(name,workdir,files,tuple(sorted(info['services'])),tuple(sorted(info['ids'])),tuple(sorted(info['states'])),tuple(sorted(info['images']))))
    return sorted(result,key=lambda x:x.name)

def compose_command(project,verb,services=()):
    if verb not in ('stop','up','rm') or not _SAFE.fullmatch(project.name):raise ValueError('Недопустимая команда')
    if not project.workdir or not project.config_file or ',' in project.config_file:raise ValueError('Неоднозначная конфигурация Compose')
    if not Path(project.config_file).is_absolute():raise ValueError('Некорректный Compose-файл')
    if any(not _SAFE.fullmatch(s) for s in services):raise ValueError('Недопустимое имя сервиса')
    base=['docker','compose','-f',project.config_file,'-p',project.name]
    if verb=='up':return base+['up','-d']+list(services)
    if verb=='rm':return base+['rm','-f']+list(services)
    return base+['stop']+list(services)

def plan_compose_action(project:ComposeProject,services:tuple[str,...],verb:str)->ComposeAction:
    if verb not in ('start','stop'):raise ValueError('Недопустимое действие')
    if not services or any(s not in project.services for s in services):raise ValueError('Неизвестный сервис')
    return ComposeAction(project,services,verb)

def apply_compose_action(action:ComposeAction,runner,state,dry_run=False)->OperationResult:
    cmd=compose_command(action.project,'up' if action.verb=='start' else 'stop',action.services)
    if dry_run:return OperationResult(True,'DRY RUN: '+' '.join(cmd))
    if not Path(action.project.config_file).is_file():
        # A missing compose file must never silently fall back to default working directory.
        return OperationResult(False,'Compose-файл не найден: '+action.project.config_file)
    result=runner.run(cmd,timeout=180)
    if state:state.record('compose-'+action.verb,action.project.name,{'exit_code':result.returncode,'stderr':result.stderr})
    return OperationResult(result.returncode==0,result.stderr or result.stdout or 'Операция завершена')

def plan_restart_override(container_ids:tuple[str,...],runner):
    objects=all_containers(runner)
    matches={o['Id']:((o.get('HostConfig') or {}).get('RestartPolicy') or {}).get('Name','no') for o in objects if o['Id'] in container_ids}
    if len(matches)!=len(container_ids):raise RuntimeError('Контейнеры изменились')
    return matches

def apply_restart_override(restart_plan,runner,state,dry_run=False):
    results={}
    for cid,prior in restart_plan.items():
        if dry_run: results[cid]='dry-run';continue
        res=runner.run(['docker','update','--restart=no',cid])
        results[cid]=res.returncode==0
        if state:
            data=state.load_state();policies=data.setdefault('restart_policies',{})
            if res.returncode==0:policies[cid]=prior;state.save_state(data)
            state.record('docker-restart-disable',cid,{'ok':res.returncode==0,'previous':prior})
    return results

def service_statuses(project: ComposeProject) -> dict[str, str]:
    """Describe the current state of each service represented by discovered containers."""
    grouped: dict[str, list[tuple[str, str]]] = {service: [] for service in project.services}
    for service, status, health in project.states:
        grouped.setdefault(service, []).append((status, health))
    output = {}
    for service, entries in grouped.items():
        if not entries:
            output[service] = 'unknown'
        elif any(status == 'running' and health == 'unhealthy' for status, health in entries):
            output[service] = 'unhealthy'
        elif any(status == 'running' for status, health in entries):
            output[service] = 'running'
        elif any(status == 'restarting' for status, health in entries):
            output[service] = 'restarting'
        else:
            output[service] = entries[0][0]
    return output


def project_status(project: ComposeProject) -> str:
    states = service_statuses(project)
    total = len(states)
    running = sum(status in ('running', 'unhealthy') for status in states.values())
    if not total:
        return 'Неизвестно (0/0)'
    if any(status in ('unhealthy', 'restarting') for status in states.values()):
        return f'Проблема ({running}/{total})'
    if running == total:
        return f'Работает ({running}/{total})'
    if running == 0:
        return f'Остановлен (0/{total})'
    return f'Частично ({running}/{total})'

def project_resources(project: ComposeProject, runner, image_ids: set[str] | None = None) -> tuple[int | None, int, int]:
    """Return (present images, known images, containers); None means unavailable inventory."""
    if image_ids is None:
        response = runner.run(['docker', 'image', 'ls', '-q', '--no-trunc'])
        if response.returncode:
            return None, len(project.image_ids), len(project.container_ids)
        image_ids = set(response.stdout.splitlines())
    return len(set(project.image_ids) & image_ids), len(project.image_ids), len(project.container_ids)

import os
import re
import tempfile
import json
import datetime
from pathlib import Path
from .models import OperationResult
from .nginx import SiteChange

_NAME=re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}$')

def apply_site_change(change:SiteChange, runner, state, dry_run:bool=False)->OperationResult:
    if change.action not in ('enable','disable','create') or not _NAME.fullmatch(change.name):
        return OperationResult(False,'Недопустимое имя или действие')
    a=change.available_dir; e=change.enabled_dir
    file=a/change.name; link=e/change.name
    if a.is_symlink() or e.is_symlink():return OperationResult(False,'Каталоги не должны быть ссылками')
    if not a.is_dir() or not e.is_dir():return OperationResult(False,'Отсутствует каталог nginx')
    if file.is_symlink(): return OperationResult(False,'Исходный файл является ссылкой')
    if link.is_symlink() and link.resolve()!=file.resolve():
        return OperationResult(False,'Ссылка ведёт за пределы управляемого файла')
    if link.exists() and not link.is_symlink():return OperationResult(False,'Конфликт имени в sites-enabled')
    if change.action!='create' and not file.is_file():return OperationResult(False,'Файл отсутствует')
    if change.action=='create' and (file.exists() or link.is_symlink() or change.content is None):
        return OperationResult(False,'Файл уже существует или содержимое отсутствует')
    if dry_run: return OperationResult(True, f'DRY RUN: {change.action} {change.name}')
    old_link=link.is_symlink(); old_target=os.readlink(link) if old_link else None; old_file=file.exists()
    backup_root=state.root/'backups'
    backup_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    backup_root.chmod(0o700)
    backup_name=(change.name+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S%f')+'.json')
    backup=backup_root/backup_name
    before={'name':change.name,'action':change.action,'enabled':old_link,
            'file_exists':old_file,'content':file.read_text() if old_file else None,
            'symlink_target':os.readlink(link) if old_link else None}
    backup_fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(backup_fd,'w') as f:
        json.dump(before,f,ensure_ascii=False)
        f.flush();os.fsync(f.fileno())
    created_file=False
    try:
        if change.action=='create':
            fd,tmp=tempfile.mkstemp(dir=a,prefix='.server-manager-')
            try:
                os.fchmod(fd,0o644)
                with os.fdopen(fd,'w') as out:out.write(change.content)
                # Fail instead of overwriting an existing file in concurrent race.
                os.link(tmp,file);created_file=True
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
        if change.action in ('enable','create') and not old_link:link.symlink_to(file)
        if change.action=='disable' and old_link:link.unlink()
        check=runner.run(['nginx','-t'])
        if check.returncode:raise RuntimeError('nginx -t: '+check.stderr)
        reload=runner.run(['systemctl','reload','nginx'])
        if reload.returncode:raise RuntimeError('reload: '+reload.stderr)
        state.record('nginx-'+change.action,change.name,{'ok':True,'backup':str(backup)})
        return OperationResult(True,'Конфигурация применена')
    except (OSError,RuntimeError) as exc:
        rollback_errors=[]
        try:
            if old_link and not link.is_symlink():link.symlink_to(old_target)
            if not old_link and link.is_symlink():link.unlink()
            if created_file and not old_file:file.unlink()
        except OSError as error:rollback_errors.append(str(error))
        for argv in (['nginx','-t'],['systemctl','reload','nginx']):
            outcome=runner.run(argv)
            if outcome.returncode:rollback_errors.append(' '.join(argv)+': '+outcome.stderr)
        state.record('nginx-'+change.action,change.name,{'ok':False,'error':str(exc),'rollback_errors':rollback_errors,'backup':str(backup)})
        return OperationResult(False,str(exc)+('; откат с ошибками: '+str(rollback_errors) if rollback_errors else '; изменения отменены'),bool(rollback_errors))

def rollback_site_change(backup_id:str,runner,state,available_dir:Path,enabled_dir:Path,dry_run:bool=False)->OperationResult:
    if not re.fullmatch(r'[a-zA-Z0-9._-]+\.json',backup_id) or '..' in backup_id:
        return OperationResult(False,'Неверное имя резервной копии')
    source=state.root/'backups'/backup_id
    if source.is_symlink() or not source.is_file():return OperationResult(False,'Резервная копия отсутствует')
    try:
        backup=json.loads(source.read_text())
        name=backup['name']; target=Path(available_dir)/name; link=Path(enabled_dir)/name
        if not _NAME.fullmatch(name) or target.is_symlink() or (link.is_symlink() and link.resolve()!=target.resolve()):
            return OperationResult(False,'Небезопасный объект Nginx')
        if link.exists() and not link.is_symlink():return OperationResult(False,'Конфликт в sites-enabled')
        if not Path(available_dir).is_dir() or not Path(enabled_dir).is_dir():return OperationResult(False,'Нет каталогов')
        previous_content=target.read_text() if target.is_file() else None
        previous_link=os.readlink(link) if link.is_symlink() else None
        if dry_run:return OperationResult(True,'DRY RUN: восстановить '+name)
        try:
            if backup.get('file_exists'):
                if target.exists() and previous_content!=backup['content']:
                    raise RuntimeError('Текущий файл отличается от резервной копии; восстановление автоматически заблокировано')
                if not target.exists():
                    fd,tmp=tempfile.mkstemp(dir=available_dir,prefix='.restore-')
                    try:
                        with os.fdopen(fd,'w') as f:f.write(backup['content'])
                        os.link(tmp,target)
                    finally:
                        if os.path.exists(tmp):os.unlink(tmp)
            elif target.exists():
                raise RuntimeError('Файл появился после сохранения: ручное вмешательство необходимо')
            if link.is_symlink():link.unlink()
            if backup.get('enabled'):
                saved_target=backup.get('symlink_target')
                if saved_target is None:raise RuntimeError('Нет цели ссылки в резервной копии')
                link.symlink_to(saved_target)
            check=runner.run(['nginx','-t'])
            if check.returncode:raise RuntimeError('nginx -t: '+check.stderr)
            reload=runner.run(['systemctl','reload','nginx'])
            if reload.returncode:raise RuntimeError('reload: '+reload.stderr)
            state.record('nginx-manual-restore',name,{'ok':True,'backup':backup_id})
            return OperationResult(True,'Резервная копия восстановлена')
        except (OSError,RuntimeError) as exc:
            try:
                if link.is_symlink():link.unlink()
                if previous_link is not None:link.symlink_to(previous_link)
                if previous_content is None and target.is_file():target.unlink()
            except OSError:return OperationResult(False,'Ошибка и откат не завершён: '+str(exc),True)
            runner.run(['nginx','-t']);runner.run(['systemctl','reload','nginx'])
            return OperationResult(False,'Восстановление отменено: '+str(exc))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return OperationResult(False,'Некорректная резервная копия: '+str(exc))

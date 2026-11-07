import os
import pwd
import re
from pathlib import Path
from .models import OperationResult

_SAFE=re.compile(r'^[a-z0-9][a-z0-9_-]{0,50}$')

def list_units(runner):
    r=runner.run(['systemctl','list-unit-files','--type=service','--no-pager','--no-legend'])
    if r.returncode:raise RuntimeError(r.stderr)
    return [(line.split()[0],line.split()[1]) for line in r.stdout.splitlines() if len(line.split())>=2]

def _escape_exec(arg):
    if any(c in arg for c in ('\n','\r','\0')):raise ValueError('Недопустимый аргумент')
    # systemd percent-specifiers must be escaped even inside quoted args.
    value=arg.replace('%','%%').replace('\\','\\\\').replace('"','\\"')
    return '"'+value+'"'

def render_managed_unit(name:str,user:str,workdir:Path,argv:tuple[str,...],env_file:Path|None=None)->str:
    if not _SAFE.fullmatch(name):raise ValueError('Недопустимое имя')
    try:
        account=pwd.getpwnam(user)
        if account.pw_uid==0:raise ValueError('Нельзя запускать новое приложение от root')
    except KeyError:raise ValueError('Пользователь не найден') from None
    workdir=Path(workdir)
    if not workdir.is_absolute() or not workdir.is_dir():raise ValueError('Рабочая директория не существует')
    if not argv or not Path(argv[0]).is_absolute():raise ValueError('Исполняемый файл должен иметь абсолютный путь')
    if any('\n' in x or '\r' in x or '\0' in x for x in argv):raise ValueError('Опасный аргумент')
    env=''
    if env_file is not None:
        if not Path(env_file).is_absolute() or not Path(env_file).is_file():raise ValueError('Неверный env-файл')
        env='EnvironmentFile='+_escape_exec(str(env_file))+'\n'
    return ('[Unit]\nDescription=Server Manager: '+name+'\nAfter=network.target\n\n'
            '[Service]\nType=simple\nUser='+user+'\nWorkingDirectory='+_escape_exec(str(workdir))+'\n'
            +env+'ExecStart='+' '.join(_escape_exec(a) for a in argv)+'\nRestart=on-failure\n\n'
            '[Install]\nWantedBy=multi-user.target\n')

def create_managed_unit(name,user,workdir,argv,runner,directory=Path('/etc/systemd/system'),env_file=None,dry_run=False)->OperationResult:
    try:content=render_managed_unit(name,user,Path(workdir),tuple(argv),env_file)
    except ValueError as exc:return OperationResult(False,str(exc))
    target=Path(directory)/('server-manager-'+name+'.service')
    if target.exists() or target.is_symlink():return OperationResult(False,'Unit уже существует')
    if dry_run:return OperationResult(True,content)
    try:
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
        with os.fdopen(fd,'w') as file:file.write(content)
        r=runner.run(['systemctl','daemon-reload'])
        if r.returncode:
            target.unlink()
            return OperationResult(False,'daemon-reload: '+r.stderr)
        return OperationResult(True,'Создан '+str(target))
    except OSError as exc:return OperationResult(False,str(exc))

def plan_systemd_action(unit:str,verb:str):
    if not re.fullmatch(r'[a-zA-Z0-9_.@-]+\.service',unit) or verb not in ('start','stop','enable','disable'):
        raise ValueError('Недопустимая операция')
    return (unit,verb)

def apply_systemd_action(action,runner,state=None,dry_run=False)->OperationResult:
    unit,verb=action
    cmd=['systemctl',verb,unit]
    if dry_run:return OperationResult(True,'DRY RUN: '+' '.join(cmd))
    r=runner.run(cmd,timeout=120)
    if state:state.record('systemd-'+verb,unit,{'ok':r.returncode==0,'stderr':r.stderr})
    return OperationResult(r.returncode==0,r.stderr or r.stdout or 'Операция завершена')

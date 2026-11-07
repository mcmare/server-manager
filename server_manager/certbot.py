import shutil
from dataclasses import dataclass
from pathlib import Path
from .models import OperationResult,Site
from .nginx import valid_domain

@dataclass(frozen=True)
class CertPlan:
    site: Site
    domains: tuple[str,...]

def plan_certificate(site:Site,runner)->CertPlan:
    if not site.enabled or site.ambiguous or not site.domains or any(not valid_domain(d) or d.startswith('*.') for d in site.domains):
        raise ValueError('Нужен включённый сайт с корректными DNS-доменами')
    if not site.path.is_file():raise ValueError('Конфигурация отсутствует')
    return CertPlan(site,site.domains)

def list_certificates(runner):
    return runner.run(['certbot','certificates'],timeout=60)

def run_certificate(plan:CertPlan,runner,state=None,dry_run=False,backup_dir=Path('/var/lib/server-manager/backups'))->OperationResult:
    cmd=['certbot','--nginx']
    for name in plan.domains:cmd+=['-d',name]
    if dry_run:return OperationResult(True,'DRY RUN: '+' '.join(cmd))
    chk=runner.run(['nginx','-t'])
    if chk.returncode:return OperationResult(False,'Ошибка nginx -t: '+chk.stderr)
    import datetime
    backup_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    backup=backup_dir/(plan.site.name+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S%f')+'.conf')
    shutil.copy2(plan.site.path,backup)
    backup.chmod(0o600)
    res=runner.run(cmd,timeout=600)
    check=runner.run(['nginx','-t'])
    if state:state.record('certbot',plan.site.name,{'exit_code':res.returncode,'backup':str(backup),'nginx_test':check.returncode})
    return OperationResult(res.returncode==0 and check.returncode==0, res.stderr or res.stdout or 'Готово',res.returncode!=0 or check.returncode!=0)

def renewal_dry_run(runner):
    return runner.run(['certbot','renew','--dry-run'],timeout=900)

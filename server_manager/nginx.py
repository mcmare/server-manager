import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from .models import Site

_DOMAIN=re.compile(r'^(?=.{1,253}$)(?:\*\.)?[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$')

@dataclass(frozen=True)
class SiteChange:
    action: str
    name: str
    available_dir: Path
    enabled_dir: Path
    content: str|None = None

def valid_domain(domain):
    return bool(_DOMAIN.fullmatch(domain)) and '\n' not in domain

def _extract(contents):
    clean=re.sub(r'#.*$', '', contents, flags=re.M)
    domains=[]; endpoints=[]
    for match in re.finditer(r'\b(server_name|proxy_pass)\s+([^;{}]+);', clean):
        kind,value=match.groups()
        if kind=='server_name': domains.extend(v for v in value.split() if v!='_')
        else: endpoints.append(value.strip())
    server_count=len(re.findall(r'\bserver\s*\{',clean))
    domains=list(dict.fromkeys(domains))
    endpoints=list(dict.fromkeys(endpoints))
    ambiguous=(server_count<1 or len(endpoints)!=1 or any('$' in e or not e.startswith(('http://','https://')) for e in endpoints))
    return tuple(domains),tuple(endpoints),ambiguous

def list_sites(available_dir:Path=Path('/etc/nginx/sites-available'), enabled_dir:Path=Path('/etc/nginx/sites-enabled')) -> list[Site]:
    available_dir=Path(available_dir); enabled_dir=Path(enabled_dir)
    output=[]
    if not available_dir.is_dir(): return []
    for path in sorted(available_dir.iterdir()):
        if not path.is_file() or path.is_symlink(): continue
        try:
            domains,endpoints,ambiguous=_extract(path.read_text())
            linked=enabled_dir/path.name
            enabled=linked.is_symlink() and linked.resolve()==path.resolve()
            output.append(Site(path.name,path,enabled,domains,endpoints,ambiguous))
        except (OSError,UnicodeError):
            output.append(Site(path.name,path,False,ambiguous=True,reason='Не удалось прочитать конфигурацию'))
    if enabled_dir.is_dir():
        for link in enabled_dir.iterdir():
            if not link.is_symlink(): continue
            if any(s.name==link.name and s.enabled for s in output): continue
            output.append(Site(link.name,link,True,ambiguous=True,reason='Неподконтрольная или опасная символическая ссылка'))
    return sorted(output,key=lambda s:s.name)

def render_proxy_site(domains:tuple[str,...],address:str,port:int)->str:
    if not domains or any(not valid_domain(d) for d in domains): raise ValueError('Неверный домен')
    try: ipaddress.ip_address(address)
    except ValueError: raise ValueError('Адрес backend должен быть IP') from None
    if not 1<=port<=65535: raise ValueError('Неверный порт')
    dest=f'[{address}]' if ':' in address else address
    return ('server {\n    listen 80;\n    server_name '+ ' '.join(domains) +
            ';\n    location / {\n        proxy_pass http://'+dest+':'+str(port)+';\n'
            '        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n'
            '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
            '        proxy_set_header X-Forwarded-Proto $scheme;\n    }\n}\n')

def plan_site_change(action: str,site:Site,available_dir=None,enabled_dir=None) -> SiteChange:
    if action not in ('enable','disable'): raise ValueError('Недопустимое действие')
    if site.ambiguous and (site.path.is_symlink() or not site.path.is_file()): raise ValueError('Небезопасная ссылка')
    return SiteChange(action,site.name,Path(available_dir or site.path.parent),Path(enabled_dir or '/etc/nginx/sites-enabled'))

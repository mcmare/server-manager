from dataclasses import dataclass
from .compose import all_containers

@dataclass(frozen=True)
class BackendOwner:
    kind: str
    name: str
    confidence: str
    container_id: str = ''

def find_backend_owners(endpoint:str,runner)->list[BackendOwner]:
    try:
        addr,port=endpoint.rsplit(':',1)
        port=int(port)
    except (ValueError,AttributeError):return []
    if addr not in ('127.0.0.1','::1','localhost') or not 1<=port<=65535:return []
    try:objects=all_containers(runner)
    except (RuntimeError,ValueError,KeyError):return []
    found=[]
    for obj in objects:
        labels=((obj.get('Config') or {}).get('Labels') or {})
        project=labels.get('com.docker.compose.project')
        service=labels.get('com.docker.compose.service')
        if not project or not service:continue
        bindings=((obj.get('NetworkSettings') or {}).get('Ports') or {})
        for ports in bindings.values():
            for binding in ports or []:
                if binding.get('HostPort')==str(port) and binding.get('HostIp') in ('127.0.0.1','::1'):
                    found.append(BackendOwner('compose',project+'/'+service,'confirmed',obj.get('Id','')))
    return found

from dataclasses import dataclass
import re
from urllib.parse import urlsplit
from .compose import all_containers

@dataclass(frozen=True)
class BackendOwner:
    kind: str
    name: str
    confidence: str
    container_id: str = ''


def find_backend_owners(endpoint: str, runner) -> list[BackendOwner]:
    """Identify local Compose publishing or systemd listener; never guess when uncertain."""
    try:
        url = urlsplit(endpoint if '://' in endpoint else 'http://' + endpoint)
        addr, port = url.hostname, url.port
    except (ValueError, TypeError, AttributeError):
        return []
    if addr not in ('127.0.0.1', '::1', 'localhost') or not port or not 1 <= port <= 65535:
        return []
    found = []
    try:
        objects = all_containers(runner)
    except (RuntimeError, ValueError, KeyError):
        objects = []
    for obj in objects:
        labels = ((obj.get('Config') or {}).get('Labels') or {})
        project = labels.get('com.docker.compose.project')
        service = labels.get('com.docker.compose.service')
        if not project or not service:
            continue
        bindings = ((obj.get('NetworkSettings') or {}).get('Ports') or {})
        for bindings_for_port in bindings.values():
            if any(binding.get('HostPort') == str(port) and
                   binding.get('HostIp') in ('127.0.0.1', '::1', '0.0.0.0', '::', '')
                   for binding in (bindings_for_port or [])):
                found.append(BackendOwner('compose', project + '/' + service,
                                          'confirmed', obj.get('Id', '')))
                break
    if found:
        return found
    # systemd PID attribution via systemctl status PID. Failure is not an ownership claim.
    try:
        listeners = runner.run(['ss', '-H', '-lntp'])
        if listeners.returncode:
            return []
        pids = set()
        for line in listeners.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[3] if parts[0] == 'LISTEN' else parts[4] if len(parts) > 4 else ''
            if not local.endswith(':' + str(port)):
                continue
            pids.update(re.findall(r'pid=(\d+)', line))
        for pid in pids:
            result = runner.run(['systemctl', 'status', pid, '--no-pager', '--plain'])
            match = re.search(r'^\s*[●*]?\s*([\w@.-]+\.service)\b', result.stdout, re.M)
            if match and match.group(1) not in ('nginx.service', 'docker.service', 'ssh.service', 'sshd.service', 'systemd.service'):
                found.append(BackendOwner('systemd', match.group(1), 'confirmed'))
    except (RuntimeError, ValueError, OSError):
        return []
    return list({(o.kind, o.name): o for o in found}.values())

import re
from .models import Listener

_USER=re.compile(r'"([^"\n]+)",pid=(\d+)')

def parse_ss(output: str) -> list[Listener]:
    rows=[]
    for line in output.splitlines():
        parts=line.split(maxsplit=6)
        if len(parts)<5 or parts[0] not in ('tcp','udp'): continue
        protocol,state=parts[0],parts[1]
        if protocol=='tcp' and state!='LISTEN': continue
        if protocol=='udp' and state not in ('UNCONN','LISTEN'): continue
        local=parts[4]
        try:
            addr,port=local.rsplit(':',1)
            if not port.isdigit(): continue
            owners=_USER.findall(parts[6] if len(parts)>6 else '')
            if owners:
                for process,pid in owners: rows.append(Listener(protocol,addr,int(port),process,pid))
            else: rows.append(Listener(protocol,addr,int(port)))
        except ValueError: continue
    return sorted(rows,key=lambda x:(x.port,x.protocol,x.bind_address,x.pid))

def list_listeners(runner, protocol: str|None=None) -> list[Listener]:
    out=runner.run(['ss','-H','-lntup'])
    if out.returncode: raise RuntimeError(out.stderr)
    rows=parse_ss(out.stdout)
    return [r for r in rows if r.protocol==protocol] if protocol else rows

def _compact_values(values: list[str], width: int) -> str:
    unique = list(dict.fromkeys(values))
    joined = ','.join(unique)
    if len(joined) <= width:
        return joined
    if len(unique) == 1:
        return joined
    suffix = f' (+{len(unique) - 1})'
    return unique[0][:max(1, width - len(suffix))] + suffix


def format_listeners(listeners: list[Listener]) -> str:
    """Display each (protocol, port) once, preserving addresses and process owners."""
    grouped: dict[tuple[str, int], list[Listener]] = {}
    for listener in listeners:
        grouped.setdefault((listener.protocol, listener.port), []).append(listener)

    lines = [f'{"PROTO":<6} {"PORT":>5} {"ADDRESS":<28} {"PID":<17} {"PROCESS":<24} SOCKETS']
    for (protocol, port), entries in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        addresses = _compact_values([entry.bind_address for entry in entries], 28)
        pids = _compact_values([entry.pid for entry in entries], 17)
        processes = _compact_values([entry.process for entry in entries], 24)
        lines.append(f'{protocol:<6} {port:>5} {addresses:<28} {pids:<17} {processes:<24} {len(entries):>7}')
    return '\n'.join(lines)

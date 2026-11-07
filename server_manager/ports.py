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

def format_listeners(listeners: list[Listener]) -> str:
    lines=[f'{"PROTO":<6} {"ADDRESS":<28} {"PORT":>5} {"PID":>8} {"PROCESS":<22} OWNER']
    for r in listeners:
        lines.append(f'{r.protocol:<6} {r.bind_address:<28.28} {r.port:>5} {r.pid:>8} {r.process:<22.22} {r.owner_hint}')
    return '\n'.join(lines)

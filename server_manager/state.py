import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_SECRET = ('password','secret','token','key','credential','environment','private')

def redact(obj):
    if isinstance(obj, dict):
        return {str(k): ('[REDACTED]' if any(s in str(k).lower() for s in _SECRET) else redact(v)) for k,v in obj.items()}
    if isinstance(obj, list): return [redact(x) for x in obj]
    return obj

class StateStore:
    def __init__(self, root: Path = Path('/var/lib/server-manager')):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def save_state(self, data: dict):
        lock = self.root / '.lock'
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            tmpfd, tmpname = tempfile.mkstemp(dir=self.root, prefix='.state-')
            try:
                with os.fdopen(tmpfd,'w') as f:
                    json.dump(data, f, ensure_ascii=False)
                    f.flush(); os.fsync(f.fileno())
                os.chmod(tmpname,0o600)
                os.replace(tmpname,self.root/'state.json')
            finally:
                if os.path.exists(tmpname): os.unlink(tmpname)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def load_state(self):
        try: return json.loads((self.root/'state.json').read_text())
        except FileNotFoundError: return {}

    def record(self, action: str, target: str, result: dict):
        entry = {'timestamp':datetime.now(timezone.utc).isoformat(),'uid':os.getuid(),
                 'action':action,'target':target,'result':redact(result)}
        fd=os.open(self.root/'audit.jsonl',os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        try:
            with os.fdopen(fd,'a') as f:
                f.write(json.dumps(entry, ensure_ascii=False)+'\n')
        except BaseException:
            raise

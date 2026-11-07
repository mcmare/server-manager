from dataclasses import dataclass, field
from pathlib import Path

@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ''
    stderr: str = ''

@dataclass(frozen=True)
class OperationResult:
    ok: bool
    message: str
    partial: bool = False

@dataclass(frozen=True)
class Site:
    name: str
    path: Path
    enabled: bool
    domains: tuple[str, ...] = ()
    endpoints: tuple[str, ...] = ()
    ambiguous: bool = False
    reason: str = ''

@dataclass(frozen=True)
class Listener:
    protocol: str
    bind_address: str
    port: int
    process: str = '?'
    pid: str = '?'
    owner_hint: str = '?'

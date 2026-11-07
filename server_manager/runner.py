import subprocess
from .models import CommandResult

class CommandRunner:
    def run(self, argv: list[str], timeout: int = 30, cwd=None) -> CommandResult:
        if not argv or not all(isinstance(part, str) for part in argv):
            raise ValueError('argv must be a list of strings')
        try:
            result = subprocess.run(argv, shell=False, capture_output=True, text=True,
                                    timeout=timeout, cwd=cwd, check=False)
            return CommandResult(result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired:
            return CommandResult(124, '', f'Команда превысила таймаут {timeout} с')
        except OSError as exc:
            return CommandResult(127, '', str(exc))

import io,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from server_manager.cli import main
from server_manager.models import CommandResult
class Fake:
 def __init__(self):self.calls=[]
 def run(self,argv,**kw):
  self.calls.append(argv)
  if argv[0]=='ss':return CommandResult(0,'tcp LISTEN 0 10 127.0.0.1:8805 0.0.0.0:* users:(("python",pid=88,fd=7))')
  return CommandResult(0,'','')
class CLITests(unittest.TestCase):
 def test_ports(self):
  output=io.StringIO();runner=Fake()
  with patch('sys.stdout',output):self.assertEqual(main(['ports'],runner=runner),0)
  self.assertIn('8805',output.getvalue())
 def test_no_unknown_backend_stop(self):
  from server_manager.discovery import find_backend_owners
  self.assertEqual(find_backend_owners('127.0.0.1:8080',Fake()),[])

class DryRunTests(unittest.TestCase):
 def test_menu_dry_run_does_not_create_state_folder(self):
  runner=Fake()
  with tempfile.TemporaryDirectory() as d:
   from pathlib import Path
   with patch('server_manager.cli.StateStore',side_effect=AssertionError('StateStore must not initialize')):
    with patch('builtins.input',return_value='0'):
     with patch('sys.stdout',io.StringIO()):
      self.assertEqual(main(['--dry-run'],runner=runner),0)

class RestoreMenuTests(unittest.TestCase):
 def test_restore_menu_not_invoked_without_confirmation(self):
  from server_manager.cli import _restore_backup
  from server_manager.state import StateStore
  with tempfile.TemporaryDirectory() as d:
   state=StateStore(Path(d))
   with patch('builtins.input',return_value='0'):
    with patch('sys.stdout',io.StringIO()):
     with patch('server_manager.cli.rollback_site_change',side_effect=AssertionError('should not restore')):
      _restore_backup(Fake(),state,False)

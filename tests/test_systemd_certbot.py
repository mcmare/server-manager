import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from server_manager.systemd import render_managed_unit,create_managed_unit
from server_manager.certbot import plan_certificate
from server_manager.models import Site,CommandResult

class Fake:
 def __init__(self):self.calls=[]
 def run(self,argv,**kw):self.calls.append(argv);return CommandResult(0,'','')

class ServiceTests(unittest.TestCase):
 def test_reject_root_user(self):
  with self.assertRaises(ValueError):render_managed_unit('site','root',Path('/tmp'),('/usr/bin/python3','app.py'))
 def test_reject_shell_injection_unit_name(self):
  with self.assertRaises(ValueError):render_managed_unit('a;rm','nobody',Path('/tmp'),('/bin/sleep','5'))
 def test_create_does_not_overwrite(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);name='server-manager-demo.service';(root/name).write_text('original')
   r=create_managed_unit('demo','nobody',root,('/bin/sleep','5'),Fake(),directory=root)
   self.assertFalse(r.ok);self.assertEqual((root/name).read_text(),'original')
 def test_certbot_does_not_run_for_disabled_site(self):
  with self.assertRaises(ValueError):plan_certificate(Site('site',Path('/tmp/site'),False,('app.example.com',)),Fake())

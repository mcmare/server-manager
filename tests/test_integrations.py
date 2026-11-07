import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from server_manager.models import CommandResult
from server_manager.nginx import SiteChange
from server_manager.transactions import apply_site_change
from server_manager.state import StateStore
from server_manager.discovery import find_backend_owners
from server_manager.cleanup import inspect_cleanup
from server_manager.compose import ComposeProject
from tests.test_compose_cleanup import Fake,record
class IntegrationTests(unittest.TestCase):
 def test_nginx_persistent_backup(self):
  class Runner:
   def run(self,*a,**kw):return CommandResult(0,'','')
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);a=root/'a';e=root/'e';a.mkdir();e.mkdir()
   (a/'site').write_text('server { listen 80; }');(e/'site').symlink_to(a/'site')
   state=StateStore(root/'state')
   r=apply_site_change(SiteChange('disable','site',a,e),Runner(),state)
   self.assertTrue(r.ok)
   self.assertTrue(list((state.root/'backups').glob('*.json')))
 def test_cleanup_image_recoverable_manifest(self):
  fake=Fake({'id1':record('id1')},images={'sha256:abc':{'RepoTags':['example/repo:1']}})
  old_run=fake.run
  def run(argv,**kw):
   if argv[:3]==['docker','manifest','inspect']:return CommandResult(0,'{"schemaVersion":2}')
   return old_run(argv,**kw)
  fake.run=run
  plan=inspect_cleanup(ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',)),fake)
  self.assertEqual(plan.image_candidates,('sha256:abc',))
 def test_unknown_owner_not_fabricated(self):
  class Empty:
   def run(self,argv,**kw):return CommandResult(1,'','denied')
  self.assertEqual(find_backend_owners('127.0.0.1:8805',Empty()),[])

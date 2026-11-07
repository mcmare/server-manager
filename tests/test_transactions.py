import tempfile,unittest
from pathlib import Path
from server_manager.nginx import SiteChange
from server_manager.transactions import apply_site_change
from server_manager.models import CommandResult
from server_manager.state import StateStore

class Fake:
    def __init__(self, codes):self.codes=list(codes); self.calls=[]
    def run(self,argv,**kw):
        self.calls.append(argv); code=self.codes.pop(0) if self.codes else 0
        return CommandResult(code,'','failure' if code else '')

class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name);self.a=base/'a';self.e=base/'e';self.a.mkdir();self.e.mkdir();self.state=StateStore(base/'state')
        (self.a/'site').write_text('server { listen 80; server_name site.example; }')
    def test_enable(self):
        r=apply_site_change(SiteChange('enable','site',self.a,self.e),Fake([]),self.state)
        self.assertTrue(r.ok);self.assertTrue((self.e/'site').is_symlink())
    def test_reload_failure_rolls_back(self):
        r=apply_site_change(SiteChange('enable','site',self.a,self.e),Fake([0,1,0,0]),self.state)
        self.assertFalse(r.ok);self.assertFalse((self.e/'site').exists())
    def test_dry_run(self):
        fake=Fake([])
        r=apply_site_change(SiteChange('enable','site',self.a,self.e),fake,self.state,dry_run=True)
        self.assertTrue(r.ok);self.assertFalse(fake.calls);self.assertFalse((self.e/'site').exists())
    def test_unsafe_symlink(self):
        (self.e/'site').symlink_to('/etc/passwd')
        r=apply_site_change(SiteChange('disable','site',self.a,self.e),Fake([]),self.state)
        self.assertFalse(r.ok);self.assertTrue((self.e/'site').is_symlink())

class ExactLinkRollback(unittest.TestCase):
    def test_restores_relative_symlink_after_failed_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'a';e=root/'e';a.mkdir();e.mkdir()
            (a/'site').write_text('server { listen 80; server_name abc.example; }')
            (e/'site').symlink_to('../a/site')
            fake=Fake([0,1,0,0]);state=StateStore(root/'state')
            result=apply_site_change(SiteChange('disable','site',a,e),fake,state)
            self.assertFalse(result.ok)
            self.assertEqual((e/'site').readlink(),Path('../a/site'))

class ManualRestore(unittest.TestCase):
    def test_restore_backup_recreates_prior_link(self):
        from server_manager.transactions import rollback_site_change
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'a';e=root/'e';a.mkdir();e.mkdir()
            (a/'site').write_text('server { listen 80; }');(e/'site').symlink_to('../a/site')
            state=StateStore(root/'state');fake=Fake([])
            result=apply_site_change(SiteChange('disable','site',a,e),fake,state)
            self.assertTrue(result.ok)
            backup=next((state.root/'backups').glob('*.json'))
            result=rollback_site_change(backup.name,fake,state,a,e)
            self.assertTrue(result.ok,result.message)
            self.assertEqual((e/'site').readlink(),Path('../a/site'))
    def test_restore_rejects_traversal_backup_name(self):
        from server_manager.transactions import rollback_site_change
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=StateStore(root/'state');a=root/'a';e=root/'e';a.mkdir();e.mkdir()
            result=rollback_site_change('../bad',Fake([]),state,a,e)
            self.assertFalse(result.ok)

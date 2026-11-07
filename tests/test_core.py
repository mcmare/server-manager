import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from server_manager.runner import CommandRunner
from server_manager.state import StateStore
from server_manager.nginx import list_sites, render_proxy_site
from server_manager.ports import parse_ss, format_listeners

class CoreTests(unittest.TestCase):
    def test_runner_no_shell(self):
        with patch('subprocess.run') as p:
            p.return_value.returncode=0; p.return_value.stdout='ok'; p.return_value.stderr=''
            self.assertEqual(CommandRunner().run(['echo','hello']).stdout,'ok')
            self.assertIs(p.call_args.kwargs['shell'],False)
    def test_state_redacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=StateStore(Path(tmp)); s.record('test','site',{'token':'very-secret','message':'ok'})
            self.assertNotIn('very-secret',(Path(tmp)/'audit.jsonl').read_text())
            s.save_state({'x':1}); self.assertEqual((Path(tmp)/'state.json').stat().st_mode & 0o777,0o600)
    def test_nginx_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); a=root/'available'; e=root/'enabled'; a.mkdir(); e.mkdir()
            (a/'ok').write_text('server { listen 80; server_name foo.test; proxy_pass http://127.0.0.1:4000; }')
            (e/'ok').symlink_to(a/'ok'); (e/'evil').symlink_to('/etc/passwd')
            sites=list_sites(a,e)
            self.assertEqual(len(sites),2)
            self.assertTrue(next(s for s in sites if s.name=='evil').ambiguous)
    def test_proxy_validation(self):
        with self.assertRaises(ValueError): render_proxy_site(('evil.test; return 200',),'127.0.0.1',8080)
    def test_ports(self):
        rows=parse_ss('tcp LISTEN 0 128 127.0.0.1:8080 0.0.0.0:* users:(("python3",pid=33,fd=4))\nudp UNCONN 0 0 [::]:53 [::]:*')
        self.assertEqual(rows[0].port,53)
        self.assertIn('8080',format_listeners(rows))

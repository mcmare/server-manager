import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from server_manager.models import CommandResult, Site
from server_manager.discovery import find_backend_owners, BackendOwner
from server_manager.nginx import _extract
from server_manager.ui import paint, site_status
from server_manager.cli import _site_action, menu


class Runner:
    def __init__(self): self.calls=[]
    def run(self, argv, **kw):
        self.calls.append(argv)
        if argv[:2] == ['docker', 'ps']:
            return CommandResult(0, 'id1\n')
        if argv[:2] == ['docker', 'inspect']:
            return CommandResult(0, '''[{"Id":"id1","Config":{"Labels":{"com.docker.compose.project":"bb","com.docker.compose.service":"web","com.docker.compose.project.working_dir":"/srv/bb","com.docker.compose.project.config_files":"/srv/bb/compose.yaml"}},"NetworkSettings":{"Ports":{"8000/tcp":[{"HostIp":"0.0.0.0","HostPort":"8805"}]}},"HostConfig":{"RestartPolicy":{"Name":"always"}}}]''')
        return CommandResult(0, '', '')


class DashboardTests(unittest.TestCase):
    def test_compose_owner_detected_from_wildcard_binding(self):
        owners = find_backend_owners('http://127.0.0.1:8805/', Runner())
        self.assertEqual([(o.kind, o.name) for o in owners], [('compose', 'bb/web')])

    def test_nginx_certbot_two_server_blocks_one_backend(self):
        config = '''server { listen 80; server_name bb.mcmare.ru; return 301 https://$host$request_uri; }
server { listen 443 ssl; server_name bb.mcmare.ru; location / { proxy_pass http://127.0.0.1:8805; } }'''
        domains, endpoints, ambiguous = _extract(config)
        self.assertEqual(domains, ('bb.mcmare.ru',))
        self.assertEqual(endpoints, ('http://127.0.0.1:8805',))
        self.assertFalse(ambiguous)

    def test_no_color_for_redirected_output(self):
        self.assertNotIn('\x1b', paint('hello', 'green', color_enabled=False))
        self.assertIn('●', site_status(True))

    def test_selected_site_shows_backend_and_can_stop_compose(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); available=root/'a'; enabled=root/'e'; available.mkdir(); enabled.mkdir()
            config=available/'bb'; config.write_text('server { server_name bb.mcmare.ru; proxy_pass http://127.0.0.1:8805; }')
            (enabled/'bb').symlink_to(config)
            runner=Runner()
            inputs=iter(['1','2','y','y','y'])
            with patch('server_manager.cli.AVAILABLE',available), patch('server_manager.cli.ENABLED',enabled), \
                patch('server_manager.cli._ensure_root',return_value=True), \
                patch('server_manager.cli.input',side_effect=lambda *a: next(inputs)), \
                patch('server_manager.cli.Path.is_file', return_value=True), \
                patch('server_manager.cli.apply_compose_action') as stop, \
                patch('server_manager.cli.apply_site_change') as change, \
                patch('server_manager.cli.apply_restart_override') as restart, \
                patch('server_manager.cli.plan_restart_override',return_value={'id1':'always'}), \
                patch('sys.stdout',new_callable=io.StringIO) as output:
                from server_manager.models import OperationResult
                change.return_value=OperationResult(True,'ok')
                stop.return_value=OperationResult(True,'ok')
                _site_action('disable',runner,None,False)
            self.assertTrue(change.called)
            self.assertTrue(stop.called)
            self.assertTrue(restart.called)
            self.assertIn('bb/web',output.getvalue())

    def test_menu_prioritizes_site_dashboard(self):
        with patch('server_manager.cli.input',side_effect=['0']),patch('sys.stdout',new_callable=io.StringIO) as out:
            menu(Runner(),None,True)
        self.assertIn('Управление сайтами',out.getvalue())
        self.assertNotIn('3. Отключить сайт',out.getvalue())

if __name__=='__main__': unittest.main()

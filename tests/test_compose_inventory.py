import json
import unittest

from server_manager.compose import discover_compose, project_resources, project_status
from server_manager.models import CommandResult


class Runner:
    def run(self, argv, **kw):
        if argv[:3] == ['docker', 'ps', '-aq']:
            return CommandResult(0, 'c1\nc2')
        if argv[:2] == ['docker', 'inspect']:
            containers = []
            for cid in argv[2:]:
                containers.append({
                    'Id': cid, 'Image': 'sha256:111' if cid == 'c1' else 'sha256:222',
                    'State': {'Status': 'running' if cid == 'c1' else 'exited'},
                    'Config': {'Labels': {
                        'com.docker.compose.project': 'demo',
                        'com.docker.compose.service': 'web' if cid == 'c1' else 'db',
                        'com.docker.compose.project.working_dir': '/work',
                        'com.docker.compose.project.config_files': '/work/compose.yml'
                    }}
                })
            return CommandResult(0, json.dumps(containers))
        if argv == ['docker', 'image', 'ls', '-q', '--no-trunc']:
            return CommandResult(0, 'sha256:111\nsha256:333')
        raise AssertionError(argv)


class InventoryTests(unittest.TestCase):
    def test_status_and_available_images(self):
        runner = Runner()
        project = discover_compose(runner)[0]
        self.assertEqual(project_status(project), 'Частично (1/2)')
        self.assertEqual(project_resources(project, runner), (1, 2, 2))

    def test_failed_image_listing_is_not_mistaken_for_absent_images(self):
        class Failing(Runner):
            def run(self, argv, **kw):
                if argv == ['docker', 'image', 'ls', '-q', '--no-trunc']:
                    return CommandResult(1, '', 'denied')
                return super().run(argv, **kw)
        runner = Failing()
        self.assertEqual(project_resources(discover_compose(runner)[0], runner), (None, 2, 2))

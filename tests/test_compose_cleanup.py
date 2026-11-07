import json,unittest
from server_manager.models import CommandResult
from server_manager.compose import discover_compose, ComposeProject, apply_compose_action, plan_compose_action
from server_manager.cleanup import inspect_cleanup, apply_cleanup

class Fake:
    def __init__(self, containers, images=None):
        self.containers=containers;self.images=images or {};self.calls=[]
    def run(self,argv,**kwargs):
        self.calls.append(argv)
        if argv[:3]==['docker','ps','-aq']:return CommandResult(0,'\n'.join(self.containers))
        if argv[:2]==['docker','inspect']:
            ids=argv[2:]
            if any(i not in self.containers for i in ids):return CommandResult(1,'','gone')
            return CommandResult(0,json.dumps([self.containers[i] for i in ids]))
        if argv[:3]==['docker','image','inspect']:
            return CommandResult(0,json.dumps([self.images[argv[3]]])) if argv[3] in self.images else CommandResult(1,'','unknown')
        return CommandResult(0,'','')

def record(cid, project='alpha', image='sha256:abc', mounts=None):
    return {'Id':cid,'Image':image,'Config':{'Image':'repo/test:latest','Labels':{'com.docker.compose.project':project,'com.docker.compose.service':'web','com.docker.compose.project.working_dir':'/work','com.docker.compose.project.config_files':'/work/compose.yaml'}},'HostConfig':{'RestartPolicy':{'Name':'unless-stopped'}},'Mounts':mounts if mounts is not None else [{'Type':'bind','Source':'/work/data','Destination':'/data'}],'NetworkSettings':{'Ports':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'8805'}]}}}

class ComposeTests(unittest.TestCase):
    def test_discover(self):
        fake=Fake({'id1':record('id1')})
        ps=discover_compose(fake)
        self.assertEqual(ps[0].name,'alpha');self.assertIn('web',ps[0].services)
    def test_only_compose_command(self):
        project=ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',))
        fake=Fake({'id1':record('id1')})
        from unittest.mock import patch
        with patch('pathlib.Path.is_file',return_value=True):
            res=apply_compose_action(plan_compose_action(project,('web',),'stop'),fake,None)
        self.assertTrue(res.ok);self.assertIn('stop',fake.calls[-1]);self.assertNotIn('down',fake.calls[-1])
    def test_cleanup_anon_blocked(self):
        fake=Fake({'id1':record('id1',mounts=[{'Type':'volume','Name':'random-long','Destination':'/storage'}])})
        plan=inspect_cleanup(ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',)),fake)
        self.assertTrue(plan.blockers)
        start=len(fake.calls)
        res=apply_cleanup(plan,fake,None,confirmed=True,accept_writable_layer=True)
        self.assertFalse(res.ok);self.assertEqual(len(fake.calls),start)
    def test_cleanup_requires_confirmation(self):
        fake=Fake({'id1':record('id1')})
        project=ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',))
        plan=inspect_cleanup(project,fake)
        start=len(fake.calls)
        r=apply_cleanup(plan,fake,None,confirmed=False,accept_writable_layer=True)
        self.assertFalse(r.ok);self.assertEqual(len(fake.calls),start)
    def test_cleanup_shared_image_is_not_deleted(self):
        fake=Fake({'id1':record('id1'),'id2':record('id2',project='other')})
        plan=inspect_cleanup(ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',)),fake)
        self.assertEqual(plan.image_candidates,())

class VolumeSafetyTests(unittest.TestCase):
    def test_named_volume_from_compose_config_is_preserved(self):
        fake=Fake({'id1':record('id1',mounts=[{'Type':'volume','Name':'alpha_storage','Source':'/var/lib/docker/volumes/alpha_storage/_data','Destination':'/data'}])})
        old=fake.run
        def command(argv,**kw):
            if 'config' in argv and '--format' in argv:
                return CommandResult(0,json.dumps({'volumes':{'storage':{}}}))
            return old(argv,**kw)
        fake.run=command
        plan=inspect_cleanup(ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',)),fake)
        self.assertFalse(plan.blockers)
    def test_anonymous_volume_even_when_project_prefix_is_blocked(self):
        fake=Fake({'id1':record('id1',mounts=[{'Type':'volume','Name':'alpha_anonrandom','Destination':'/data'}])})
        plan=inspect_cleanup(ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',)),fake)
        self.assertTrue(plan.blockers)

class CleanupExecutionTests(unittest.TestCase):
    def test_cleanup_scoped_commands_and_no_volume_delete(self):
        from unittest.mock import patch
        fake=Fake({'id1':record('id1')})
        project=ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',))
        plan=inspect_cleanup(project,fake)
        with patch('pathlib.Path.is_file',return_value=True):
            result=apply_cleanup(plan,fake,None,confirmed=True,accept_writable_layer=True)
        self.assertTrue(result.ok)
        mutations=[x for x in fake.calls if 'stop' in x or 'rm' in x or 'prune' in x]
        self.assertEqual(len(mutations),2)
        self.assertTrue(all('-p' in m and 'alpha' in m for m in mutations))
        self.assertTrue(all('-v' not in m and '--volumes' not in m and 'prune' not in m for m in mutations))

    def test_changed_container_id_blocks_cleanup(self):
        from unittest.mock import patch
        fake=Fake({'id1':record('id1')})
        project=ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',))
        plan=inspect_cleanup(project,fake)
        fake.containers={'id2':record('id2')}
        with patch('pathlib.Path.is_file',return_value=True):
            result=apply_cleanup(plan,fake,None,confirmed=True,accept_writable_layer=True)
        self.assertFalse(result.ok)
        self.assertFalse(any('stop' in cmd or 'rm' in cmd for cmd in fake.calls))

class WritableLayerTests(unittest.TestCase):
    def test_critical_data_path_in_container_layer_blocks_cleanup(self):
        fake=Fake({'id1':record('id1')})
        old=fake.run
        def cmd(argv,**kw):
            if argv[:2]==['docker','diff']:
                return CommandResult(0,'A /var/lib/postgresql/data/pg_wal/0001\n')
            return old(argv,**kw)
        fake.run=cmd
        plan=inspect_cleanup(ComposeProject('alpha','/work','/work/compose.yaml',('web',),('id1',)),fake)
        self.assertTrue(any('writable' in block for block in plan.blockers))

    def test_failed_diff_inspection_blocks_cleanup(self):
        fake=Fake({'id1':record('id1')})
        old=fake.run
        def cmd(argv,**kw):
            if argv[:2]==['docker','diff']:return CommandResult(1,'','permission denied')
            return old(argv,**kw)
        fake.run=cmd
        plan=inspect_cleanup(ComposeProject('alpha','/work','/compose.yaml',('web',),('id1',)),fake)
        self.assertTrue(plan.blockers)

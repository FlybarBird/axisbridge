import importlib.util
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch, Mock
import socket
import sys
BASE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('deploy',BASE/'deploy.py')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)

class InstallerTests(unittest.TestCase):
    def test_actual_slate7_identifiers_accepted(self):
        self.assertTrue(deploy.is_slate7_model('GL.iNet BE3600, Inc. IPQ5332/AP-MI04.1-C2'))
        self.assertTrue(deploy.is_slate7_model('qcom,ipq5332-ap-mi04.1-c2'))
        self.assertTrue(deploy.is_slate7_model('GL-BE3600'))
        self.assertFalse(deploy.is_slate7_model('GL-MT3000 Beryl AX'))

    def test_atomic_write_replaces_and_sets_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'config';p.write_text('old')
            deploy.atomic_write(p,b'new')
            self.assertEqual(p.read_bytes(),b'new')
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertEqual(len(list(Path(directory).iterdir())),1)

    def test_rollback_restores_prior_release_and_removes_new_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);app=root/'app';app.mkdir();(app/'code.py').write_text('old')
            config=root/'config';config.write_text('old settings');config.chmod(0o640)
            new=root/'new-service'
            tx=deploy.Transaction(root/'backup')
            for p in (app,config,new):tx.snapshot(p)
            (app/'code.py').write_text('new');(app/'extra').write_text('extra')
            config.write_text('new settings');new.write_text('created')
            tx.rollback()
            self.assertEqual((app/'code.py').read_text(),'old')
            self.assertFalse((app/'extra').exists());self.assertFalse(new.exists())
            self.assertEqual(config.read_text(),'old settings')
            self.assertEqual(config.stat().st_mode&0o777,0o640)

    def test_fresh_install_rollback_removes_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);app=root/'app';tx=deploy.Transaction(root/'backup');tx.snapshot(app)
            app.mkdir();(app/'run.py').write_text('app');tx.rollback()
            self.assertFalse(app.exists())

    def test_payload_checksums_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);payload=root/'payload';payload.mkdir();f=payload/'file';f.write_bytes(b'original')
            (root/'SHA256SUMS').write_text(hashlib.sha256(f.read_bytes()).hexdigest()+'  payload/file\n')
            deploy.verify_payload(root)
            f.write_bytes(b'changed')
            with self.assertRaises(ValueError):deploy.verify_payload(root)

    def test_unlisted_payload_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'payload').mkdir();(root/'payload'/'extra').write_text('extra');(root/'SHA256SUMS').write_text('')
            with self.assertRaises(ValueError):deploy.verify_payload(root)

    def test_port_collision_detected(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));s.listen()
            with self.assertRaises(ValueError):deploy.check_port(s.getsockname()[1],'127.0.0.1')

    def test_wrong_platform_rejected(self):
        with patch.object(Path,'is_file',return_value=False):
            with self.assertRaisesRegex(ValueError,'OpenWrt'):deploy.router_guard()

    def deployment_case(self, upgrade, fail_health):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            app=root/'usr/share/axisbridge';app.parent.mkdir(parents=True)
            data=root/'etc/axisbridge';data.mkdir(parents=True)
            service=root/'etc/init.d/axisbridge';service.parent.mkdir(parents=True)
            control=root/'usr/bin/axisbridge-ctl';control.parent.mkdir(parents=True)
            config=root/'etc/config/axisbridge';config.parent.mkdir(parents=True)
            (data/'current-show.json').write_text('saved show')
            if upgrade:
                app.mkdir();(app/'previous.py').write_text('old app')
                service.write_text('old service');control.write_text('old control')
                config.write_text('old config');(data/'installation.json').write_text('old receipt')
            original_mkdtemp=tempfile.mkdtemp
            calls=[]
            def service_action(action,check=False):
                calls.append(action)
                return action!='status'
            def make_temp(*args,**kwargs):
                kwargs['dir']=app.parent
                return original_mkdtemp(*args,**kwargs)
            with patch.multiple(deploy, APP=app, DATA=data, SERVICE=service, CONTROL=control, SETTINGS=config), \
                 patch.object(deploy,'preflight',return_value=(8080,'0.0.0.0',upgrade,upgrade,upgrade)), \
                 patch.object(deploy,'check_port'), patch.object(deploy,'uci',return_value='1'), \
                 patch.object(deploy,'service',side_effect=service_action), \
                 patch.object(deploy.subprocess,'run'), \
                 patch.object(deploy.tempfile,'mkdtemp',side_effect=make_temp), \
                 patch.object(deploy,'health_check',side_effect=ValueError('health failure') if fail_health else None):
                if fail_health:
                    with self.assertRaisesRegex(ValueError,'health failure'):deploy.install(8080,'0.0.0.0')
                else:deploy.install(8080,'0.0.0.0')
            self.assertEqual((data/'current-show.json').read_text(),'saved show')
            if fail_health:
                self.assertEqual((app/'previous.py').read_text(),'old app')
                self.assertEqual(service.read_text(),'old service')
                self.assertEqual(control.read_text(),'old control')
                self.assertEqual(config.read_text(),'old config')
                self.assertEqual((data/'installation.json').read_text(),'old receipt')
                self.assertEqual(calls[-2:],['enable','start'])
            else:
                self.assertTrue((app/'router_run.py').exists())
                self.assertIn('procd',service.read_text())
                self.assertIn("option port '8080'",config.read_text())
                self.assertEqual(calls,['enable','start'])
            self.assertEqual(list(app.parent.glob('.axisbridge-*')),[])

    def test_full_fresh_deployment(self):
        self.deployment_case(False,False)

    def test_failed_upgrade_restores_release_and_service(self):
        self.deployment_case(True,True)

    def test_no_changes_to_network_in_service(self):
        s=(BASE/'payload/etc/init.d/axisbridge').read_text()
        self.assertIn('USE_PROCD=1',s)
        self.assertIn('router_run.py',s)
        self.assertIn('procd_set_param respawn',s)
        self.assertNotIn('uci set network',s)

if __name__=='__main__':unittest.main(verbosity=2)

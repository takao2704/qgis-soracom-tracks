import json
import unittest
from unittest.mock import patch
import test_qgis_integration as integration
from soracom_tracks.soracom_io import DemoError
HAS_QGIS=integration.HAS_QGIS
if HAS_QGIS:
    from soracom_tracks.auth_ui import AuthDialog, Vault
    from qgis.PyQt.QtWidgets import QLineEdit
    from qgis.core import QgsAuthMethodConfig

@unittest.skipUnless(HAS_QGIS,'Requires QGIS')
class AuthQgisTests(unittest.TestCase):
    if HAS_QGIS:
        setUpClass=classmethod(integration.IntegrationTests.setUpClass.__func__)
        setUp=integration.IntegrationTests.setUp
        tearDown=integration.IntegrationTests.tearDown

    def test_connection_layout_and_auth_choices(self):
        d=self.dock
        for key in ('resource','run_filter','camera','crs'):
            self.assertFalse(d.settings_body.isAncestorOf(d.inputs[key]))
        self.assertFalse(d.settings_body.isAncestorOf(d.offset))
        self.assertTrue(d.camera_box.isAncestorOf(d.offset))
        self.assertEqual(d.auth_mode.count(),4)
        dlg=AuthDialog(d,'login')
        self.assertEqual(dlg.fields['password'].echoMode(),QLineEdit.Password)
        self.assertTrue(dlg.fields['email'].isHidden())
        dlg.kind.setCurrentIndex(1);self.assertFalse(dlg.fields['email'].isHidden())
        self.assertTrue(dlg.fields['operatorId'].isHidden());dlg.reject();dlg.deleteLater()
        dlg=AuthDialog(d,'api');self.assertFalse(dlg.fields['authKey'].isHidden())
        dlg.kind.setCurrentIndex(1);self.assertFalse(dlg.fields['token'].isHidden());dlg.reject();dlg.deleteLater()

    def test_switch_auth_back_restores_cli_selection_and_camera(self):
        d=self.dock;d.inputs['profile'].setText('fake-cli')
        r=dict(resource_type='Subscriber',resource_id='FAKE-SIM',coverage='g')
        d.set_sim_choices([r],[r]);d.inputs['camera'].setText('FAKE-CAM')
        d.auth_mode.setCurrentIndex(1);self.assertEqual(d.selected_resources(),[])
        d.auth_mode.setCurrentIndex(0)
        self.assertEqual(d.selected_resources(),[r]);self.assertEqual(d.inputs['camera'].text(),'FAKE-CAM')

    def test_both_coverages_failure_retains_selection(self):
        d=self.dock;seen=[]
        old=dict(resource_type='Subscriber',resource_id='OLD',coverage='g');d.set_sim_choices([old],[old])
        class Client:
            def __init__(self,c):self.c=c
            def subscribers(self,cancel):
                seen.append(self.c)
                if self.c=='g':raise DemoError('TEST')
                return [dict(resource_type='Subscriber',resource_id='JP')]
        d.client=lambda c:Client(c['coverage']);d.launch=lambda label,work,done:done(work(lambda:False))
        d.refresh_sims();self.assertEqual(seen,['jp','g']);self.assertEqual(d.selected_resources()[0]['resource_id'],'OLD')
        self.assertIn('g の取得失敗',d.status.text())
        all_rows=[d.sim_list.item(i).data(256) for i in range(d.sim_list.count())]
        self.assertEqual({r['coverage'] for r in all_rows},{'jp','g'})

    def test_credentials_not_in_settings_and_connection_restore(self):
        d=self.dock;d.inputs['profile'].setText('fake-cli')
        d.auth_mode.setCurrentIndex(d.auth_mode.findData('api'))
        secret=dict(kind='key',authKeyId='SECRET-ID',authKey='SECRET-VALUE')
        d.apply_auth('api','ABC1234',secret);d.save_settings()
        text=(d.storage/'settings.json').read_text()
        self.assertNotIn('SECRET',text);self.assertEqual(d.config()['profile'],'qgis-auth:ABC1234')
        other=integration.SoracomDock(self.iface,d.storage)
        try:
            self.assertEqual(other.auth_ref,'ABC1234');self.assertEqual(other.auth_mode.currentData(),'api')
            other.auth_mode.setCurrentIndex(0);self.assertEqual(other.inputs['profile'].text(),'fake-cli')
        finally:other.shutdown();other.deleteLater()
        d.auth_mode.setCurrentIndex(0);self.assertIsNone(d.api_runner('jp'))

    def test_mixed_source_format_settings_persist(self):
        d=self.dock;d.inputs['profile'].setText('fake')
        rows=[dict(resource_type='Subscriber',resource_id='SAME',coverage=c) for c in ('jp','g')]
        d.set_sim_choices(rows,rows);d.format_changed(rows[0],'multi');d.format_changed(rows[1],'gnss')
        self.assertNotEqual(d.resource_key(rows[0]),d.resource_key(rows[1]))
        d.save_settings();other=integration.SoracomDock(self.iface,d.storage)
        try:
            self.assertEqual(len(other.selected_resources()),2)
            self.assertEqual(other.resource_schema(rows[0])['schema']['kind'],'multi')
            self.assertEqual(other.resource_schema(rows[1])['schema']['kind'],'gnss')
        finally:other.shutdown();other.deleteLater()

    def test_vault_roundtrip_adapter_without_real_password_database(self):
        configs={}
        class Manager:
            def isDisabled(self):return False
            def setMasterPassword(self,verify):return True
            def storeAuthenticationConfig(self,c,overwrite):
                c.setId(c.id() or 'ABC1234');configs[c.id()]=QgsAuthMethodConfig(c);return True,c
            def loadAuthenticationConfig(self,ref,c,full):return True,configs[ref]
        vault=Vault();vault.manager=Manager()
        credentials=dict(kind='key',authKeyId='FAKE-ID',authKey='FAKE-SECRET')
        ref=vault.save(credentials);self.assertEqual(vault.load(ref),credentials)
        vault.manager.setMasterPassword=lambda _:False
        with self.assertRaises(DemoError):vault.save(credentials)

    def test_session_login_never_uses_vault_and_restart_requires_login(self):
        import time
        d=self.dock
        credentials=dict(kind='session',identity='FAKE-IDENTITY',sessions={'jp':dict(apiKey='SECRET-API',token='SECRET-TOKEN')},expires_at=time.monotonic()+3300)
        with patch('soracom_tracks.auth_ui.Vault',side_effect=AssertionError('No vault for session')):
            d.apply_session(credentials)
            d.api_session.request=lambda *a:(None,{})
            text=(d.storage/'settings.json').read_text()
            self.assertNotIn('SECRET',text);self.assertNotIn('sessions',text)
            d.save_settings();self.assertIn('ログイン情報は保存しません',d.status.text())
            other=integration.SoracomDock(self.iface,d.storage)
            try:
                self.assertEqual(other.auth_mode.currentData(),'session')
                with self.assertRaisesRegex(DemoError,'ログイン'):other.api_runner('jp')
            finally:other.shutdown();other.deleteLater()
            runner=d.api_runner('jp')
            d.logout_session()
            with self.assertRaisesRegex(DemoError,'ログアウト'):runner(['subscribers','list'])
            self.assertIsNone(d.api_session)

    def test_session_dialog_has_no_provision_or_vault(self):
        import time
        d=self.dock;dlg=AuthDialog(d,'session')
        self.assertEqual(dlg.kind.count(),1)
        self.assertEqual(dlg.submit.text(),'このSAMでログイン')
        for k,v in dict(operatorId='OP',userName='sam',password='SECRET-PASS').items():dlg.fields[k].setText(v)
        creds=dict(kind='session',identity='FAKE',sessions={},expires_at=time.monotonic()+3300)
        d.launch=lambda label,work,done,on_error=None,**kw:done(work(lambda:False))
        with patch('soracom_tracks.auth_ui.session_login',return_value=creds),patch('soracom_tracks.auth_ui.provision',side_effect=AssertionError('no provision')),patch('soracom_tracks.auth_ui.Vault',side_effect=AssertionError('no vault')):
            dlg.start()
        self.assertIsNotNone(d.api_session)
        self.assertTrue(all(not f.text() for f in dlg.fields.values()))
        self.assertEqual(creds,{})
        dlg.deleteLater()

    def test_switch_from_persistent_api_does_not_reuse_credentials_for_session(self):
        d=self.dock;d.apply_auth('api','ABC1234',dict(kind='key',authKeyId='FAKE',authKey='SECRET'))
        old=d.api_session;d.auth_mode.setCurrentIndex(d.auth_mode.findData('session'))
        self.assertTrue(old.closed)
        with self.assertRaisesRegex(DemoError,'ログイン'):d.api_runner('jp')

    def test_quit_callback_revokes_temporary_session(self):
        import time
        d=self.dock;calls=[]
        d.apply_session(dict(kind='session',identity='TEST',sessions={'jp':dict(apiKey='FAKE',token='FAKE')},expires_at=time.monotonic()+3300))
        session=d.api_session
        session.request=lambda c,m,p,*args:(calls.append((m,p)),{})
        d.end_auth_session()  # Same callback connected to aboutToQuit and shutdown.
        self.assertEqual(calls,[('POST','/auth/logout')])
        self.assertIsNone(d.api_session)
        self.assertTrue(session.closed)

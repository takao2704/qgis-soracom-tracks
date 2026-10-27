import unittest
import test_qgis_integration as integration
from test_schema import BASE,row

@unittest.skipUnless(integration.HAS_QGIS,'Requires QGIS')
class CompactUITests(unittest.TestCase):
    if integration.HAS_QGIS:
        setUpClass=classmethod(integration.IntegrationTests.setUpClass.__func__)
        setUp=integration.IntegrationTests.setUp
        tearDown=integration.IntegrationTests.tearDown

    def prepare(self):
        from soracom_tracks.schema import preset,normalize_schema
        d=self.dock;r=dict(resource_type='Subscriber',resource_id='FAKE-A',name='車載A',coverage='jp')
        d.inputs['start'].setText('2026-10-22T12:40:00+09:00');d.inputs['end'].setText('2026-10-22T12:45:00+09:00')
        d.set_sim_choices([r],[r]);d.format_changed(r,'multi');d.sim_basis_changed(r,'received')
        source={**r,'profile':''};rows=[row(dict(lat=35,lon=139),i*5000) for i in range(2)];records=[]
        points,bad=normalize_schema(rows,preset('multi'),source,records,'received')
        d.apply_points(points,bad,'EPSG:4326',False,records)
        d.source_config=d.config();d.raw_batches=[dict(source=source,raw_rows=rows)];d.store_dataset_config()
        return r

    def test_next_conditions_do_not_relabel_loaded_data(self):
        d=self.dock;r=self.prepare();old=d.layer.id()
        d.inputs['end'].setText('2026-10-22T13:00:00+09:00')
        other={**r,'resource_id':'FAKE-B','name':'追加B'};d.set_sim_choices([r,other],[r,other])
        self.assertIn('2 SIM',d.next_summary.text());self.assertIn('1 SIM',d.loaded_summary.text())
        self.assertIn('12:45',d.loaded_summary.text());self.assertNotIn('13:00',d.loaded_summary.text())
        self.assertIn('前回のデータ',d.loaded_summary.text());self.assertEqual(d.layer.id(),old)
        self.assertIn('形式未設定',d.selection_hint.text());self.assertFalse(d.load_button.isEnabled())

    def test_sim_cancel_preserves_selection_and_dataset(self):
        from qgis.PyQt.QtCore import Qt
        d=self.dock;r=self.prepare();old=d.layer.id();d.open_sim_selection()
        d.sim_list.item(0).setCheckState(Qt.Unchecked);self.assertEqual(d.selected_resources(),[])
        d.sim_dialog.reject();self.assertEqual(d.selected_resources()[0]['resource_id'],r['resource_id'])
        self.assertEqual(old,d.layer.id())

    def test_settings_dialog_auto_apply_after_close(self):
        from qgis.PyQt.QtTest import QTest
        d=self.dock;r=self.prepare();d.client=lambda *_:self.fail('Reprocessing must not call API')
        d.open_resource_settings(d.resource_key(r));self.assertTrue(d.editor_dialog.isVisible())
        d.sim_time_combo.setCurrentIndex(d.sim_time_combo.findData('measured'));d.editor_dialog.reject();QTest.qWait(650)
        self.assertFalse(d.schema_pending);self.assertEqual(d.layer.featureCount(),2)
        self.assertIn('自動反映',d.status_summary.text());self.assertIn('1 SIM',d.loaded_summary.text())

    def test_unconfigured_schema_can_fetch_sample_without_full_import(self):
        from soracom_tracks.schema_dialog import SchemaDialog
        d=self.dock;r=dict(resource_type='Subscriber',resource_id='UNKNOWN',coverage='g',name='未知形式')
        d.set_sim_choices([r],[r]);self.assertFalse(d.load_button.isEnabled())
        calls=[]
        class Client:
            def runner(self,args,cancel):calls.append(args);return [row(dict(location=dict(lat=35,lon=139)))]
        coverage=[]
        def client(config):coverage.append(config['coverage']);return Client()
        d.client=client;d.launch=lambda label,fn,callback,on_error=None:callback(fn(lambda:False))
        dialog=SchemaDialog(d,dict(name='任意',lat='',lon='',values=[]),r)
        dialog.fetch_samples()
        self.assertEqual(coverage,['g']);self.assertEqual(calls[0][-2:],['--limit','3'])
        self.assertIn('UNKNOWN',calls[0]);self.assertEqual(len(dialog.samples),1);self.assertIsNone(d.layer)
        self.assertNotIn(d.resource_key(r),d.schema_store['resources']);dialog.reject()

    def test_media_focus_preserves_settings_and_has_restore(self):
        d=self.dock;self.prepare();before=d.config();d.focus_media_button.click()
        self.assertFalse(d.acquisition_box.isChecked());self.assertEqual(d.focus_media_button.text(),'取得条件を表示')
        d.focus_media_button.click();self.assertTrue(d.acquisition_box.isChecked());self.assertEqual(d.config(),before)
        self.assertTrue(d.editor_dialog.isAncestorOf(d.format_combo));self.assertTrue(d.camera_dialog.isAncestorOf(d.camera_filter))
        self.assertTrue(d.connection_dialog.isAncestorOf(d.settings_box))

    def test_details_keep_full_error_and_main_summary(self):
        d=self.dock;d.status.setText('取得失敗\n対象SIMの設定を確認してください。')
        self.assertEqual(d.status_summary.text(),'取得失敗');self.assertIn('対象SIM',d.status.text())

    def test_scroll_track_survives_fold_and_overflow_without_nested_cards(self):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QScrollArea
        from qgis.PyQt.QtTest import QTest
        d=self.dock;self.iface.window.addDockWidget(Qt.RightDockWidgetArea,d)
        self.iface.window.resize(440,600);self.iface.window.show();d.show();r=self.prepare()
        resources=[dict(r,resource_id='FAKE-'+str(i),name='車載'+str(i)) for i in range(12)]
        d.set_sim_choices(resources,resources);QTest.qWait(40)
        self.assertEqual(d.scroll.verticalScrollBarPolicy(),Qt.ScrollBarAlwaysOn)
        self.assertTrue(d.scroll.verticalScrollBar().isVisible())
        self.assertGreater(d.scroll.verticalScrollBar().maximum(),0)
        self.assertEqual(d.scroll.widget().findChildren(QScrollArea),[])
        before=d.config();d.focus_media_button.click();QTest.qWait(40)
        self.assertTrue(d.scroll.verticalScrollBar().isVisible())
        d.focus_media_button.click();QTest.qWait(40)
        self.assertGreater(d.scroll.verticalScrollBar().maximum(),0)
        self.assertEqual(d.config(),before)
        for dialog in (d.connection_dialog,d.editor_dialog,d.camera_dialog,d.sim_dialog):
            self.assertEqual(dialog.findChild(QScrollArea).verticalScrollBarPolicy(),Qt.ScrollBarAlwaysOn)

    def test_panel_cursor_is_arrow_but_edit_fields_remain_text_editable(self):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QApplication
        d=self.dock
        self.assertEqual(d.scroll.viewport().cursor().shape(),Qt.ArrowCursor)
        self.assertEqual(d.status.cursor().shape(),Qt.ArrowCursor)
        self.assertEqual(d.status.textInteractionFlags(),Qt.NoTextInteraction)
        self.assertEqual(d.inputs['start'].cursor().shape(),Qt.IBeamCursor)
        d.status.setText('取得失敗\n設定を確認');d.status_details.setChecked(True);d.copy_status_button.click()
        self.assertEqual(QApplication.clipboard().text(),'取得失敗\n設定を確認')

    def test_fixed_footer_has_opaque_background_and_stays_outside_scroll(self):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest
        d=self.dock;self.iface.window.addDockWidget(Qt.RightDockWidgetArea,d)
        self.iface.window.resize(440,700);self.iface.window.show();d.show();self.prepare()
        d.status.setText('取得形式・範囲を変更しました。点をクリックするか取得ボタンを押してください')
        for surface in (d.widget(),d.scroll.viewport(),d.scroll.widget(),d.fixed_footer):
            self.assertTrue(surface.autoFillBackground())
        self.assertTrue(d.fixed_footer.isAncestorOf(d.loaded_summary))
        self.assertTrue(d.fixed_footer.isAncestorOf(d.save_button))
        for expanded in (False,True,False):
            d.status_details.setChecked(expanded);QTest.qWait(30)
            for value in (d.scroll.verticalScrollBar().maximum(),0):
                d.scroll.verticalScrollBar().setValue(value);QTest.qWait(30)
                self.assertLess(d.scroll.geometry().bottom(),d.fixed_footer.geometry().top())
                image=d.fixed_footer.grab().toImage()
                self.assertEqual(image.pixelColor(1,1).rgb(),d.fixed_footer.palette().color(d.fixed_footer.backgroundRole()).rgb())

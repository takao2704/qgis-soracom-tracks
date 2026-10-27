import copy
import json
import unittest
from test_schema import BASE,row,custom
try:
    import test_qgis_integration as integration
    from qgis.core import QgsProject
    from qgis.PyQt.QtCore import Qt
    from soracom_tracks.schema import preset,normalize_schema
    from soracom_tracks.schema_dialog import SchemaDialog
    HAS_QGIS=integration.HAS_QGIS
except ImportError:HAS_QGIS=False

@unittest.skipUnless(HAS_QGIS,'Requires QGIS')
class SchemaIntegrationTests(unittest.TestCase):
    if HAS_QGIS:
        setUpClass=classmethod(integration.IntegrationTests.setUpClass.__func__)
        setUp=integration.IntegrationTests.setUp
        tearDown=integration.IntegrationTests.tearDown

    def resource(self):return dict(resource_type='Subscriber',resource_id='FAKE-UNIT',name='テスト端末')

    def test_custom_dialog_preview_typed_values_and_copy(self):
        r=self.resource();samples=[row(dict(position=[dict(lat=35,lon=139)],timestamp=BASE/1000,speed=12,state='moving',active=False))]
        dialog=SchemaDialog(self.dock,custom(),r,samples);dialog.validate()
        self.assertIsNotNone(dialog.valid_spec);self.assertIn('速度: 12.0',dialog.preview.toPlainText())
        dialog.values.item(0,0).setText('別の名前');self.assertIsNone(dialog.valid_spec)
        dialog.validate();dialog.save_definition();self.assertEqual(dialog.definition['values'][0]['name'],'別の名前');dialog.deleteLater()

    def test_per_sim_format_time_persistence_and_context(self):
        d=self.dock;r=self.resource();d.inputs['profile'].setText('fake');d.set_sim_choices([r],[r])
        self.assertEqual(d.format_table.rowCount(),1)
        d.format_combo.setCurrentIndex(d.format_combo.findData('multi'))
        d.sim_time_combo.setCurrentIndex(d.sim_time_combo.findData('received'))
        self.assertEqual(d.resource_schema(r)['basis'],'received');d.save_settings()
        other=integration.SoracomDock(self.iface,self.root/'config');self.assertEqual(other.resource_schema(r)['schema']['kind'],'multi');other.shutdown();other.deleteLater()
        d.inputs['profile'].setText('another')
        with self.assertRaises(Exception):d.resource_schema(r)

    def test_missing_time_no_image_then_reprocess_arrival_without_api(self):
        d=self.dock;r=self.resource();d.set_sim_choices([r],[r]);d.format_changed(r,'multi')
        data=[row(dict(lat=35,lon=139,temp=0),i*5000) for i in range(2)]
        source={**r,'profile':'','coverage':'jp'};records=[];points,bad=normalize_schema(data,preset('multi'),source,records,'measured')
        d.apply_points(points,bad,'EPSG:4326',False,records);d.source_config=d.config();d.raw_batches=[dict(source=source,raw_rows=data)]
        d.choose(next(d.layer.getFeatures()));calls=[]
        class Client:
            def image(self,device,stamp,cancel):calls.append(stamp)
        d.client=lambda _:Client();d.launch=lambda label,fn,callback:fn(lambda:False)
        d.get_image();self.assertEqual(calls,[]);self.assertIn('時刻がありません',d.status.text())
        d.time_basis.setCurrentIndex(d.time_basis.findData('received'));d.get_image();self.assertEqual(calls,[]);self.assertTrue(d.schema_pending)
        self.assertFalse(d.choose(next(d.layer.getFeatures())));self.assertIsNone(d.pixmap)
        d.reprocess_data();self.assertFalse(d.schema_pending);self.assertEqual(d.tracks.featureCount(),1)
        d.inputs['camera'].setText('FAKE-CAM');d.choose(next(d.layer.getFeatures()));d.get_image();self.assertEqual(calls,[BASE])

    def test_attributes_gpkg_and_project_roundtrip(self):
        d=self.dock;data=[row(dict(position=[dict(lat=35,lon=139)],timestamp=BASE/1000,speed=0,state='moving',active=False))]
        records=[];points,bad=normalize_schema(data,custom(),self.resource(),records,'received');d.apply_points(points,bad,'EPSG:4326',False,records)
        defs=points[0]['extra_definitions'];keys=list(defs)
        before=next(d.layer.getFeatures());self.assertEqual(before[keys[0]],0.0);self.assertFalse(before[keys[2]])
        d.persist_layers(str(self.root/'custom.gpkg'));f=next(d.layer.getFeatures())
        self.assertEqual(f[keys[1]],'moving');self.assertEqual(f['process_ms'],BASE);self.assertEqual(d.layer.fields().field(keys[0]).alias(),'速度 (km/h)')
        project=QgsProject.instance();project.setFileName(str(self.root/'custom.qgz'));self.assertTrue(project.write());project.clear();self.assertTrue(project.read(str(self.root/'custom.qgz')))
        d.attach_existing(automatic=True);self.assertEqual(next(d.layer.getFeatures())[keys[1]],'moving')

    def test_saved_dataset_can_reprocess_without_fetch(self):
        d=self.dock;r=self.resource();d.set_sim_choices([r],[r]);d.format_changed(r,'multi')
        source={**r,'profile':'','coverage':'jp'};data=[row(dict(lat=35,lon=139),i*5000) for i in range(2)]
        records=[];points,bad=normalize_schema(data,preset('multi'),source,records,'measured')
        d.apply_points(points,bad,'EPSG:4326',False,records);d.source_config=d.config();d.raw_batches=[dict(source=source,raw_rows=data)];d.store_dataset_config()
        d.persist_layers(str(self.root/'restore.gpkg'));self.assertIsNotNone(d.raw_batches)
        d.time_basis.setCurrentIndex(d.time_basis.findData('received'));d.reprocess_data();self.assertEqual(d.tracks.featureCount(),1)

    def prepare_dataset(self, key='multi', basis='received'):
        d=self.dock;r=self.resource();d.set_sim_choices([r],[r]);d.format_changed(r,key)
        d.sim_basis_changed(r,basis)
        source={**r,'profile':'','coverage':'jp'}
        data=[row(dict(lat=35+i*.0001,lon=139+i*.0001),i*5000) for i in range(2)]
        if key=='review-custom':
            data=[row(dict(position=[dict(lat=35+i*.0001,lon=139+i*.0001)],timestamp=BASE/1000+i*5,speed=0,state='停止',active=False),i*5000) for i in range(2)]
        records=[];points,bad=normalize_schema(data,d.resource_schema(r)['schema'],source,records,basis)
        d.apply_points(points,bad,'EPSG:4326',False,records)
        d.source_config=d.config();d.raw_batches=[dict(source=source,raw_rows=data)];d.store_dataset_config()
        return r

    def test_new_profile_restores_project_formats_for_reprocess(self):
        d=self.dock;d.schema_store['presets']['review-custom']=custom()
        self.prepare_dataset('review-custom')
        d.persist_layers(str(self.root/'portable.gpkg'))
        project=QgsProject.instance();project.setFileName(str(self.root/'portable.qgz'));self.assertTrue(project.write())
        project.clear();self.iface.active=None
        self.assertTrue(project.read(str(self.root/'portable.qgz')))
        other=integration.SoracomDock(self.iface,self.root/'fresh-profile')
        try:
            other.attach_existing(automatic=True)
            self.assertEqual(len(other.selected_resources()),1)
            self.assertEqual(other.resource_schema(self.resource())['schema']['values'][0]['name'],'速度')
            other.reprocess_data()
            self.assertFalse(other.schema_pending)
            self.assertEqual(other.layer.featureCount(),2)
            self.assertIn('再取得なし',other.status.text())
        finally:other.shutdown();other.deleteLater()

    def test_saving_pending_dataset_preserves_pending_and_applied_metadata(self):
        d=self.dock;r=self.prepare_dataset()
        before=d.layer.customProperty('soracom/formats')
        d.sim_basis_changed(r,'measured');self.assertTrue(d.schema_pending)
        d.persist_layers(str(self.root/'pending.gpkg'))
        self.assertTrue(d.schema_pending)
        self.assertEqual(d.layer.customProperty('soracom/formats'),before)
        self.assertFalse(d.choose(next(d.layer.getFeatures())))

    def test_empty_fetch_keeps_old_dataset_and_configuration(self):
        d=self.dock;self.prepare_dataset()
        old_layer=d.layer.id();old_config=copy.deepcopy(d.source_config);old_batches=copy.deepcopy(d.raw_batches)
        class EmptyClient:
            def harvest_many(self,*args):return [],0,[]
        d.client=lambda _:EmptyClient()
        d.launch=lambda label,fn,callback:callback(fn(lambda:False))
        d.inputs['start'].setText('2026-10-22T01:00:00Z')
        d.load_live()
        self.assertEqual(d.layer.id(),old_layer)
        self.assertEqual(d.source_config,old_config)
        self.assertEqual(d.raw_batches,old_batches)

    def test_offset_change_clears_displayed_image_without_request(self):
        from qgis.PyQt.QtGui import QPixmap,QColor
        d=self.dock;self.prepare_dataset();d.choose(next(d.layer.getFeatures()))
        pix=QPixmap(20,20);pix.fill(QColor('blue'));d.show_pixmap(pix)
        d.offset.setValue(2)
        self.assertIsNone(d.pixmap)
        self.assertIsNone(d.image_label.pixmap())

    def test_selected_sim_editor_keeps_names_and_other_sim_settings(self):
        d=self.dock
        resources=[dict(resource_type='Subscriber',resource_id='FAKE-'+str(i),name='架空SIM '+str(i)) for i in range(3)]
        d.schema_store['presets']['long-custom']={**custom(),'name':'速度・状態・稼働のカスタム'}
        d.set_sim_choices(resources,resources)
        for r,key in zip(resources,('gnss','multi','long-custom')):d.format_changed(r,key)
        d.resize(410,1000);d.show();self.app.processEvents()
        for i in range(3):
            d.format_table.setCurrentCell(i,0)
            self.assertEqual(d.editing_resource(),dict(resources[i],coverage='jp'))
            self.assertEqual(d.format_combo.toolTip(),d.format_combo.currentText())
            self.assertEqual(d.sim_time_combo.accessibleName(),resources[i]['name']+'の時刻基準')
        d.sim_time_combo.setCurrentIndex(d.sim_time_combo.findData('received'))
        self.assertEqual(d.resource_schema(resources[2])['basis'],'received')
        self.assertEqual(d.schema_store['resources'][d.resource_key(resources[0])].get('basis','inherit'),'inherit')
        self.assertEqual(d.format_table.horizontalScrollBar().maximum(),0)
        last=2;table=d.format_table
        self.assertLessEqual(table.rowViewportPosition(last)+table.rowHeight(last),table.viewport().height())

    def test_restore_preserves_local_schema_and_marks_difference_pending(self):
        d=self.dock;d.schema_store['presets']['review-custom']=custom();r=self.prepare_dataset('review-custom')
        d.persist_layers(str(self.root/'conflict.gpkg'))
        other=integration.SoracomDock(self.iface,self.root/'other-config')
        try:
            other.schema_store['presets']['review-custom']={**custom(),'lat':'different.lat'}
            other.schema_store['resources'][other.resource_key(r)]=dict(format='review-custom',basis='received')
            other.attach_existing(automatic=True)
            self.assertTrue(other.schema_pending)
            self.assertEqual(other.resource_schema(r)['schema']['lat'],'different.lat')
            self.assertEqual(d.layer.customProperty('soracom/formats'),other.layer.customProperty('soracom/formats'))
            count=len(other.schema_store['presets']);other.restore_dataset_config()
            self.assertEqual(len(other.schema_store['presets']),count)
        finally:other.shutdown();other.deleteLater()

    def test_preview_explains_wrong_time_units_and_positionless_samples(self):
        d=self.dock;r=self.resource();spec=custom()
        data=row(dict(position=[dict(lat=35,lon=139)],timestamp=BASE))
        dialog=SchemaDialog(d,spec,r,[data])
        try:
            dialog.validate();self.assertIn('測位時刻の変換失敗',dialog.preview.toPlainText())
            dialog.sample_text.setPlainText(json.dumps(dict(position=[dict(lat=None,lon=139)],timestamp=BASE/1000)))
            dialog.validate();self.assertIsNotNone(dialog.valid_spec)
            self.assertIn('地図には表示されず',dialog.message.text())
        finally:dialog.deleteLater()

    def test_saved_and_reprocessed_layers_bind_their_own_records(self):
        d=self.dock;self.prepare_dataset()
        d.persist_layers(str(self.root/'original.gpkg'));saved_records=d.records.id()
        d.reprocess_data();current=d.layer;current_records=d.records.id()
        self.assertNotEqual(saved_records,current_records)
        self.assertIsNotNone(QgsProject.instance().mapLayer(saved_records))
        d.reset_binding();self.iface.active=current;d.attach_existing(automatic=True)
        self.assertIsNotNone(d.records)
        self.assertEqual(d.records.id(),current_records)
        self.assertEqual(d.records.featureCount(),2)
        self.assertIsNotNone(d.raw_batches)

    def test_keyboard_traverses_each_custom_editor_step(self):
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QApplication
        dialog=SchemaDialog(self.dock,custom(),self.resource(),[row(dict(position=[dict(lat=35,lon=139)],timestamp=BASE/1000,speed=0,state='moving',active=False))])
        dialog.show();QApplication.setActiveWindow(dialog);self.app.processEvents()
        try:
            dialog.validate();self.assertTrue(dialog.save.isEnabled())
            for page,target in [(0,dialog.sample_text),(1,dialog.paths['lat']),(2,dialog.preview_table)]:
                dialog.tabs.setCurrentIndex(page)
                for key in (Qt.Key_Tab,Qt.Key_Backtab):
                    dialog.name.setFocus();visited=[]
                    for step in range(100):
                        widget=QApplication.focusWidget();self.assertIsNotNone(widget)
                        if step and widget is dialog.name:break
                        if widget is dialog.save:visited.append('save')
                        if widget is target:visited.append('target')
                        QTest.keyClick(widget,key);self.app.processEvents()
                    self.assertIs(QApplication.focusWidget(),dialog.name)
                    self.assertIn('save',visited);self.assertIn('target',visited)
            dialog.paths['lat'].setText('wrong');self.assertFalse(dialog.save.isEnabled())
        finally:dialog.reject();dialog.deleteLater()

    def test_camera_routing_uses_clicked_sim_not_editor_and_none_blocks_cache(self):
        from qgis.PyQt.QtGui import QPixmap,QColor
        d=self.dock;r=self.prepare_dataset();r2={**r,'resource_id':'FAKE-OTHER','name':'もう1台'}
        d.set_sim_choices([r,r2],[r,r2]);d.inputs['camera'].setText('COMMON')
        d.set_camera_choices([dict(device_id='CAM-A',name='車載A',status='オフライン'),dict(device_id='CAM-B',name='車載B',status='オンライン')])
        d.set_camera_binding(r,'camera:CAM-A');d.set_camera_binding(r2,'camera:CAM-B')
        d.format_table.setCurrentCell(1,0);d.choose(next(d.layer.getFeatures()));self.assertEqual(d.effective_camera()['device'],'CAM-A')
        calls=[]
        class Client:
            def image(self,device,stamp,cancel):calls.append(device)
        d.client=lambda _:Client();d.launch=lambda label,fn,callback:fn(lambda:False)
        d.get_image();self.assertEqual(calls,['CAM-A'])
        image=self.root/'cached.jpg';pix=QPixmap(10,10);pix.fill(QColor('blue'));pix.save(str(image))
        d.image_done(dict(path=str(image),requested_ms=BASE,cached=True))
        self.assertTrue(d.choose(d.layer.getFeature(d.point['feature_id'])))
        d.set_camera_binding(r,'camera:CAM-B');self.assertIsNone(d.pixmap)
        self.assertFalse(d.choose(d.layer.getFeature(d.point['feature_id'])));self.assertIsNone(d.pixmap)
        d.get_image();self.assertEqual(calls,['CAM-A','CAM-B'])
        d.set_camera_binding(r,'none');self.assertFalse(d.schema_pending)
        self.assertFalse(d.choose(d.layer.getFeature(d.point['feature_id'])));d.get_image()
        self.assertEqual(calls,['CAM-A','CAM-B']);self.assertIn('連携しない',d.status.text())
        d.set_camera_binding(r,'inherit');self.assertEqual(d.effective_camera()['device'],'COMMON')

    def test_camera_bindings_roundtrip_context_and_pending_schema_snapshot(self):
        d=self.dock;r=self.prepare_dataset();d.inputs['camera'].setText('COMMON')
        d.set_camera_binding(r,'camera:CAM-A');d.choose(next(d.layer.getFeatures()))
        d.inputs['camera_coverage'].setText('g')
        with self.assertRaisesRegex(Exception,'カバレッジ'):d.effective_camera()
        d.inputs['camera_coverage'].setText('jp');self.assertEqual(d.effective_camera()['device'],'CAM-A')
        d.sim_basis_changed(r,'measured');snapshot=d.layer.customProperty('soracom/formats')
        d.set_camera_binding(r,'none');self.assertTrue(d.schema_pending)
        self.assertEqual(d.layer.customProperty('soracom/formats'),snapshot)
        d.persist_layers(str(self.root/'camera-route.gpkg'))
        other=integration.SoracomDock(self.iface,self.root/'fresh-camera')
        try:
            other.attach_existing(automatic=True)
            self.assertEqual(other.schema_store['cameras'][other.resource_key(r)]['mode'],'none')
            self.assertFalse(other.choose(next(other.layer.getFeatures())))
            other.save_settings()
        finally:other.shutdown();other.deleteLater()
        reloaded=integration.SoracomDock(self.iface,self.root/'fresh-camera')
        try:self.assertEqual(reloaded.schema_store['cameras'][reloaded.resource_key(r)]['mode'],'none')
        finally:reloaded.shutdown();reloaded.deleteLater()
        d.inputs['profile'].setText('different')
        self.assertNotIn(d.resource_key(r),d.schema_store['cameras'])

    def test_custom_sample_assignment_then_preview_invalidation(self):
        dialog=SchemaDialog(self.dock,dict(name='試験',lat='',lon='',values=[]),self.resource())
        try:
            dialog.sample_text.setPlainText('{"location":{"lat":35,"lon":139},"battery":0}')
            dialog.sample_to_fields();self.assertEqual(dialog.tabs.currentIndex(),1)
            for target,path in [('lat','location.lat'),('lon','location.lon')]:
                dialog.target.setCurrentIndex(dialog.target.findData(target));dialog.leaf.setCurrentIndex(dialog.leaf.findData(path));dialog.assign()
            dialog.add_value(dict(name='電池残量',type='number',unit='%'))
            dialog.target.setCurrentIndex(dialog.target.findData(0));dialog.leaf.setCurrentIndex(dialog.leaf.findData('battery'));dialog.assign()
            dialog.validate();self.assertTrue(dialog.save.isEnabled());self.assertEqual(dialog.tabs.currentIndex(),2)
            self.assertIn('電池残量: 0.0',dialog.preview.toPlainText());self.assertIn('緯度 35',dialog.preview.toPlainText())
            dialog.sample_text.setPlainText('invalid');self.assertFalse(dialog.save.isEnabled());self.assertIsNone(dialog.valid_spec)
            dialog.sample_to_fields();self.assertIn('サンプルを確認',dialog.message.text())
        finally:dialog.reject();dialog.deleteLater()

    def test_assignment_candidate_tracks_existing_path_and_blank_has_no_default(self):
        dialog=SchemaDialog(self.dock,dict(name='位置',lat='lat',lon='lon',values=[]),self.resource(),[row(dict(schema_version=1,lat=35,lon=139))])
        try:
            self.assertEqual(dialog.leaf.currentData(),'lat')
            self.assertEqual(dialog.path_samples['lat'].text(),'35')
            dialog.target.setCurrentIndex(dialog.target.findData('lon'))
            self.assertEqual(dialog.leaf.currentData(),'lon')
            dialog.paths['lat'].clear();dialog.target.setCurrentIndex(dialog.target.findData('lat'))
            self.assertIsNone(dialog.leaf.currentData());self.assertFalse(dialog.assign_button.isEnabled())
            dialog.assign();self.assertEqual(dialog.paths['lat'].text(),'')
        finally:dialog.reject();dialog.deleteLater()

    def test_preview_table_keeps_input_rows_invalid_position_and_zero_values(self):
        spec=dict(name='位置',lat='lat',lon='lon',time='t',time_format='seconds',values=[dict(name='電池',path='battery',type='number',unit='%')])
        samples=[row(dict(lat=35,lon=139,t=BASE/1000+10,battery=0)),row(dict(lat=None,lon=139,t=BASE/1000,battery=None))]
        dialog=SchemaDialog(self.dock,spec,self.resource(),samples)
        try:
            dialog.validate();table=dialog.preview_table
            self.assertEqual(table.rowCount(),2);self.assertEqual(table.item(0,1).text(),'35.0')
            self.assertEqual(table.item(1,1).text(),'—');self.assertIn('0.0',table.item(0,5).text())
            self.assertIn('null',table.item(1,5).text());self.assertNotEqual(table.item(1,6).text(),'正常')
            self.assertIn('battery',table.horizontalHeaderItem(5).text())
        finally:dialog.reject();dialog.deleteLater()

    def test_pending_per_sim_summary_and_saved_working_groups(self):
        d=self.dock;r=self.prepare_dataset();d.inputs['camera'].setText('COMMON')
        self.assertEqual(d.resource_apply_state(r),'反映済み')
        d.persist_layers(str(self.root/'baseline.gpkg'));saved=d.layer.id()
        d.sim_basis_changed(r,'measured');self.assertEqual(d.resource_apply_state(r),'未適用');self.assertIn('未適用',d.apply_note.text())
        d.set_camera_binding(r,'none');self.assertIn('カメラ連携なし',d.format_table.item(0,1).text())
        d.reprocess_data();self.assertEqual(d.resource_apply_state(r),'反映済み');self.assertNotIn('Harvest取得完了',d.status.text())
        tree=QgsProject.instance().layerTreeRoot()
        self.assertTrue(tree.findLayer(saved).parent().name().startswith('保存済み'))
        self.assertTrue(tree.findLayer(d.layer.id()).parent().name().startswith('作業中'))
        count=len(tree.findGroups());d.reprocess_data();self.assertEqual(len(tree.findGroups()),count)

    def test_camera_search_empty_disables_confirmation_and_refresh_preserves_filter(self):
        from qgis.PyQt.QtCore import QTimer
        from qgis.PyQt.QtWidgets import QDialog,QLineEdit,QListWidget,QPushButton
        d=self.dock;r=self.prepare_dataset();d.set_camera_choices([dict(device_id='A',name='車載A',status='オフライン')]);d.set_camera_binding(r,'camera:A')
        failures=[]
        class Client:
            def cameras(self,cancel):return [dict(device_id='B',name='車載B',status='オンライン')]
        d.client=lambda _:Client();d.launch=lambda label,fn,callback,on_error=None:callback(fn(lambda:False))
        def interact():
            dialog=next(x for x in d.findChildren(QDialog) if x.windowTitle()=='このSIMの連携カメラ')
            try:
                search=dialog.findChild(QLineEdit);choices=dialog.findChild(QListWidget)
                use=next(b for b in dialog.findChildren(QPushButton) if b.text().startswith('選択したカメラ'))
                search.setText('B');self.assertFalse(use.isEnabled())
                refresh=next(b for b in dialog.findChildren(QPushButton) if b.text()=='カメラ一覧を更新');refresh.click()
                self.assertEqual(search.text(),'B');self.assertEqual(choices.count(),1);self.assertFalse(use.isEnabled())
                choices.setCurrentRow(0);self.assertTrue(use.isEnabled())
                dialog.reject()
            except Exception as e:failures.append(e);dialog.reject()
        QTimer.singleShot(0,interact);d.pick_resource_camera()
        if failures:raise failures[0]
        self.assertEqual(d.schema_store['cameras'][d.resource_key(r)]['device_id'],'A')

    def test_failed_auto_apply_guidance_keeps_map_and_navigates(self):
        from qgis.PyQt.QtTest import QTest
        d=self.dock;r=self.prepare_dataset();original_layer=d.layer.id()
        d.format_changed(r,'missing-format');QTest.qWait(650)
        self.assertTrue(d.schema_pending);self.assertFalse(d.pending_settings_button.isHidden())
        self.assertIn('SIMごとの設定',d.status.text());self.assertIn('テスト端末',d.status.text())
        shown=[];d.scroll.ensureWidgetVisible=lambda widget,*args:shown.append(widget)
        d.pending_settings_button.click();self.assertEqual(shown,[d.format_combo])
        self.assertEqual(d.layer.id(),original_layer)
        d.format_changed(r,'multi');QTest.qWait(650)
        self.assertFalse(d.schema_pending);self.assertTrue(d.pending_settings_button.isHidden())

    def test_pending_guidance_without_raw_data_explains_retrieval(self):
        d=self.dock;self.prepare_dataset();d.raw_batches=None;d.schema_pending=True
        d.show_pending_guidance()
        self.assertIn('選択したSIMのHarvestから取得',d.status.text())
        shown=[];d.scroll.ensureWidgetVisible=lambda widget,*args:shown.append(widget)
        d.pending_settings_button.click();self.assertEqual(shown,[d.format_combo])

    def test_auto_apply_debounce_no_api_and_preserve_view(self):
        from qgis.PyQt.QtTest import QTest
        from qgis.core import QgsRectangle
        d=self.dock;r=self.prepare_dataset()
        d.client=lambda *_:self.fail('Automatic apply must not call an API')
        count=[];original=d.apply_points
        def apply(*args,**kwargs):count.append(True);return original(*args,**kwargs)
        d.apply_points=apply
        self.iface.canvas.setExtent(QgsRectangle(138,34,140,36));extent=QgsRectangle(self.iface.canvas.extent())
        d.sim_basis_changed(r,'measured');QTest.qWait(200)
        d.sim_basis_changed(r,'received');QTest.qWait(200)
        self.assertEqual(count,[]);self.assertTrue(d.schema_pending)
        QTest.qWait(450)
        self.assertEqual(count,[True]);self.assertFalse(d.schema_pending)
        self.assertEqual(d.resource_apply_state(r),'反映済み')
        self.assertEqual(self.iface.canvas.extent(),extent)
        self.assertIn('自動反映しました',d.status.text())
        self.assertTrue(d.pending_settings_button.isHidden())

    def test_auto_apply_waits_for_task_and_cancels_on_layer_reset(self):
        from qgis.PyQt.QtTest import QTest
        d=self.dock;r=self.prepare_dataset();old=d.layer.id()
        d.task=object();d.sim_basis_changed(r,'measured');QTest.qWait(650)
        self.assertEqual(d.layer.id(),old);self.assertTrue(d.schema_pending)
        d.task=None;QTest.qWait(550);self.assertFalse(d.schema_pending)
        d.sim_basis_changed(r,'received');d.reset_binding();QTest.qWait(650)
        self.assertIsNone(d.layer);self.assertFalse(d.auto_apply_timer.isActive())

    def test_auto_apply_zero_valid_positions_keeps_previous_map(self):
        from qgis.PyQt.QtTest import QTest
        d=self.dock;r=self.prepare_dataset();old=d.layer.id()
        spec=copy.deepcopy(preset('multi'));spec['lat']='missing.latitude'
        d.schema_store['presets']['bad-coordinates']=spec
        d.format_changed(r,'bad-coordinates');QTest.qWait(650)
        self.assertEqual(d.layer.id(),old);self.assertEqual(d.layer.featureCount(),2)
        self.assertTrue(d.schema_pending);self.assertIn('緯度・経度',d.status.text())
        self.assertFalse(d.auto_apply_timer.isActive())

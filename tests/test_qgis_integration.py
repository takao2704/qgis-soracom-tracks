"""Run with the QGIS Python runtime and QT_QPA_PLATFORM=offscreen."""
import json
import tempfile
import unittest
from pathlib import Path
try:
    from qgis.core import QgsApplication,QgsProject,QgsVectorLayer,QgsCoordinateReferenceSystem
    from qgis.gui import QgsMapCanvas
    from qgis.PyQt.QtCore import QObject,pyqtSignal
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.PyQt.QtGui import QPixmap,QColor
    from soracom_tracks.dock import SoracomDock
    HAS_QGIS=True
except ImportError:
    HAS_QGIS=False

if HAS_QGIS:
    class Iface(QObject):
        def __init__(self):
            super().__init__(); self.window=QMainWindow(); self.canvas=QgsMapCanvas(); self.active=None
            self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        def mainWindow(self):return self.window
        def mapCanvas(self):return self.canvas
        def activeLayer(self):return self.active
        def setActiveLayer(self,layer):self.active=layer

@unittest.skipUnless(HAS_QGIS,'Requires QGIS Python')
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Qt permits one application per process, including across test classes.
        cls.app=QgsApplication.instance()
        if cls.app is None:
            cls.app=QgsApplication([],True);cls.app.initQgis()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.iface=Iface();self.dock=SoracomDock(self.iface,self.root/'config')

    def tearDown(self):
        self.dock.shutdown();QgsProject.instance().clear();self.dock.deleteLater()
        self.iface.canvas.deleteLater();self.iface.window.deleteLater()
        self.app.processEvents();self.temp.cleanup()

    def test_save_reopen_image_and_layer_removal(self):
        d=self.dock;d.load_sample()
        self.assertEqual(d.layer.featureCount(),60);self.assertEqual(d.tracks.featureCount(),4)
        d.choose(next(d.layer.getFeatures()))
        image=self.root/'sample.jpg';pix=QPixmap(16,16);pix.fill(QColor('blue'));pix.save(str(image))
        d.pending_camera='SAMPLE';d.image_done({'path':str(image),'requested_ms':d.point['measured_ms'],'cached':True})
        d.persist_layers(str(self.root/'sample.gpkg'))
        self.assertEqual(d.layer.providerType(),'ogr')
        self.assertTrue(d.pixmap and not d.pixmap.isNull())
        project=QgsProject.instance();project.setFileName(str(self.root/'sample.qgz'));self.assertTrue(project.write())
        project.clear();self.iface.active=None
        self.assertIsNone(d.layer)
        self.assertTrue(project.read(str(self.root/'sample.qgz')))
        d.attach_existing(automatic=True)
        self.assertEqual(d.layer.featureCount(),60);self.assertTrue(d.sample)
        self.assertIsNotNone(d.pixmap)
        # Attached layers belong to the user; fetching a new sample must preserve them.
        saved=d.layer.id();d.load_sample();self.assertIsNotNone(project.mapLayer(saved))
        count=len(project.mapLayers());d.load_sample();self.assertEqual(len(project.mapLayers()),count)

    def test_receiver_tracks_import_save_reopen_and_replace(self):
        d=self.dock;project=QgsProject.instance()
        d.load_sample()
        self.assertEqual({x.name() for x in d.track_views},{'RTK・軌跡','CLAS・軌跡'})
        self.assertEqual(sum(x.featureCount() for x in d.track_views),d.tracks.featureCount())
        for view in d.track_views:
            self.assertTrue(project.layerTreeRoot().findLayer(view.id()).isVisible())
            self.assertEqual({f['receiver'] for f in view.getFeatures()},{view.name().split('・')[0]})
        self.assertFalse(project.layerTreeRoot().findLayer(d.tracks.id()).isVisible())
        # Individual visibility survives disk save and a fresh project read.
        hidden=d.track_views[0];project.layerTreeRoot().findLayer(hidden.id()).setItemVisibilityChecked(False)
        hidden_name=hidden.name()
        tree=project.layerTreeRoot();tree.setHasCustomLayerOrder(True);tree.setCustomLayerOrder([d.layer]+d.track_views+[d.tracks])
        d.persist_layers(str(self.root/'receiver_tracks.gpkg'))
        self.assertEqual(len(d.track_views),2)
        self.assertTrue(all(x.providerType()=='ogr' for x in d.track_views))
        self.assertEqual(tree.customLayerOrder()[0].id(),d.layer.id())
        project.setFileName(str(self.root/'receiver_tracks.qgz'));self.assertTrue(project.write())
        project.clear();self.iface.active=None;self.assertTrue(project.read(str(self.root/'receiver_tracks.qgz')))
        d.attach_existing(automatic=True)
        self.assertEqual(len(d.track_views),2)
        for view in d.track_views:
            self.assertEqual(project.layerTreeRoot().findLayer(view.id()).isVisible(),view.name()!=hidden_name)
        saved={x.id() for x in d.track_views}
        d.load_sample();count=len(project.mapLayers());d.load_sample()
        self.assertEqual(count,len(project.mapLayers()))
        self.assertTrue(all(project.mapLayer(x) for x in saved))
        self.assertTrue(all(x.id() in d.owned_ids for x in d.track_views))

    def test_multi_sim_selection_migration_context_and_persistence(self):
        from qgis.PyQt.QtCore import Qt
        d=self.dock
        resources=[dict(resource_type='Subscriber',resource_id=x,name='Tracker '+x) for x in ('SAMPLE-A','SAMPLE-B')]
        d.set_sim_choices(resources,resources)
        d.sim_filter.setText('SAMPLE-A')
        self.assertTrue(d.sim_list.item(1).isHidden())
        self.assertEqual(len(d.selected_resources()),2)  # Filtering must not silently drop selection.
        d.save_settings()
        other=SoracomDock(self.iface,self.root/'config')
        self.assertEqual(len(other.selected_resources()),2)
        other.inputs['coverage'].setText('g')
        self.assertEqual(other.selected_resources(),[])  # Cannot reuse IDs on another coverage/account.
        other.clear_sims();other.save_settings();other.shutdown();other.deleteLater()
        config=json.loads((self.root/'config/settings.json').read_text());config.pop('selected_resources')
        config['resource']='SAMPLE-OLD';config['resource_type']='Sim'
        (self.root/'config/settings.json').write_text(json.dumps(config))
        migrated=SoracomDock(self.iface,self.root/'config')
        self.assertEqual(migrated.selected_resources()[0]['resource_id'],'SAMPLE-OLD')
        self.assertEqual(migrated.selected_resources()[0]['resource_type'],'Sim')
        migrated.shutdown();migrated.deleteLater()

    def test_same_epoch_multi_sim_saved_selection_and_image(self):
        from soracom_tracks.soracom_io import normalize,utc_ms
        base=utc_ms('2026-10-22T00:00:00Z');points=[]
        rows=[dict(time=base+i*5000,content=dict(measured_at=base+i*5000,lat=35,lon=139,
              receiver='RTK',run='same',fix_status='FIX')) for i in range(2)]
        for key in ('SAMPLE-A','SAMPLE-B'):
            current,_=normalize(rows,source=dict(resource_type='Subscriber',resource_id=key,name=key))
            points.extend(current)
        d=self.dock;d.apply_points(points,0,'EPSG:4326',False)
        self.assertEqual(d.layer.featureCount(),4);self.assertEqual(d.tracks.featureCount(),2)
        chosen=next(f for f in d.layer.getFeatures() if f['resource_id']=='SAMPLE-B')
        d.choose(chosen)
        image=self.root/'sample.jpg';pix=QPixmap(16,16);pix.fill(QColor('blue'));pix.save(str(image))
        d.pending_camera='SAMPLE';d.image_done(dict(path=str(image),requested_ms=base,cached=True))
        d.persist_layers(str(self.root/'multi.gpkg'))
        self.assertEqual(d.layer.getFeature(d.point['feature_id'])['resource_id'],'SAMPLE-B')
        self.assertIsNotNone(d.pixmap)
        self.assertEqual(sum(isinstance(f['image_path'],str) and bool(f['image_path']) for f in d.layer.getFeatures()),1)
        project=QgsProject.instance();project.setFileName(str(self.root/'multi.qgz'));self.assertTrue(project.write())
        project.clear();self.iface.active=None;self.assertTrue(project.read(str(self.root/'multi.qgz')))
        d.attach_existing(automatic=True)
        self.assertEqual(d.layer.getFeature(d.point['feature_id'])['resource_id'],'SAMPLE-B')
        self.assertEqual({f['resource_id'] for f in d.tracks.getFeatures()},{'SAMPLE-A','SAMPLE-B'})
        self.assertIsNotNone(d.pixmap)

    def test_camera_selection_request_context_and_persistence(self):
        d=self.dock; d.inputs['profile'].setText('SAMPLE')
        d.set_camera_choices([dict(device_id='CAM-A',name='A',status='オンライン'),
                              dict(device_id='CAM-B',name='B',status='オフライン')])
        self.assertEqual(d.inputs['camera'].text(),'')  # Listing never silently picks a camera.
        d.camera_combo.setCurrentIndex(d.camera_combo.findData('CAM-B'))
        self.assertEqual(d.config()['camera'],'CAM-B')
        d.save_settings(); other=SoracomDock(self.iface,self.root/'config')
        self.assertEqual(other.camera_combo.currentData(),'CAM-B')
        other.shutdown();other.deleteLater()
        d.load_sample();d.choose(next(d.layer.getFeatures()));d.sample=False
        calls=[]
        class Client:
            def image(self,device,stamp,cancel):
                calls.append((device,stamp));return None
        d.client=lambda config:Client()
        d.launch=lambda label,fn,callback:fn(lambda:False)
        d.get_image()
        self.assertEqual(calls, [('CAM-B',int(d.point['measured_ms']))])
        self.assertEqual(d.pending_camera,'CAM-B')
        d.busy(True);self.assertFalse(d.camera_combo.isEnabled());d.busy(False)
        d.inputs['coverage'].setText('g')
        self.assertEqual(d.camera_combo.currentData(),'CAM-B')  # Harvest coverage is independent.
        d.inputs['camera_coverage'].setText('g')
        self.assertEqual(d.inputs['camera'].text(),'');self.assertEqual(d.camera_combo.count(),1)
        d.get_image();self.assertEqual(len(calls),1)  # No export without a camera.
        d.inputs['camera'].setText('MANUAL');self.assertEqual(d.camera_combo.currentData(),'MANUAL')
        d.set_camera_choices([]);self.assertEqual(d.camera_combo.currentData(),'MANUAL')
        d.inputs['profile'].setText('OTHER');self.assertEqual(d.inputs['camera'].text(),'')

    def test_camera_switch_clears_old_image(self):
        d=self.dock;d.load_sample();f=next(d.layer.getFeatures());d.choose(f)
        path=self.root/'camera.jpg';pix=QPixmap(16,16);pix.fill(QColor('blue'));pix.save(str(path))
        d.inputs['camera'].setText('CAM-A');d.pending_camera='CAM-A'
        d.image_done(dict(path=str(path),requested_ms=d.point['measured_ms'],cached=True))
        self.assertIsNotNone(d.pixmap)
        d.inputs['camera'].setText('CAM-B');self.assertIsNone(d.pixmap)
        d.choose(d.layer.getFeature(f.id()));self.assertIsNone(d.pixmap)
        d.inputs['camera'].setText('CAM-A');d.choose(d.layer.getFeature(f.id()))
        self.assertIsNotNone(d.pixmap)

    def test_settings_and_uncertain_task_detachment(self):
        d=self.dock;d.inputs['profile'].setText('SAMPLE');d.offset.setValue(1.25);d.save_settings()
        other=SoracomDock(self.iface,self.root/'config')
        self.assertEqual(other.config()['profile'],'SAMPLE');self.assertEqual(other.offset.value(),1.25)
        other.shutdown();other.deleteLater()
        class Pending:
            cancelled=False
            done=None
            def cancel(self):self.cancelled=True
        pending=Pending();d.task=pending;d.reset_binding()
        self.assertTrue(pending.cancelled);self.assertIsNone(d.task)
        pending.done(None,None)  # Cannot call a deleted dock after unload/project close.

    def test_map_click_fetch_cache_offset_and_offline(self):
        d=self.dock;d.load_sample();d.sample=False;d.inputs['camera'].setText('CAM-DEMO')
        features=list(d.layer.getFeatures());f=features[0]
        image=self.root/'click.jpg';pix=QPixmap(16,16);pix.fill(QColor('blue'));pix.save(str(image))
        calls=[]
        class Client:
            def image(self,device,stamp,cancel):
                calls.append((device,stamp));return dict(path=str(image),requested_ms=stamp,cached=False)
        d.client=lambda config:Client()
        d.launch=lambda label,fn,callback:callback(fn(lambda:False))
        d.choose(f);self.assertEqual(calls,[])  # Programmatic restore never exports.
        d.tool.featureIdentified.emit(f)
        self.assertEqual(calls,[('CAM-DEMO',f['measured_ms'])]);self.assertIsNotNone(d.pixmap)
        d.tool.featureIdentified.emit(d.layer.getFeature(f.id()))
        self.assertEqual(len(calls),1)  # Explicit click uses stored image without API.
        d.offset.setValue(2);d.tool.featureIdentified.emit(d.layer.getFeature(f.id()))
        self.assertEqual(calls[-1],('CAM-DEMO',f['measured_ms']+2000))
        d.auto_image.setChecked(False);d.tool.featureIdentified.emit(features[1])
        self.assertEqual(len(calls),2);self.assertIsNone(d.pixmap)
        d.tool.featureIdentified.emit(d.layer.getFeature(f.id()))
        self.assertIsNotNone(d.pixmap);self.assertEqual(len(calls),2)
        d.save_settings();other=SoracomDock(self.iface,self.root/'config')
        self.assertFalse(other.auto_image.isChecked());other.shutdown();other.deleteLater()

    def test_click_during_request_and_camera_switch(self):
        d=self.dock;d.load_sample();d.sample=False;d.inputs['camera'].setText('CAM-A')
        features=list(d.layer.getFeatures());d.choose(features[0])
        class Pending:
            def cancel(self):pass
        d.task=Pending();d.tool.featureIdentified.emit(features[1])
        self.assertEqual(d.point['feature_id'],features[0].id());d.task=None
        calls=[];d.get_image=lambda:calls.append(d.point['feature_id'])
        d.inputs['camera'].setText('CAM-B');d.tool.featureIdentified.emit(features[1])
        self.assertEqual(calls,[features[1].id()]);self.assertIsNone(d.pixmap)

    def test_sample_click_never_uses_client_and_attach_never_exports(self):
        d=self.dock;d.load_sample();d.client=lambda _:self.fail('Sample must not call API')
        d.tool.featureIdentified.emit(next(d.layer.getFeatures()))
        self.assertIsNotNone(d.pixmap);self.assertIn('APIは未呼び出し',d.status.text())
        d.sample=False;d.layer.setCustomProperty('soracom/sample',False)
        d.attach_existing(automatic=True)  # No request when opening an existing layer.

    def test_video_click_range_cache_switch_and_no_request_on_attach(self):
        from unittest.mock import patch
        d=self.dock;d.load_sample();d.sample=False;d.inputs['profile'].setText('sample');d.inputs['camera'].setText('CAM-A')
        feature=next(d.layer.getFeatures());d.offset.setValue(10)
        d.media_mode.setCurrentIndex(d.media_mode.findData('video'));d.video_before.setValue(3);d.video_after.setValue(7)
        calls=[];shown=[]
        def launch(label,fn,done):done(fn(lambda:False))
        d.launch=launch;d.show_video=lambda result,autoplay=False:shown.append((result,autoplay))
        def exported(client,device,start,end,cancel):
            calls.append((device,start,end));return dict(path='video.mp4',from_ms=start,to_ms=end,cached=False)
        with patch('soracom_tracks.camera_media.export_video',side_effect=exported),patch('soracom_tracks.camera_media.cached_video',return_value=None):
            d.choose(feature);self.assertEqual(calls,[])
            d.point_clicked(feature)
            self.assertEqual(calls,[('CAM-A',feature['measured_ms']+7000,feature['measured_ms']+17000)])
            self.assertTrue(shown[-1][1])
            d.auto_image.setChecked(False);d.point_clicked(feature);self.assertEqual(len(calls),1)
            d.auto_image.setChecked(True);d.video_before.setValue(0);d.video_after.setValue(0);d.point_clicked(feature);self.assertEqual(len(calls),1)
            self.assertIn('1秒以上',d.status.text())
            d.sample=True;d.point_clicked(feature);self.assertEqual(len(calls),1)
        d.media_mode.setCurrentIndex(0);self.assertIsNone(d.video_path);self.assertFalse(d.video_panel.isVisible())

    def test_windows_video_fallback_controls_and_cleanup(self):
        from unittest.mock import patch
        class Fallback(QObject):
            durationChanged=pyqtSignal(int);positionChanged=pyqtSignal(int)
            stateChanged=pyqtSignal(int);error=pyqtSignal(int)
            StoppedState,PlayingState,PausedState=0,1,2
            def __init__(self,parent):super().__init__(parent);self.current=0;self.ms=0;self.closed=False
            def setVideoOutput(self,widget):self.widget=widget
            def set_source(self,path):self.path=path;self.durationChanged.emit(14000)
            def play(self):self.current=1;self.stateChanged.emit(1)
            def pause(self):self.current=2;self.stateChanged.emit(2)
            def stop(self):self.current=0;self.stateChanged.emit(0)
            def state(self):return self.current
            def position(self):return self.ms
            def duration(self):return 14000
            def setPosition(self,ms):self.ms=ms;self.positionChanged.emit(ms)
            def close(self):self.closed=True;self.stop()
        d=self.dock;d.video_path=str(self.root/'clip.mp4');d._video_autoplay=True
        with patch('soracom_tracks.camera_media.sys.platform','win32'),patch('soracom_tracks.vlc_player.VlcPlayer',Fallback):
            d.video_error()
            self.assertIsInstance(d.player,Fallback)
            self.assertEqual(d.player.state(),1);self.assertEqual(d.video_play.text(),'一時停止')
            d.toggle_video();self.assertEqual(d.player.state(),2);self.assertEqual(d.video_play.text(),'再生')
            d.video_seek.setValue(8000)
            self.assertEqual(d.player.position(),8000);self.assertEqual(d.video_time.text(),'00:08 / 00:14')
            # Playback progress must not feed back into another seek.
            with patch.object(d.player,'setPosition') as seek:
                d.video_position_changed(9000);seek.assert_not_called()
            d.toggle_video();self.assertEqual(d.player.state(),1)
            d.video_error();self.assertIsInstance(d.player,Fallback)  # No repeated backend creation.
            d.close_video_player();self.assertTrue(d.player.closed)

    def test_video_duration_reset_does_not_seek_and_macos_keeps_qt(self):
        from unittest.mock import patch
        d=self.dock;player=d.player
        d.video_seek.setRange(0,14000);d.video_seek.setValue(9000)
        with patch.object(player,'setPosition') as seek:
            d.video_duration_changed(0)
            seek.assert_not_called()
        d.video_path='clip.mp4'
        with patch('soracom_tracks.camera_media.sys.platform','darwin'),\
             patch('soracom_tracks.vlc_player.VlcPlayer') as vlc:
            d.video_error();vlc.assert_not_called()
        self.assertIs(d.player,player)
        self.assertNotIn('VLC',d.status.text())
        import sys
        self.assertEqual(d.vlc_settings.isHidden(),sys.platform!='win32')
        self.assertEqual(d.vlc_settings.parentWidget().title(),'地点と映像')

    def test_vlc_settings_validate_save_reset_and_cancel(self):
        from unittest.mock import patch
        from qgis.PyQt.QtCore import QSettings
        from qgis.PyQt.QtWidgets import QLineEdit,QDialogButtonBox
        settings=QSettings(str(self.root/'vlc.ini'),QSettings.IniFormat)
        key='soracom_tracks/vlc_directory';settings.setValue(key,'previous')
        d=self.dock
        with patch('soracom_tracks.camera_media.QSettings',return_value=settings) as factory:
            factory.NoError=QSettings.NoError
            d.open_vlc_settings();dialog=d.vlc_dialog
            field=dialog.findChild(QLineEdit);buttons=dialog.findChild(QDialogButtonBox)
            field.setText(str(self.root/'missing'));buttons.button(QDialogButtonBox.Save).click()
            self.assertIs(d.vlc_dialog,dialog);self.assertEqual(settings.value(key),'previous')
            valid=self.root/'VLC 日本語';(valid/'plugins').mkdir(parents=True);(valid/'libvlc.dll').touch()
            field.setText(str(valid));buttons.button(QDialogButtonBox.Save).click()
            self.assertIsNone(d.vlc_dialog);self.assertEqual(settings.value(key),str(valid))
            d.open_vlc_settings();d.vlc_dialog.findChild(QLineEdit).clear()
            d.vlc_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).click()
            self.assertEqual(settings.value(key),str(valid))
            d.open_vlc_settings();d.vlc_dialog.findChild(QLineEdit).clear()
            d.vlc_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
            self.assertFalse(settings.contains(key));self.assertIsNone(d.vlc_dialog)

    def test_cached_video_lookup_after_logout_never_authenticates_or_exports(self):
        from unittest.mock import patch
        d=self.dock;d.load_sample();d.sample=False
        d.inputs['profile'].setText('sample');d.inputs['camera'].setText('CAM-A')
        d.media_mode.setCurrentIndex(1)
        d.auth_mode.setCurrentIndex(d.auth_mode.findData('session'));d.api_session=None
        d.inputs['profile'].setText('sample');d.inputs['camera'].setText('CAM-A')
        result=dict(path='cached.mp4',from_ms=1,to_ms=2,cached=True)
        with patch.object(d,'api_runner',side_effect=AssertionError('must not authenticate')),\
             patch('soracom_tracks.camera_media.cached_video',return_value=result),\
             patch.object(d,'show_video') as show,\
             patch('soracom_tracks.camera_media.export_video',side_effect=AssertionError('must not export')):
            self.assertTrue(d.choose(next(d.layer.getFeatures())),d.status.text())
            show.assert_called_once_with(result,False)

    def test_media_default_migrates_legacy_and_remembers_explicit_optout(self):
        cfg=self.root/'migration';cfg.mkdir();(cfg/'settings.json').write_text(json.dumps({'auto_image':False}))
        d=SoracomDock(self.iface,cfg)
        try:
            self.assertTrue(d.auto_image.isChecked());self.assertEqual(d.media_mode.currentData(),'image')
            d.auto_image.setChecked(False);d.media_mode.setCurrentIndex(1);d.video_before.setValue(8);d.video_after.setValue(12)
        finally:d.shutdown();d.deleteLater()
        d=SoracomDock(self.iface,cfg)
        try:
            self.assertFalse(d.auto_image.isChecked());self.assertEqual(d.media_mode.currentData(),'video')
            self.assertEqual((d.video_before.value(),d.video_after.value()),(8,12))
        finally:d.shutdown();d.deleteLater()

if __name__=='__main__':unittest.main()

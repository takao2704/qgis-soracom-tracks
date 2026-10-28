"""QGIS dock for Harvest positions and time-linked SoraCam images."""
from pathlib import Path
import json
import shutil
import uuid
from datetime import datetime, timezone, timedelta
from .schema_ui import SchemaUI
from .compact_ui import CompactUI, StatusLabel
from .soracom_io import SoracomClient, DemoError, normalize, segments, utc_ms, utc_text, sample_entries, atomic_json
from qgis.core import (QgsApplication, QgsTask, QgsProject, QgsVectorLayer, QgsFeature,
                       QgsGeometry, QgsPointXY, QgsField, QgsMarkerSymbol, QgsLineSymbol,
                       QgsRendererCategory, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsVectorFileWriter, QgsEditorWidgetSetup)
from qgis.gui import QgsMapToolIdentifyFeature
from qgis.PyQt.QtCore import Qt, QVariant, QEvent
from qgis.PyQt.QtGui import QPixmap, QColor, QPainter, QPen, QFont
from qgis.PyQt.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QLabel, QComboBox, QDoubleSpinBox, QScrollArea, QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QCheckBox)


def make_layers(points, crs, gap_ms=5000, sample=False):
    if not QgsCoordinateReferenceSystem(crs).isValid():
        raise DemoError('座標参照系が不正です')
    name = 'SAMPLE・架空走行' if sample else ('Harvest・架空走行（API動作確認）' if points and all(p.get('synthetic') for p in points) else 'Harvest・走行ログ')
    layer = QgsVectorLayer('Point?crs='+crs, name, 'memory')
    provider = layer.dataProvider()
    fields = [('measured_ms', QVariant.LongLong), ('measured_at', QVariant.String),
              ('source_key', QVariant.String), ('resource_id', QVariant.String), ('resource_type', QVariant.String),
              ('coverage', QVariant.String), ('sim_name', QVariant.String), ('event_id', QVariant.String),
              ('received_at', QVariant.String), ('receiver', QVariant.String), ('run', QVariant.String),
              ('raw_entry_json', QVariant.String), ('position_valid', QVariant.Bool),
              ('fix_status', QVariant.String), ('synthetic', QVariant.Bool), ('time_basis', QVariant.String),
              ('received_ms', QVariant.LongLong), ('process_ms', QVariant.LongLong), ('process_at', QVariant.String),
              ('time_error', QVariant.String), ('value_status_json', QVariant.String), ('extra_values_json', QVariant.String),
              ('image_path', QVariant.String), ('image_requested_at', QVariant.String), ('camera_id', QVariant.String)]
    definitions={}
    for point in points:definitions.update(point.get('extra_definitions',{}))
    types={'number':QVariant.Double,'string':QVariant.String,'boolean':QVariant.Bool}
    fields += [(key,types[v['type']]) for key,v in definitions.items()]
    provider.addAttributes([QgsField(n,t) for n,t in fields]); layer.updateFields()
    for key,v in definitions.items():layer.setFieldAlias(layer.fields().indexFromName(key),v['name']+(' ('+v['unit']+')' if v.get('unit') else ''))
    layer.setCustomProperty('soracom/value_definitions',json.dumps(definitions,ensure_ascii=False))
    features=[]
    for p in points:
        f=QgsFeature(layer.fields()); f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(p['lon'], p['lat'])))
        data=dict(p);data.setdefault('process_ms',p.get('measured_ms'));data.setdefault('process_at',p.get('measured_at',''));data.update(p.get('extra_values',{}))
        f.setAttributes([data.get(n,None if t!=QVariant.String else '') for n,t in fields]); features.append(f)
    provider.addFeatures(features); layer.updateExtents()
    layer.setEditorWidgetSetup(layer.fields().indexFromName('image_path'),QgsEditorWidgetSetup('ExternalResource',{'DocumentViewer':1,'DocumentViewerHeight':270}))
    colors={'FIX':'#008779','FLOAT':'#e79b32','SPS':'#1978b8','DGPS':'#8064a2','DR':'#b64b4b','unknown':'#888888'}
    labels={'FIX':'FIX（整数解）','FLOAT':'FLOAT（浮動解）','SPS':'SPS（単独測位）',
            'DGPS':'DGPS（差分測位）','DR':'DR（推測航法）','unknown':'不明（品質未確認）','RMC VALID':'RMC有効（品質未確認）'}
    categories=[]
    for status in sorted({p['fix_status'] for p in points}):
        symbol=QgsMarkerSymbol.createSimple({'name':'circle','color':colors.get(status,'#888888'),'size':'3.2','outline_color':'white','outline_width':'0.4'})
        categories.append(QgsRendererCategory(status,symbol,labels.get(status,status+'（品質未分類）')))
    layer.setRenderer(QgsCategorizedSymbolRenderer('fix_status',categories))
    tracks=QgsVectorLayer('LineString?crs='+crs, name+'・軌跡', 'memory')
    track_fields=['receiver','run','source_key','resource_id','resource_type','coverage','sim_name','track_label']
    dp=tracks.dataProvider(); dp.addAttributes([QgsField(n,QVariant.String) for n in track_fields]); tracks.updateFields()
    features=[]
    for group in segments(points,gap_ms):
        p=group[0]
        label=p['receiver'] + (' / '+p.get('sim_name',p['resource_id'])+' …'+p['resource_id'][-6:] if p.get('resource_id') else '')
        f=QgsFeature(tracks.fields()); f.setAttributes([label if n=='track_label' else p.get(n,'') for n in track_fields])
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(p['lon'],p['lat']) for p in group])); features.append(f)
    dp.addFeatures(features); tracks.updateExtents()
    categories=[]
    for i,label in enumerate(sorted({f['track_label'] for f in features})):
        receiver=next(f['receiver'] for f in features if f['track_label']==label)
        color={'RTK':'#1978b8','CLAS':'#c27a25'}.get(receiver,['#1978b8','#c27a25','#777777'][i%3])
        symbol=QgsLineSymbol.createSimple({'line_color':color,'line_width':'0.8'})
        categories.append(QgsRendererCategory(label,symbol,label))
    tracks.setRenderer(QgsCategorizedSymbolRenderer('track_label',categories))
    layer.setCustomProperty('soracom/sample',sample)
    return layer,tracks


def make_records(records, name):
    """Audit table: retain every original entry, including duplicates and invalid positions."""
    layer=QgsVectorLayer('None',name+'・全受信記録','memory')
    fields=[('receiver',QVariant.String),('run',QVariant.String),('fix_status',QVariant.String),
            ('measured_at',QVariant.String),('received_at',QVariant.String),('resource_id',QVariant.String),
            ('position_valid',QVariant.Bool),('disposition',QVariant.String),('raw_entry_json',QVariant.String),
            ('source_key',QVariant.String),('process_at',QVariant.String),('time_basis',QVariant.String),
            ('reason',QVariant.String),('time_error',QVariant.String),('value_status_json',QVariant.String),('extra_values_json',QVariant.String)]
    definitions={}
    for record in records:definitions.update(record.get('extra_definitions',{}))
    types={'number':QVariant.Double,'string':QVariant.String,'boolean':QVariant.Bool}
    fields += [(key,types[v['type']]) for key,v in definitions.items()]
    layer.dataProvider().addAttributes([QgsField(n,t) for n,t in fields]);layer.updateFields()
    features=[]
    for record in records:
        feature=QgsFeature(layer.fields());data={**record,**record.get('extra_values',{})};feature.setAttributes([data.get(n,None if t!=QVariant.String else '') for n,t in fields]);features.append(feature)
    layer.dataProvider().addFeatures(features)
    for key,v in definitions.items():layer.setFieldAlias(layer.fields().indexFromName(key),v['name']+(' ('+v['unit']+')' if v.get('unit') else ''))
    return layer


class Work(QgsTask):
    def __init__(self, label, fn, done):
        super().__init__(label,QgsTask.CanCancel); self.fn=fn; self.done=done; self.value=None; self.error=None
    def run(self):
        try:
            self.value=self.fn(self.isCanceled)
            return not self.isCanceled()
        except DemoError as e:
            self.error=str(e); return False
        except Exception:
            self.error='処理に失敗しました。入力と応答形式を確認してください'; return False
    def finished(self, ok):
        self.done(self.value,self.error or (None if ok else '中止しました'))
        self.value=None


from .camera_bindings import CameraBindings
from .camera_media import CameraMedia
from .auth_ui import AuthUI


class SoracomDock(QDockWidget, CompactUI, SchemaUI, CameraBindings, CameraMedia, AuthUI):
    def __init__(self, iface, storage=None):
        super().__init__('GNSS Tracks for SORACOM | 走行ログと映像',iface.mainWindow())
        self.iface=iface; self.layer=None; self.tracks=None; self.records=None; self.task=None; self.tool=None; self.sample=True
        self.point=None; self.pixmap=None; self.source_config=None
        self.storage=Path(storage) if storage else Path(QgsApplication.qgisSettingsDirPath())/'soracom_tracks'
        self.storage.mkdir(parents=True,exist_ok=True)
        self.init_schema_state()
        self.cache_dir=self.storage/'cache'
        self.owned_ids=set()
        self.track_views=[]
        self.setObjectName('SoracomTracksDock'); self.setMinimumWidth(410)
        self.setStyleSheet('QPushButton, QComboBox { color: palette(window-text); }')
        self.setAttribute(Qt.WA_DeleteOnClose,False)
        widget=QWidget(); layout=QVBoxLayout(widget); layout.setAlignment(Qt.AlignTop); form=QFormLayout()
        from qgis.PyQt.QtWidgets import QGroupBox,QSizePolicy
        self.settings_box=QGroupBox('接続設定（開く／閉じる）'); self.settings_box.setCheckable(True); self.settings_box.setChecked(False); self.settings_box.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Maximum)
        self.settings_body=QWidget(); self.settings_body.setLayout(form); self.settings_body.hide()
        box_layout=QVBoxLayout(self.settings_box); box_layout.addWidget(self.settings_body)
        self.settings_box.toggled.connect(self.settings_body.setVisible); layout.addWidget(self.settings_box)
        now=datetime.now(timezone.utc).replace(microsecond=0)
        cli=shutil.which('soracom') or next((str(p) for p in (Path('/opt/homebrew/bin/soracom'),Path('/usr/local/bin/soracom')) if p.is_file()),'soracom')
        self.inputs={}
        defaults=[('cli','CLI',cli),('profile','プロファイル',''),
                  ('coverage','Harvestカバレッジ（jp/g）','jp'),('camera_coverage','カメラカバレッジ（jp/g）','jp'),
                  ('resource','手動追加用 IMSI / SIM ID',''),('start','取得開始',(now-timedelta(minutes=10)).isoformat()),
                  ('end','取得終了',now.isoformat()),('run_filter','走行ID（任意）',''),('camera','カメラID（手動指定可）',''),
                  ('crs','ログのCRS','EPSG:4326')]
        for key,label,value in defaults:
            edit=QLineEdit(value); self.inputs[key]=edit
            edit.hide()
        self.build_auth_ui(form)
        self.resource_type=QComboBox(); self.resource_type.addItems(['Subscriber','Sim'])
        self.add_sim_button=QPushButton('入力したIDをSIM選択に追加'); self.add_sim_button.clicked.connect(self.add_manual_sim)
        self.offset=QDoubleSpinBox(); self.offset.setRange(-3600,3600); self.offset.setDecimals(3); self.offset.setSuffix(' 秒')
        self.gap=QDoubleSpinBox(); self.gap.setRange(0.1,3600); self.gap.setValue(5); self.gap.setSuffix(' 秒')
        self.mapping=QLineEdit('measured_at,lat,lon,receiver,run,fix_status'); self.mapping.hide()
        self.settings_save=QPushButton('接続設定をこのPCに保存'); form.addRow(self.settings_save); self.settings_save.clicked.connect(self.save_settings)
        self.sim_box=QGroupBox('取得するSIM（複数選択）'); self.sim_box.setCheckable(True); self.sim_box.setChecked(True)
        sim_outer=QVBoxLayout(self.sim_box); self.sim_body=QWidget(); sim_layout=QVBoxLayout(self.sim_body);sim_layout.setContentsMargins(0,0,0,0);sim_outer.addWidget(self.sim_body)
        self.sim_box.toggled.connect(self.sim_body.setVisible)
        row=QHBoxLayout(); self.list_button=QPushButton('SIM一覧を取得'); self.clear_button=QPushButton('選択を解除')
        row.addWidget(self.list_button); row.addWidget(self.clear_button); sim_layout.addLayout(row)
        self.list_button.clicked.connect(self.refresh_sims); self.clear_button.clicked.connect(self.clear_sims)
        self.sim_filter=QLineEdit(); self.sim_filter.setPlaceholderText('SIM名・IDで絞り込み'); self.sim_filter.setAccessibleName('SIM名・IDで絞り込み'); self.sim_filter.setClearButtonEnabled(True); sim_layout.addWidget(self.sim_filter)
        self.sim_list=QListWidget(); self.sim_list.setMinimumHeight(85); self.sim_list.setMaximumHeight(130); sim_layout.addWidget(self.sim_list)
        self.sim_filter_count=QLabel(); sim_filter_tip='検索で隠れたSIMも選択を保持します。'; self.sim_filter.setToolTip(sim_filter_tip); sim_layout.addWidget(self.sim_filter_count)
        self.sim_count=QLabel('0 SIM選択 / 最大20'); self.sim_count.setWordWrap(True); sim_outer.addWidget(self.sim_count)
        self.sim_list.itemChanged.connect(self.update_sim_count); self.sim_filter.textChanged.connect(self.filter_sims)
        manual=QGroupBox('SIMをIDで探す');manual.setCheckable(True);manual.setChecked(False)
        manual_body=QWidget();manual_form=QFormLayout(manual_body)
        manual_form.addRow('IDの種類',self.resource_type);manual_form.addRow('IMSI / SIM ID',self.inputs['resource']);manual_form.addRow(self.add_sim_button)
        manual_outer=QVBoxLayout(manual);manual_outer.addWidget(manual_body);manual_body.hide();manual.toggled.connect(manual_body.setVisible);sim_layout.addWidget(manual)
        self.inputs['resource'].show()
        layout.addWidget(self.sim_box)
        self.build_schema_ui(layout)
        self.attach_button=QPushButton('既存の位置レイヤを使用'); layout.addWidget(self.attach_button); self.attach_button.clicked.connect(self.attach_existing)
        self.period_box=QGroupBox('取得期間（Harvestへのデータ到達時刻）'); period_layout=QVBoxLayout(self.period_box);period_form=QFormLayout()
        period_form.addRow('開始',self.inputs['start']);period_form.addRow('終了',self.inputs['end']);period_layout.addLayout(period_form)
        self.inputs['start'].setAccessibleName('Harvest到達時刻の取得開始');self.inputs['end'].setAccessibleName('Harvest到達時刻の取得終了')
        tip=QLabel('UTCまたは時差付きで指定・最大24時間。処理に使う時刻を切り替えても、この取得範囲は変わりません。');tip.setWordWrap(True);period_layout.addWidget(tip)
        data_options=QGroupBox('走行ログの詳細設定');data_options.setCheckable(True);data_options.setChecked(False)
        options_body=QWidget();options_form=QFormLayout(options_body)
        options_form.addRow('走行ID（任意）',self.inputs['run_filter']);options_form.addRow('ログのCRS',self.inputs['crs']);options_form.addRow('軌跡を分ける欠測幅',self.gap)
        options_outer=QVBoxLayout(data_options);options_outer.addWidget(options_body);options_body.hide();data_options.toggled.connect(options_body.setVisible);period_layout.addWidget(data_options)
        self.inputs['run_filter'].show();self.inputs['crs'].show();self.inputs['start'].show();self.inputs['end'].show()
        self.load_button=QPushButton('選択したSIMのHarvestから取得'); period_layout.addWidget(self.load_button); self.load_button.clicked.connect(self.load_live);layout.addWidget(self.period_box)
        self.sample_button=QPushButton('サンプルを表示（API呼び出しなし）'); layout.addWidget(self.sample_button); self.sample_button.clicked.connect(self.load_sample)
        self.camera_box=QGroupBox('共通カメラ（SIMで個別指定がない場合）'); camera_layout=QVBoxLayout(self.camera_box)
        self.camera_list_button=QPushButton('カメラ一覧を取得'); camera_layout.addWidget(self.camera_list_button)
        self.camera_filter=QLineEdit(); self.camera_filter.setPlaceholderText('カメラ名・IDで絞り込み'); self.camera_filter.setAccessibleName('共通カメラの検索'); self.camera_filter.setClearButtonEnabled(True); camera_layout.addWidget(self.camera_filter)
        self.camera_combo=QComboBox(); self.camera_combo.setMinimumContentsLength(20)
        self.camera_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.camera_combo.addItem('カメラを選択してください',None); camera_layout.addWidget(self.camera_combo)
        self.camera_filter_count=QLabel(); self.camera_filter_count.setWordWrap(True); camera_layout.addWidget(self.camera_filter_count)
        self.camera_filter.textChanged.connect(self.filter_cameras)
        camera_details=QGroupBox('カメラの詳細設定');camera_details.setCheckable(True);camera_details.setChecked(False)
        camera_body=QWidget();camera_form=QFormLayout(camera_body);camera_form.addRow('カメラID（手動指定）',self.inputs['camera']);camera_form.addRow('映像要求時刻の補正',self.offset)
        camera_outer=QVBoxLayout(camera_details);camera_outer.addWidget(camera_body);camera_body.hide();camera_details.toggled.connect(camera_body.setVisible);camera_layout.addWidget(camera_details);self.inputs['camera'].show()
        layout.addWidget(self.camera_box)
        self.camera_list_button.clicked.connect(self.refresh_cameras)
        self.camera_combo.currentIndexChanged.connect(self.camera_selected)
        self.build_media_controls(layout)
        row=QHBoxLayout(); self.pick_button=QPushButton('地図上の点を選択'); self.image_button=QPushButton('選んだ時刻の画像を取得'); row.addWidget(self.pick_button); row.addWidget(self.image_button); layout.addLayout(row)
        self.pick_button.clicked.connect(self.pick); self.image_button.clicked.connect(self.get_image)
        self.auto_image=QCheckBox('点クリックで映像を取得（未保存時はAPI）'); self.auto_image.setChecked(True)
        self.auto_image.setToolTip('初期設定はON。OFFにすると保存済みの静止画・動画だけを表示します。サンプルはAPIを呼びません。')
        layout.addWidget(self.auto_image)
        self.camera_target=QLabel();self.camera_target.setWordWrap(True);self.camera_target.setTextFormat(Qt.PlainText);self.camera_target.setAccessibleName('この地点の連携カメラ');layout.addWidget(self.camera_target)
        footer_row=QHBoxLayout(); self.save_button=QPushButton('GeoPackageに保存'); self.cancel_button=QPushButton('処理を中止'); footer_row.addWidget(self.save_button); footer_row.addWidget(self.cancel_button)
        self.save_button.clicked.connect(self.save_layers); self.cancel_button.clicked.connect(self.cancel)
        self.status=StatusLabel('サンプルまたはHarvestから位置情報を読み込んでください'); self.status.setWordWrap(True);self.status.setAccessibleName('処理結果と次の操作');self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.selected=StatusLabel('地点未選択'); self.selected.setWordWrap(True); layout.addWidget(self.selected)
        self.image_label=QLabel('選んだ地点の画像を表示'); self.image_label.setMinimumHeight(200); self.image_label.setAlignment(Qt.AlignCenter); self.image_label.setStyleSheet('background:#102c40;color:white;'); layout.addWidget(self.image_label)
        self.build_video_player(layout)
        note=QLabel('静止画・動画は1件ずつ取得し、このPCに保存します。\n新規取得は静止画1枚で1秒、動画は取得した秒数のエクスポート枠を消費します。動画は前後各450秒まで。\n要求時刻・範囲は実際の録画フレームを保証しません。'); note.setWordWrap(True); layout.addWidget(note)
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setWidget(widget)
        panel=QWidget();panel_layout=QVBoxLayout(panel);panel_layout.setContentsMargins(0,0,0,0);panel_layout.addWidget(self.scroll,1)
        footer=QWidget();footer_layout=QVBoxLayout(footer);footer_layout.addWidget(self.status)
        self.pending_settings_button=QPushButton('未適用の設定へ移動');self.pending_settings_button.setAccessibleName('未適用のデータ形式・時刻設定へ移動');self.pending_settings_button.setToolTip('問題があるSIMの設定を開きます。修正すると自動で再試行します。');self.pending_settings_button.clicked.connect(self.go_to_pending_settings);self.pending_settings_button.hide();footer_layout.addWidget(self.pending_settings_button)
        footer_layout.addLayout(footer_row);panel_layout.addWidget(footer);self.setWidget(panel)
        self.busy(False)
        self.offset.valueChanged.connect(self.image_time_changed)
        config={}
        settings=self.storage/'settings.json'
        if settings.exists():
            try: config=json.loads(settings.read_text())
            except (ValueError,OSError): config={}; self.status.setText('設定ファイルを読めません。接続設定を入力してください')
            for key,edit in self.inputs.items():
                if key in config:edit.setText(str(config[key]))
            self.resource_type.setCurrentText(config.get('resource_type','Subscriber'))
            self.gap.setValue(config.get('gap_seconds',5))
            self.offset.setValue(config.get('offset_seconds',0))
            self.auto_image.setChecked(config.get('auto_media',True))
            self.media_mode.setCurrentIndex(max(0,self.media_mode.findData(config.get('media_mode','image'))))
            self.video_before.setValue(int(config.get('video_before_seconds',5)))
            self.video_after.setValue(int(config.get('video_after_seconds',5)))
            self.mapping.setText(config.get('mapping',self.mapping.text()))
            self.cache_dir=Path(config.get('cache_dir',str(self.cache_dir)))
            # Migrate the original single-resource setting once, without an API request.
            resources=config.get('selected_resources')
            if resources is None:
                resources=[dict(resource_type=self.resource_type.currentText(),resource_id=config['resource'],name=config['resource'])] if config.get('resource') else []
            self.set_sim_choices(resources,resources)
            if not self.schema_path.exists(): self.migrate_formats(resources)
        self.restore_auth(config)
        self.media_changed()
        for signal in (self.auto_image.toggled,self.media_mode.currentIndexChanged,self.video_before.valueChanged,self.video_after.valueChanged):signal.connect(self.save_media_preferences)
        self.render_formats()
        self.sync_camera_id()
        self.inputs['camera'].textChanged.connect(self.sync_camera_id)
        for key in ('profile','camera_coverage'):
            self.inputs[key].textChanged.connect(self.clear_camera_context)
        for key in ('profile','coverage'):
            self.inputs[key].textChanged.connect(self.clear_sim_context)
        self.build_compact_ui()
        QgsProject.instance().layersWillBeRemoved.connect(self.layers_removing)

    def busy(self,value):
        for b in (self.sample_button,self.load_button,self.image_button,self.save_button,self.pick_button,self.attach_button): b.setEnabled(not value)
        self.cancel_button.setEnabled(value)
        self.settings_box.setEnabled(not value)
        self.period_box.setEnabled(not value)
        self.sim_box.setEnabled(not value)
        self.camera_box.setEnabled(not value)
        self.auto_image.setEnabled(not value)
        self.media_mode.setEnabled(not value);self.video_range.setEnabled(not value)
        self.pending_settings_button.setEnabled(not value)
        self.format_box.setEnabled(not value);self.time_basis.setEnabled(not value);self.reprocess_button.setEnabled(not value)
        if hasattr(self,'compact_busy'):self.compact_busy(value)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.StyleChange,QEvent.FontChange) and hasattr(self,'time_basis'):
            self.render_formats()

    def set_camera_choices(self, cameras):
        selected=self.inputs['camera'].text().strip()
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear(); self.camera_combo.addItem('カメラを選択してください',None)
        for camera in cameras:
            self.camera_combo.addItem(f"{camera['name']} / {camera['status']} / {camera['device_id']}",camera['device_id'])
        if selected and self.camera_combo.findData(selected)<0:
            self.camera_combo.addItem('手動指定・一覧未確認 / '+selected,selected)
        self.camera_combo.setCurrentIndex(max(0,self.camera_combo.findData(selected)))
        self.camera_combo.blockSignals(False)
        self.filter_cameras();self.render_formats();self.update_camera_target()

    def filter_cameras(self, *_):
        query=self.camera_filter.text().strip().casefold()
        visible=0
        for i in range(self.camera_combo.count()):
            match=not i or query in self.camera_combo.itemText(i).casefold()
            self.camera_combo.view().setRowHidden(i,not match)
            self.camera_combo.model().item(i).setEnabled(match)
            if i and match:visible+=1
        total=self.camera_combo.count()-1
        message=f"{visible} / {total} 台を表示" if visible or not query else "一致するカメラがありません。検索条件を変えてください。"
        current=self.camera_combo.currentIndex()
        if current>0 and self.camera_combo.view().isRowHidden(current):message+=" 選択中のカメラは検索対象外ですが、選択を保持しています。"
        self.camera_filter_count.setText(message)

    def sync_camera_id(self, *_):
        selected=self.inputs['camera'].text().strip()
        self.camera_combo.blockSignals(True)
        if selected and self.camera_combo.findData(selected)<0:
            self.camera_combo.addItem('手動指定・一覧未確認 / '+selected,selected)
        self.camera_combo.setCurrentIndex(max(0,self.camera_combo.findData(selected)))
        self.camera_combo.blockSignals(False)
        self.filter_cameras();self.render_formats();self.update_camera_target()
        self.clear_video();self.pixmap=None; self.image_label.clear()

    def camera_selected(self, *_):
        device=self.camera_combo.currentData() or ''
        self.inputs['camera'].setText(device)
        self.clear_video();self.pixmap=None; self.image_label.clear()
        self.status.setText('カメラを選びました。「選んだ時刻の画像を取得」で取得します' if device else 'カメラを選択してください')

    def image_time_changed(self, *_):
        self.update_camera_target()
        self.clear_video();self.pixmap=None; self.image_label.clear()
        if self.point:
            self.status.setText('画像要求時刻の補正を変更しました。地点を選び直すか、画像を取得してください')

    def clear_camera_context(self, *_):
        self.inputs['camera'].clear(); self.set_camera_choices([])
        self.clear_video();self.pixmap=None; self.image_label.clear()
        self.status.setText('プロファイル／カメラカバレッジを変更しました。カメラを選び直してください')

    def refresh_cameras(self):
        try:
            config=self.config(); client=self.client({**config,'coverage':config['camera_coverage']})
            def done(cameras):
                self.set_camera_choices(cameras)
                self.status.setText(f"カメラ {len(cameras)} 台を取得しました。使用するカメラを選択してください" if cameras else 'カメラが見つかりません。プロファイルとカバレッジを確認してください')
            self.launch('カメラ一覧を取得中…',lambda cancel:client.cameras(cancel),done)
        except DemoError as e: self.status.setText(str(e))

    def selected_resources(self):
        return [self.sim_list.item(i).data(Qt.UserRole) for i in range(self.sim_list.count())
                if self.sim_list.item(i).checkState()==Qt.Checked]

    def set_sim_choices(self, resources, selected):
        def scoped(r):return dict(r,coverage=r.get('coverage',self.inputs['coverage'].text().strip()))
        selected=[scoped(r) for r in selected];resources=[scoped(r) for r in resources]
        keys={(r['coverage'],r['resource_type'],r['resource_id']) for r in selected}
        combined={(r['coverage'],r['resource_type'],r['resource_id']):r for r in [*selected,*resources]}
        self.sim_list.clear()
        for key,r in combined.items():
            item=QListWidgetItem(f"{r.get('name') or r['resource_id']}  / {r['resource_type']} …{r['resource_id'][-6:]}")
            item.setToolTip(r['resource_id']+' / '+r.get('status','')+' / '+r['coverage'])
            item.setData(Qt.UserRole,r); item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if key in keys else Qt.Unchecked); self.sim_list.addItem(item)
        self.filter_sims(); self.update_sim_count()

    def update_sim_count(self, *_):
        self.sim_count.setText(f'{len(self.selected_resources())} SIM選択 / 最大20（絞り込みで隠れた選択も含む）')
        self.render_formats()

    def filter_sims(self, *_):
        query=self.sim_filter.text().strip().casefold()
        visible=0
        for i in range(self.sim_list.count()):
            item=self.sim_list.item(i)
            item.setHidden(query not in (item.text()+' '+item.data(Qt.UserRole)['resource_id']).casefold())
            visible+=not item.isHidden()
        self.sim_filter_count.setText(f'{visible} / {self.sim_list.count()} SIMを表示' if visible or not query else '一致するSIMがありません。検索条件を変えてください。')

    def clear_sims(self):
        for i in range(self.sim_list.count()): self.sim_list.item(i).setCheckState(Qt.Unchecked)

    def clear_sim_context(self, *_):
        self.sim_list.clear(); self.filter_sims(); self.update_sim_count()
        self.status.setText('プロファイル／カバレッジを変更しました。SIM一覧を取得して選び直してください')

    def add_manual_sim(self):
        resource=self.inputs['resource'].text().strip()
        if not resource: self.status.setText('手動追加用のIMSIまたはSIM IDを入力してください'); return
        resource_type=self.resource_type.currentText()
        def select(resources, failures):
            matches=[dict(r,resource_type=resource_type,resource_id=resource) for r in resources if (r['resource_id'] if resource_type=='Subscriber' else r.get('sim_id'))==resource]
            if not matches:
                self.status.setText('指定したIDは取得済み一覧にありません。'+(' / '.join(failures) if failures else 'IDを確認してください'));return
            old=[self.sim_list.item(i).data(Qt.UserRole) for i in range(self.sim_list.count())]
            self.set_sim_choices(old+matches,self.selected_resources()+matches)
            self.status.setText('IDからSIMを追加しました。'+(' / '.join(failures)))
        self.discover_sims(select)

    def discover_sims(self, callback):
        try:
            config=self.config();clients={coverage:self.client({**config,'coverage':coverage}) for coverage in ('jp','g')}
            def work(cancel):
                resources=[];failures=[]
                for coverage,client in clients.items():
                    if cancel():raise DemoError('中止しました')
                    try:resources.extend(dict(r,coverage=coverage) for r in client.subscribers(cancel))
                    except DemoError as e:failures.append(coverage+' の取得失敗: '+str(e))
                if cancel():raise DemoError('中止しました')
                return resources,failures
            self.launch('日本・グローバルのSIM一覧を取得中…',work,lambda result:callback(*result))
        except DemoError as e:self.status.setText(str(e))

    def refresh_sims(self):
        selected=self.selected_resources()
        existing=[self.sim_list.item(i).data(Qt.UserRole) for i in range(self.sim_list.count())]
        def done(resources,failures):
            failed={message.split(' ')[0] for message in failures}
            retained=[r for r in existing if r.get('coverage') in failed]
            self.set_sim_choices(retained+resources,selected)
            self.status.setText(('日本・グローバルのSIM一覧を取得しました。チェックして選択してください' if not failures else '一部の一覧を取得できませんでした。既存の選択は保持しています。\n'+'\n'.join(failures)))
        self.discover_sims(done)

    def launch(self,label,fn,callback,on_error=None,dispose=None):
        if self.task and self.task.isActive(): return
        self.busy(True); self.status.setText(label)
        generation=self.auth_generation
        def done(value,error):
            self.busy(False); self.task=None
            if generation!=self.auth_generation:
                if dispose and value is not None:dispose(value)
                return
            if error:
                if dispose and value is not None:dispose(value)
                self.status.setText(error)
                if on_error:on_error(error)
            else:
                try: callback(value)
                except DemoError as e: self.status.setText(str(e))
                except Exception: self.status.setText('表示処理に失敗しました。取得したデータの形式を確認してください')
        self.task=Work(label,fn,done); QgsApplication.taskManager().addTask(self.task)

    def cancel(self):
        if self.task: self.task.cancel()

    def load_sample(self):
        self.raw_batches=None;self.source_config=None
        points,rejected=normalize(sample_entries()); self.apply_points(points,rejected,'EPSG:4326',True)

    def config(self):
        return {**{key:edit.text().strip() for key,edit in self.inputs.items()},**self.auth_config()}

    def client(self,c):
        return SoracomClient(c['cli'],c['profile'],self.cache_dir,coverage=c.get('coverage','jp'),runner=self.api_runner(c.get('coverage','jp')))

    def load_live(self):
        try:
            config=self.config(); client=self.client(config)
            mapping=None
            crs=QgsCoordinateReferenceSystem(config['crs'])
            if not crs.isValid() or not crs.isGeographic(): raise DemoError('緯度・経度に対応する地理座標系を指定してください')
            resources=[self.resource_schema(r) for r in self.selected_resources()]
            if not 1 <= len(resources) <= 20: raise DemoError('SIMを1〜20件選択してください')
            client.coverage_clients={cov:self.client({**config,'coverage':cov}) for cov in {r.get('coverage',config['coverage']) for r in resources}}
            def work(cancelled):
                return client.harvest_many(resources,config['start'],config['end'],mapping,config['run_filter'],cancelled)
            def done(result):
                points,rejected,counts=result
                if not self.apply_points(points,rejected,config['crs'],False,[r for c in counts for r in c['records']]):return
                self.source_config=config
                self.raw_batches=counts;self.schema_pending=False;self.store_dataset_config()
                details=' / '.join(f"{c['name']}: {c['points']}点 / 無効{c['rejected']}件" for c in counts)
                self.status.setText(self.status.text()+'\n'+details)
            self.launch('Harvest APIから走行ログを取得中…',work,done)
        except (DemoError,ValueError) as e: self.status.setText(str(e))

    def apply_points(self,points,rejected,crs,sample,records=None):
        if not points and not records:
            self.status.setText(f'表示できる位置情報がありません（対象外 {rejected} 件）。元のレイヤと設定を保持しています。項目名と日時を確認してください'); return False
        if self.layer:self.organize_dataset_layers()
        layer,tracks=make_layers(points,crs,int(self.gap.value()*1000),sample)
        if self.tool and self.iface.mapCanvas().mapTool()==self.tool: self.iface.mapCanvas().unsetMapTool(self.tool)
        for old_id in list(self.owned_ids):
            if QgsProject.instance().mapLayer(old_id): QgsProject.instance().removeMapLayer(old_id)
        self.owned_ids={layer.id(),tracks.id()}
        self.layer,self.tracks,self.sample=layer,tracks,sample
        self.records=make_records(records,layer.name()) if records is not None else None
        if self.records:
            QgsProject.instance().addMapLayer(self.records); self.owned_ids.add(self.records.id())
        QgsProject.instance().addMapLayer(tracks); QgsProject.instance().addMapLayer(layer)
        self.add_track_views();self.organize_dataset_layers()
        self.iface.setActiveLayer(layer)
        if points:
            extent=QgsCoordinateTransform(layer.crs(),self.iface.mapCanvas().mapSettings().destinationCrs(),QgsProject.instance()).transformBoundingBox(layer.extent())
            if extent.width()==0 or extent.height()==0: extent.grow(0.0001 if self.iface.mapCanvas().mapSettings().destinationCrs().isGeographic() else 10)
            extent.scale(1.15); self.iface.mapCanvas().setExtent(extent); self.iface.mapCanvas().refresh()
        self.clear_video();self.point=None; self.pixmap=None; self.image_label.clear(); self.selected.setText('地図上の点を選択してください');self.update_camera_target()
        self.status.setText(('SAMPLE・架空データ / ' if sample else 'Harvest取得完了 / ')+f'{len(points)} 点・{tracks.featureCount()} 区間・受信機別の軌跡 {len(self.track_views)} レイヤ・無効位置 {rejected} 件（地図には表示しません）')
        self.schema_pending=False
        missing=sum(p.get('process_ms',p.get('measured_ms')) is None for p in points)
        errors=sum(any(v=='invalid' for v in json.loads(p.get('value_status_json','{}')).values()) for p in points)
        self.status.setText(self.status.text()+f'\n処理時刻なし {missing} 点（軌跡・画像の対象外） / 追加の値の変換失敗 {errors} 点')
        self.pick()
        return True

    def organize_dataset_layers(self):
        """Group only this dataset; retain saved data and unrelated user groups."""
        if not self.layer:return
        from datetime import datetime
        root=QgsProject.instance().layerTreeRoot();token=self.layer.customProperty('soracom/track_group','')
        if not token:return
        layers=[self.layer,*self.track_views]+([self.tracks] if self.tracks else [])+([self.records] if self.records else [])
        saved=self.layer.providerType()!='memory'
        stamp=self.layer.customProperty('soracom/created_at','') or datetime.now().strftime('%H:%M:%S')
        self.layer.setCustomProperty('soracom/created_at',stamp)
        name=('保存済み / '+Path(self.layer.source().split('|')[0]).name if saved else '作業中 / '+stamp)
        group=next((g for g in root.findGroups() if g.customProperty('soracom/dataset_group','')==token),None)
        if group is None:group=root.insertGroup(0,name);group.setCustomProperty('soracom/dataset_group',token)
        group.setName(name)
        for layer in layers:
            node=root.findLayer(layer.id())
            if node and node.parent()!=group:
                parent=node.parent();group.addChildNode(node.clone());parent.removeChildNode(node)
        for other in list(root.findGroups()):
            if other.customProperty('soracom/dataset_group','') and not other.children():other.parent().removeChildNode(other)

    def add_track_views(self):
        """Separate receiver trajectories, retaining the aggregate for compatibility."""
        project=QgsProject.instance()
        self.track_views=[]
        token=uuid.uuid4().hex
        self.layer.setCustomProperty('soracom/track_group',token)
        self.tracks.setCustomProperty('soracom/track_group',token)
        if self.records:self.records.setCustomProperty('soracom/track_group',token)
        grouped={}
        for feature in self.tracks.getFeatures():
            grouped.setdefault(str(feature['receiver']),[]).append(feature)
        for receiver,features in sorted(grouped.items()):
            view=QgsVectorLayer('LineString?crs='+self.tracks.crs().authid(),receiver+'・軌跡','memory')
            view.dataProvider().addAttributes(list(self.tracks.fields()));view.updateFields()
            view.dataProvider().addFeatures(features);view.updateExtents()
            renderer=self.tracks.renderer().clone()
            labels={feature['track_label'] for feature in features}
            for index in reversed(range(len(renderer.categories()))):
                if renderer.categories()[index].value() not in labels: renderer.deleteCategory(index)
            view.setRenderer(renderer)
            view.setCustomProperty('soracom/track_group',token)
            view.setCustomProperty('soracom/track_view',True)
            project.addMapLayer(view);self.track_views.append(view);self.owned_ids.add(view.id())
        project.layerTreeRoot().findLayer(self.tracks.id()).setItemVisibilityChecked(not bool(self.track_views))
        # Custom render order may otherwise put newly added layers below the basemap.
        tree=project.layerTreeRoot()
        if tree.hasCustomLayerOrder():
            front=[self.layer]+self.track_views+[self.tracks]
            ids={layer.id() for layer in front}
            tree.setCustomLayerOrder(front+[layer for layer in tree.customLayerOrder() if layer.id() not in ids])

    def pick(self):
        if not self.layer or not QgsProject.instance().mapLayer(self.layer.id()):
            self.status.setText('先に位置情報を読み込んでください'); return
        if self.tool:
            if self.iface.mapCanvas().mapTool()==self.tool: self.iface.mapCanvas().unsetMapTool(self.tool)
            self.tool.deleteLater()
        self.tool=QgsMapToolIdentifyFeature(self.iface.mapCanvas(),self.layer)
        self.tool.featureIdentified.connect(self.point_clicked)
        self.iface.mapCanvas().setMapTool(self.tool)

    def point_clicked(self,feature):
        # Only an explicit map click may start a request. Restore/save/attach must not.
        if self.task: return
        cached=self.choose(feature)
        if cached and self.media_mode.currentData()=='video' and self.player:self.play_video()
        if not cached and self.auto_image.isChecked():
            self.get_image()

    def choose(self,feature):
        if self.schema_pending:
            self.pixmap=None;self.image_label.clear();self.show_pending_guidance();return False
        if self.task:
            return False  # Keep the selected timestamp stable while its image is being fetched.
        self.point={key:feature[key] for key in ('measured_ms','measured_at','receiver','fix_status','synthetic')}
        fields=feature.fields().names()
        for key in ('process_ms','process_at','time_basis','received_at','source_key'):
            if key in fields:self.point[key]=feature[key]
        stamp=self.point.get('process_ms',self.point['measured_ms'])
        self.point['active_ms']=stamp if isinstance(stamp,(int,float)) else None
        self.point['feature_id']=feature.id()
        self.clear_video()
        self.layer.selectByIds([feature.id()]); self.pixmap=None; self.image_label.clear()
        label='架空位置・API動作確認\n' if self.point['synthetic'] else ''
        if feature.fields().indexFromName('resource_id')>=0 and feature['resource_id']:
            label+=f"SIM: {feature['sim_name']} / …{str(feature['resource_id'])[-6:]}\n"
        self.selected.setText(label+f"{self.point['receiver']} / {self.point['fix_status']}\n測位: {self.point['measured_at'] or 'なし'}\n到達: {self.point.get('received_at','')}\n処理基準: { {'measured':'測位時刻','received':'データ到達時刻'}.get(self.point.get('time_basis'), '測位時刻')} / {self.point.get('process_at',self.point['measured_at'])}")
        if self.media_mode.currentData()=='video':
            self.update_camera_target();return self.find_video()
        self.image_label.show()
        cached = feature['image_path'] if feature.fields().indexFromName('image_path') >= 0 else None
        self.update_camera_target()
        try:
            target=self.effective_camera(allow_unselected=True);selected_camera=target['device']
        except DemoError as e:
            if not self.sample:self.status.setText(str(e));return False
            selected_camera=''
        same_camera=not selected_camera or str(feature['camera_id'])==selected_camera
        try:
            same_time=utc_ms(str(feature['image_requested_at']))==int(self.point['active_ms'])+round(self.offset.value()*1000)
        except (DemoError,ValueError,TypeError):
            same_time=False
        if same_camera and same_time and isinstance(cached, str) and cached and Path(QgsProject.instance().pathResolver().readPath(cached)).is_file():
            pix = QPixmap(QgsProject.instance().pathResolver().readPath(cached))
            if not pix.isNull():
                self.show_pixmap(pix)
                self.status.setText('保存済み画像を表示\n要求時刻: '+str(feature['image_requested_at'])+'\nカメラの設置場所と測位点の対応は別途確認してください')
                return True
        self.status.setText('このカメラ・要求時刻の保存済み画像はありません。自動取得をONにして点をクリックするか、画像取得ボタンを押してください')
        return False

    def get_image(self):
        if self.media_mode.currentData()=='video':return self.get_video()
        if self.task:return
        if self.schema_pending:self.show_pending_guidance();return
        if not self.point: self.status.setText('地図上の点を1つ選んでください'); return
        if self.point.get('active_ms') is None:self.status.setText('選択した基準の時刻がありません。データ到達時刻を選び、取得済みデータに適用してください');return
        if self.layer.isEditable(): self.status.setText('レイヤの編集を終了してから画像を取得してください'); return
        stamp=int(self.point['active_ms'])+round(self.offset.value()*1000)
        if self.sample:
            pix=QPixmap(960,540); pix.fill(QColor('#102c40')); painter=QPainter(pix)
            painter.setPen(QPen(QColor('#557b8d'),3))
            for x in range(0,960,80): painter.drawLine(x,0,x,540)
            for y in range(0,540,80): painter.drawLine(0,y,960,y)
            painter.fillRect(0,160,960,190,QColor('#24475a')); painter.setPen(QPen(QColor('#ffffff'),4,Qt.DashLine)); painter.drawLine(0,255,960,255)
            painter.setPen(QColor('white')); painter.setFont(QFont('Arial',28)); painter.drawText(35,70,'SAMPLE / NOT CAMERA FOOTAGE')
            painter.setFont(QFont('Arial',22)); painter.drawText(35,430,utc_text(stamp)); painter.end()
            self.show_pixmap(pix); self.status.setText('サンプル画像を表示中。ソラカメAPIは未呼び出しです'); return
        try:
            config=self.config()
            if self.source_config and config['profile']!=self.source_config['profile']: raise DemoError('取得時と同じプロファイルを指定してください')
            target=self.effective_camera();self.update_camera_target()
            camera_config={**config,'coverage':target['coverage']}
            client=self.client(camera_config);device=target['device']
            self.pending_camera=device
            self.launch('ソラカメ画像を取得中…',lambda cancel:client.image(device,stamp,cancel),self.image_done)
        except (DemoError,ValueError) as e: self.status.setText(str(e))

    def image_done(self,result):
        pix=QPixmap(result['path'])
        if pix.isNull(): raise DemoError('取得した画像を表示できません')
        self.show_pixmap(pix)
        if self.point:
            attrs={'image_path':result['path'],'image_requested_at':utc_text(result['requested_ms']),'camera_id':self.pending_camera}
            if self.layer.isEditable(): raise DemoError('レイヤの編集を終了してから画像を紐づけてください')
            ok=self.layer.dataProvider().changeAttributeValues({self.point['feature_id']:{self.layer.fields().indexFromName(k):v for k,v in attrs.items()}})
            if not ok: raise DemoError('画像はキャッシュに取得済みですが、レイヤへ紐づけを保存できませんでした')
            stored=self.layer.getFeature(self.point['feature_id'])
            if any(stored[k]!=v for k,v in attrs.items()): raise DemoError('画像の紐づけを再読込できませんでした')
        self.status.setText(('キャッシュ' if result['cached'] else 'ソラカメAPI')+'から画像を表示\n要求時刻: '+utc_text(result['requested_ms']))
        self.status.setText(self.status.text()+'\nカメラの設置場所と測位点の対応は別途確認してください')
        if self.point and self.point['synthetic']:
            self.status.setText(self.status.text()+'\n架空位置と別設置カメラの組み合わせです')

    def show_pixmap(self,pix):
        self.clear_video();self.image_label.show()
        self.pixmap=pix; self.image_label.setPixmap(pix.scaled(max(360,self.image_label.width()),270,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        self.scroll.ensureWidgetVisible(self.image_label)

    def save_settings(self):
        config={**self.config(),'resource_type':self.resource_type.currentText(),
                'selected_resources':self.selected_resources(),
                'gap_seconds':self.gap.value(),'offset_seconds':self.offset.value(),
                'auto_image':self.auto_image.isChecked(),'auto_media':self.auto_image.isChecked(),
                'media_mode':self.media_mode.currentData(),'video_before_seconds':self.video_before.value(),'video_after_seconds':self.video_after.value(),
                'mapping':self.mapping.text(),'cache_dir':str(self.cache_dir)}
        try:
            atomic_json(self.storage/'settings.json',config)
            note={'session':'ログイン情報は保存しません','cli':'認証情報はCLI側で管理'}.get(self.auth_mode.currentData(),'認証情報はQGIS認証ストアで管理')
            self.status.setText('設定をこのPCに保存しました（'+note+'）')
        except OSError:
            self.status.setText('接続設定の保存に失敗しました')

    @staticmethod
    def compatible(layer):
        required={'measured_ms','measured_at','received_at','receiver','run','fix_status','synthetic',
                  'image_path','image_requested_at','camera_id'}
        return isinstance(layer,QgsVectorLayer) and layer.isValid() and layer.geometryType()==0 and required.issubset(set(layer.fields().names()))

    def attach_existing(self, automatic=False):
        if self.task: return
        project=QgsProject.instance()
        layers=[layer for layer in project.mapLayers().values() if self.compatible(layer)]
        active=self.iface.activeLayer()
        layer=active if active in layers else (layers[0] if len(layers)==1 else None)
        if layer is None:
            if not automatic: self.status.setText('このプラグインで作った位置レイヤを選択してください（複数ある場合は左のレイヤ一覧で選択）')
            return
        self.layer=layer; self.source_config=None; self.sample=bool(layer.customProperty('soracom/sample',False))
        token=layer.customProperty('soracom/track_group','')
        tracks=[x for x in project.mapLayers().values() if isinstance(x,QgsVectorLayer) and x.name()==layer.name()+'・軌跡' and (not token or x.customProperty('soracom/track_group','')==token)]
        self.tracks=tracks[0] if len(tracks)==1 else None
        token=layer.customProperty('soracom/track_group','')
        self.track_views=[x for x in project.mapLayers().values() if token and x.customProperty('soracom/track_view',False) and x.customProperty('soracom/track_group','')==token]
        records=[x for x in project.mapLayers().values() if isinstance(x,QgsVectorLayer) and x.name()==layer.name()+'・全受信記録']
        matching=[x for x in records if token and x.customProperty('soracom/track_group','')==token]
        records=matching or [x for x in records if not x.customProperty('soracom/track_group','')]
        self.records=records[0] if len(records)==1 else None
        self.restore_dataset_config()
        self.clear_video();self.point=None; self.pixmap=None; self.image_label.clear()
        chosen=layer.selectedFeatures()
        if not chosen:
            # Prefer a saved image without scanning the whole layer into memory.
            first=None
            for f in layer.getFeatures():
                if first is None: first=f
                if isinstance(f['image_path'],str) and f['image_path']:
                    chosen=[f]; break
            if not chosen and first is not None: chosen=[first]
        if chosen: self.choose(chosen[0])
        else: self.status.setText('位置レイヤは空です')
        self.pick()

    def reset_binding(self):
        self.auto_apply_timer.stop();self.auto_apply_error=''
        if hasattr(self,'pending_settings_button'):self.pending_settings_button.hide()
        self.clear_video()
        if self.task:
            self.task.done=lambda *_:None
            self.task.cancel(); self.task=None
        if self.tool:
            if self.iface.mapCanvas().mapTool()==self.tool: self.iface.mapCanvas().unsetMapTool(self.tool)
            self.tool.deleteLater(); self.tool=None
        self.layer=None; self.tracks=None; self.records=None; self.track_views=[]; self.point=None; self.pixmap=None; self.source_config=None
        self.schema_pending=False;self.raw_batches=None
        self.image_label.clear(); self.selected.setText('地点未選択'); self.update_camera_target(); self.busy(False)

    def layers_removing(self,ids):
        self.owned_ids.difference_update(ids)
        self.track_views=[layer for layer in self.track_views if layer.id() not in ids]
        if self.layer and self.layer.id() in ids:
            self.reset_binding()
            self.status.setText('位置レイヤが閉じられました')
        elif self.records and self.records.id() in ids:
            self.records=None
        elif self.tracks and self.tracks.id() in ids:
            self.tracks=None

    def shutdown(self):
        self.end_auth_session()
        self.close_video_player()
        try:QgsApplication.instance().aboutToQuit.disconnect(self.end_auth_session)
        except (TypeError,RuntimeError):pass
        for name in ('connection_dialog','sim_dialog','editor_dialog','camera_dialog','vlc_dialog'):
            dialog=getattr(self,name,None)
            if dialog:dialog.close()
        self.reset_binding()
        QgsProject.instance().layersWillBeRemoved.disconnect(self.layers_removing)

    def save_layers(self):
        if not self.layer: self.status.setText('先に位置情報を読み込んでください'); return
        path,_=QFileDialog.getSaveFileName(self,'位置情報を保存',str(Path.home()/'drive.gpkg'),'GeoPackage (*.gpkg)')
        if not path:return
        try:
            self.persist_layers(path)
            self.status.setText('GeoPackageに保存し、保存済みレイヤへ切り替えました。続けてQGISプロジェクトを保存してください。画像は元のキャッシュに保持します')
        except (DemoError,OSError) as e: self.status.setText(str(e))

    def persist_layers(self,path):
        if Path(path).exists(): raise DemoError('既存ファイルを上書きせず、新しいファイル名を指定してください')
        project=QgsProject.instance(); originals=[self.layer]+([self.tracks] if self.tracks else [])+([self.records] if self.records else [])
        roles=['positions']+(['tracks'] if self.tracks else [])+(['records'] if self.records else [])
        originals+=self.track_views
        roles+=['receiver_track_'+str(i) for i in range(len(self.track_views))]
        visibility=[project.layerTreeRoot().findLayer(layer.id()).itemVisibilityChecked() for layer in originals]
        restored=[]
        identity_fields=[n for n in ('source_key','event_id','receiver','run','measured_ms','fix_status') if n in self.layer.fields().names()]
        chosen=self.layer.getFeature(self.point['feature_id']) if self.point else None
        selected=([chosen[n] for n in identity_fields],chosen.geometry().asWkb()) if chosen else None
        for i,layer in enumerate(originals):
            name=roles[i]
            options=QgsVectorFileWriter.SaveVectorOptions(); options.driverName='GPKG'; options.layerName=name
            options.actionOnExistingFile=QgsVectorFileWriter.CreateOrOverwriteFile if i==0 else QgsVectorFileWriter.CreateOrOverwriteLayer
            result=QgsVectorFileWriter.writeAsVectorFormatV3(layer,str(path),project.transformContext(),options)
            if result[0]!=QgsVectorFileWriter.NoError: raise DemoError('GeoPackageの保存に失敗しました。元のレイヤを保持しています')
            new=QgsVectorLayer(str(path)+'|layername='+name,layer.name(),'ogr')
            if not new.isValid() or new.featureCount()!=layer.featureCount(): raise DemoError('保存したレイヤの再読込に失敗しました。元のレイヤを保持しています')
            if layer.renderer(): new.setRenderer(layer.renderer().clone())
            for field in layer.fields():
                if field.alias():new.setFieldAlias(new.fields().indexFromName(field.name()),field.alias())
            new.setCustomProperty('soracom/sample',self.sample)
            for key in ('soracom/track_group','soracom/track_view','soracom/source_config','soracom/formats','soracom/value_definitions','soracom/camera_bindings','soracom/created_at'):
                new.setCustomProperty(key,layer.customProperty(key,''))
            restored.append(new)
        sample=self.sample
        for new in reversed(restored): project.addMapLayer(new)
        for new,visible in zip(restored,visibility): project.layerTreeRoot().findLayer(new.id()).setItemVisibilityChecked(visible)
        tree=project.layerTreeRoot()
        if tree.hasCustomLayerOrder():
            replacements={old.id():new for old,new in zip(originals,restored)}
            order=[replacements.get(layer.id(),layer) for layer in tree.customLayerOrder() if layer.id() not in {x.id() for x in restored}]
            tree.setCustomLayerOrder(order)
        for old in originals: project.removeMapLayer(old.id())
        self.layer=restored[0]; self.tracks=restored[roles.index('tracks')] if 'tracks' in roles else None; self.sample=sample
        self.records=restored[roles.index('records')] if 'records' in roles else None
        self.track_views=[layer for role,layer in zip(roles,restored) if role.startswith('receiver_track_')]
        self.restore_dataset_config();self.organize_dataset_layers()
        self.layer.setEditorWidgetSetup(self.layer.fields().indexFromName('image_path'),QgsEditorWidgetSetup('ExternalResource',{'DocumentViewer':1,'DocumentViewerHeight':270}))
        self.iface.setActiveLayer(self.layer)
        if selected is not None:
            for feature in self.layer.getFeatures():
                if ([feature[n] for n in identity_fields],feature.geometry().asWkb())==selected: self.choose(feature); break
        self.pick()

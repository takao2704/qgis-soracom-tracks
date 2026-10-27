"""Per-SIM format and time selection, persisted separately from credentials."""
import copy
import json
import uuid
from qgis.PyQt.QtCore import Qt,QTimer
from qgis.PyQt.QtWidgets import QLabel,QComboBox,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QDialog,QGroupBox,QVBoxLayout,QFormLayout,QAbstractItemView
from .schema import BUILTINS,preset,normalize_schema
from .soracom_io import atomic_json,DemoError
from .schema_dialog import SchemaDialog


class SchemaUI:
    def init_schema_state(self):
        self.schema_path=self.storage/'formats.json';self.schema_store={'presets':{},'resources':{}}
        self.raw_batches=None;self.schema_pending=False;self._format_rendering=False
        self.auto_apply_error='';self.auto_apply_timer=QTimer(self);self.auto_apply_timer.setSingleShot(True);self.auto_apply_timer.setInterval(500);self.auto_apply_timer.timeout.connect(self.auto_apply_settings)
        if self.schema_path.exists():
            try:
                value=json.loads(self.schema_path.read_text())
                if isinstance(value.get('presets'),dict) and isinstance(value.get('resources'),dict):self.schema_store=value
            except (ValueError,OSError):pass

    def build_schema_ui(self,layout):
        self.format_box=QGroupBox('SIMごとの設定');box=QVBoxLayout(self.format_box);layout.addWidget(self.format_box)
        hint=QLabel('一覧でSIMを選び、下の欄で設定します。');box.addWidget(hint)
        self.format_table=QTableWidget(0,2);self.format_table.setHorizontalHeaderLabels(['SIM・反映状態','形式・カメラ・時刻'])
        self.format_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.format_table.verticalHeader().hide();self.format_table.setWordWrap(True)
        self.format_table.setSelectionBehavior(QAbstractItemView.SelectRows);self.format_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.format_table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.format_table.setAccessibleName('設定するSIMを選択')
        self.format_table.currentCellChanged.connect(self.show_resource_editor);box.addWidget(self.format_table)
        self.resource_title=QLabel();self.resource_title.setWordWrap(True);self.resource_title.setTextFormat(Qt.PlainText);box.addWidget(self.resource_title)
        form=QFormLayout();form.setRowWrapPolicy(QFormLayout.WrapLongRows);box.addLayout(form)
        self.format_combo=QComboBox();self.sim_time_combo=QComboBox();self.sim_camera_combo=QComboBox()
        for c in (self.format_combo,self.sim_time_combo,self.sim_camera_combo):
            c.setFocusPolicy(Qt.StrongFocus);c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);c.setMinimumContentsLength(12)
        form.addRow('データ形式',self.format_combo)
        self.edit_format_button=QPushButton('項目を確認・カスタム形式を作成');form.addRow(self.edit_format_button)
        for label,key in [('一括設定に合わせる','inherit'),('デバイスの測位時刻','measured'),('Harvestへの到達時刻','received')]:self.sim_time_combo.addItem(label,key)
        form.addRow('このSIMの時刻',self.sim_time_combo);form.addRow('連携カメラ',self.sim_camera_combo)
        camera_note=QLabel('カメラは自動保存。次の点選択・映像取得から有効です。');camera_note.setWordWrap(True);form.addRow(camera_note)
        self.binding_summary=QLabel();self.binding_summary.setTextFormat(Qt.PlainText);self.binding_summary.setWordWrap(True);form.addRow(self.binding_summary)
        self.binding_pick=QPushButton('個別カメラを選ぶ…');form.addRow(self.binding_pick);self.binding_pick.clicked.connect(self.pick_resource_camera)
        self.binding_refresh=QPushButton('カメラ一覧を更新');form.addRow(self.binding_refresh);self.binding_refresh.clicked.connect(self.refresh_cameras)
        self.format_detail=QLabel();self.format_detail.setWordWrap(True);self.format_detail.setTextFormat(Qt.PlainText);box.addWidget(self.format_detail)
        self.format_combo.currentIndexChanged.connect(self.editor_format_changed)
        self.sim_time_combo.currentIndexChanged.connect(self.editor_basis_changed)
        self.sim_camera_combo.currentIndexChanged.connect(self.editor_camera_changed)
        self.edit_format_button.clicked.connect(lambda:self.edit_schema(self.editing_resource()) if self.editing_resource() else None)
        self.time_basis=QComboBox();self.time_basis.addItem('Harvestへのデータ到達時刻','received');self.time_basis.addItem('デバイスの測位時刻','measured')
        layout.addWidget(QLabel('処理に使う時刻（一括設定）'));layout.addWidget(self.time_basis)
        self.time_basis.setCurrentIndex(max(0,self.time_basis.findData(self.schema_store.get('basis','measured'))))
        self.time_basis.currentIndexChanged.connect(self.global_basis_changed)
        self.schema_note=QLabel();self.schema_note.setWordWrap(True);layout.addWidget(self.schema_note)
        self.reprocess_button=QPushButton('反映を再試行（API再取得なし）');box.addWidget(self.reprocess_button);self.reprocess_button.clicked.connect(self.reprocess_data)
        self.apply_note=QLabel();self.apply_note.setTextFormat(Qt.PlainText);self.apply_note.setWordWrap(True);box.insertWidget(box.count()-1,self.apply_note)

    def editing_resource(self):
        row=self.format_table.currentRow()
        item=self.format_table.item(row,0) if row>=0 else None
        return item.data(Qt.UserRole) if item else None

    def show_resource_editor(self,*_):
        if self._format_rendering:return
        r=self.editing_resource();self._format_rendering=True
        try:
            for widget in (self.format_combo,self.sim_time_combo,self.sim_camera_combo,self.edit_format_button,self.binding_pick):widget.setEnabled(bool(r))
            if not r:
                self.resource_title.setText('取得するSIMを選択してください');self.format_detail.clear();return
            cfg=self.schema_store['resources'].get(self.resource_key(r),{});name=r.get('name') or r['resource_id']
            self.resource_title.setText(name+' / '+r['resource_id'] if name!=r['resource_id'] else name)
            self.format_combo.clear();self.format_combo.addItem('未設定 — 形式を選択','')
            for key,spec in {**BUILTINS,**self.schema_store['presets']}.items():
                self.format_combo.addItem({'gnss':'QLM / CLAS','multi':'GPSマルチユニット','beacon':'ビーコンGW'}.get(key,spec['name']),key)
            self.format_combo.addItem('新しいカスタム形式…','new')
            self.format_combo.setCurrentIndex(max(0,self.format_combo.findData(cfg.get('format',''))))
            self.format_combo.setToolTip(self.format_combo.currentText());self.format_combo.setAccessibleName(name+'のデータ形式')
            self.sim_time_combo.setCurrentIndex(max(0,self.sim_time_combo.findData(cfg.get('basis','inherit'))));self.sim_time_combo.setAccessibleName(name+'の時刻基準')
            self.populate_binding_combo(r)
            self.binding_summary.setText(name+" → "+self.camera_summary(r))
            try:
                spec=preset(cfg.get('format'),self.schema_store['presets'])
                self.format_detail.setText('緯度: '+spec['lat']+' / 経度: '+spec['lon']+'\n測位時刻: '+(spec.get('time') or 'なし')+' / 追加の値: '+str(len(spec.get('values',[])))+'項目')
            except ValueError:self.format_detail.setText('形式が未設定です。既知の形式を選ぶか、サンプルから項目を設定してください。')
        finally:self._format_rendering=False

    def editor_format_changed(self,*_):
        r=self.editing_resource()
        if r and not self._format_rendering:self.format_changed(r,self.format_combo.currentData())

    def editor_basis_changed(self,*_):
        r=self.editing_resource()
        if r and not self._format_rendering:self.sim_basis_changed(r,self.sim_time_combo.currentData())

    def editor_camera_changed(self,*_):
        r=self.editing_resource()
        if r and not self._format_rendering:self.set_camera_binding(r,self.sim_camera_combo.currentData())

    def resource_key(self,r):
        return json.dumps([self.inputs['profile'].text().strip(),r.get('coverage',self.inputs['coverage'].text().strip()),r['resource_type'],r['resource_id']],separators=(',',':'))

    def save_formats(self):
        try:atomic_json(self.schema_path,self.schema_store)
        except OSError:self.status.setText('形式設定を保存できませんでした。設定はこのセッション内のみ有効です')

    def migrate_formats(self,resources):
        keys=[k.strip() for k in self.mapping.text().split(',')]
        key='gnss'
        if keys!=['measured_at','lat','lon','receiver','run','fix_status'] and len(keys)==6 and all(keys):
            key='legacy-fields';spec=copy.deepcopy(BUILTINS['gnss']);spec.update(dict(zip(['time','lat','lon','receiver','run','fix'],keys)));spec['name']='旧版の項目設定';self.schema_store['presets'].setdefault(key,spec)
        for r in resources:self.schema_store['resources'].setdefault(self.resource_key(r),{'format':key,'basis':'inherit'})
        if resources:self.save_formats()
        self.render_formats()

    def render_formats(self,*_):
        if not hasattr(self,'format_table') or self._format_rendering:return
        current=self.editing_resource();selected=self.resource_key(current) if current else None
        self._format_rendering=True
        try:
            resources=self.selected_resources();self.format_table.setRowCount(len(resources));selected_row=0
            for row,r in enumerate(resources):
                key=self.resource_key(r);cfg=self.schema_store['resources'].get(key,{})
                name=r.get('name') or r['resource_id'];state=self.resource_apply_state(r)
                item=QTableWidgetItem(('…'+r['resource_id'][-6:] if name==r['resource_id'] else name)+'\n'+state)
                item.setToolTip(name+' / '+r['resource_id']);item.setData(Qt.UserRole,dict(r));self.format_table.setItem(row,0,item)
                spec={**BUILTINS,**self.schema_store['presets']}.get(cfg.get('format'))
                title=({'gnss':'QLM / CLAS','beacon':'ビーコンGW'}.get(cfg.get('format'),spec['name']) if spec else '未設定')
                basis=cfg.get('basis','inherit');basis=self.time_basis.currentData() if basis=='inherit' else basis
                summary=title+'\n'+self.camera_summary(r)+'\n'+('測位時刻' if basis=='measured' else '到達時刻')
                item=QTableWidgetItem(summary);item.setToolTip(summary);self.format_table.setItem(row,1,item)
                if key==selected:selected_row=row
            self.format_table.resizeRowsToContents()
            for row in range(len(resources)):self.format_table.setRowHeight(row,max(76,self.format_table.rowHeight(row)))
            height=self.format_table.horizontalHeader().height()+sum(self.format_table.rowHeight(i) for i in range(min(3,len(resources))))+4
            self.format_table.setFixedHeight(max(65,height))
            if resources:self.format_table.setCurrentCell(selected_row,0)
            self.update_time_note()
            if hasattr(self,'apply_note'):
                note=('自動反映できませんでした。データ形式・時刻を修正すると再試行します。' if self.auto_apply_error else '変更は未適用です。取得済みデータへ自動反映します…') if self.schema_pending else '設定を変更すると、取得済みデータへ自動反映します（API再取得なし）。'
                self.apply_note.setText(note)
                self.reprocess_button.setVisible(bool(self.schema_pending and self.auto_apply_error and self.raw_batches))
        finally:self._format_rendering=False
        self.show_resource_editor()
        if hasattr(self,"cards_layout"):self.update_compact_ui()
        if hasattr(self,"pending_settings_button"):self.pending_settings_button.setVisible(bool(self.schema_pending and (self.auto_apply_error or not self.raw_batches)))

    def show_pending_guidance(self):
        names=[r.get("name") or r["resource_id"] for r in self.selected_resources() if self.resource_apply_state(r) in ("未適用","未設定")]
        target=("対象SIM: "+"、".join(names[:3])+(" ほか" if len(names)>3 else "")+"。\n") if names else ""
        if self.raw_batches and not self.auto_apply_error:
            self.status.setText('データ形式・時刻の変更を取得済みデータへ自動反映します…\n反映が終わってから地点を選択してください。')
            self.pending_settings_button.hide();return
        if self.raw_batches:
            action='「SIMごとの設定」でデータ形式・時刻を修正してください。変更後に自動で再試行します。'
        else:
            action='再処理用のデータがありません。「選択したSIMのHarvestから取得」で読み直してください。'
        self.status.setText('設定を反映できませんでした。元の地図を保持しています。\n'+target+(self.auto_apply_error+'\n' if self.auto_apply_error else '')+action)
        self.pending_settings_button.show()

    def schedule_auto_apply(self):
        self.auto_apply_timer.stop();self.auto_apply_error=''
        if self.schema_pending and self.layer and self.raw_batches:
            self.auto_apply_timer.start()
        if self.schema_pending:self.show_pending_guidance()
        self.render_formats()

    def auto_apply_settings(self):
        if not self.schema_pending or not self.layer or not self.raw_batches:return
        if self.task:
            self.auto_apply_timer.start();return
        self.reprocess_data(automatic=True)

    def go_to_pending_settings(self):
        self.render_formats()
        for row in range(self.format_table.rowCount()):
            r=self.format_table.item(row,0).data(Qt.UserRole)
            if self.resource_apply_state(r) in ("未適用","未設定"):
                self.format_table.setCurrentCell(row,0);break
        self.show_pending_guidance()
        if hasattr(self,"editor_dialog") and self.editing_resource():self.open_resource_settings(self.resource_key(self.editing_resource()))
        target=self.format_combo
        self.scroll.ensureWidgetVisible(target,0,80)
        target.setFocus(Qt.OtherFocusReason)

    def resource_apply_state(self,r):
        if not self.layer:return '取得前'
        try:
            saved=json.loads(self.layer.customProperty('soracom/formats','{}'));key=self.resource_key(r)
            if key not in saved.get('resources',{}):return '未適用' if self.schema_pending else '取得前'
            old=saved['resources'][key];current=self.resource_schema(r)
            basis=old.get('basis','inherit');basis=saved.get('basis','measured') if basis=='inherit' else basis
            same=preset(old.get('format'),saved.get('presets',{}))==current['schema'] and basis==current['basis']
            return '反映済み' if same else '未適用'
        except (ValueError,TypeError,DemoError):return '未設定'

    def settings_changed(self):
        self.save_formats()
        self.update_time_note()
        if self.layer:
            self.schema_pending=True;self.pixmap=None;self.image_label.clear()
            self.schedule_auto_apply()
        self.render_formats()

    def format_changed(self,r,key):
        if key=='new':self.edit_schema(r,new=True);self.render_formats();return
        cfg=self.schema_store['resources'].setdefault(self.resource_key(r),{});cfg['format']=key;self.settings_changed()
        self.render_formats()

    def sim_basis_changed(self,r,value):
        self.schema_store['resources'].setdefault(self.resource_key(r),{})['basis']=value;self.settings_changed();self.render_formats()

    def global_basis_changed(self,*_):
        self.schema_store['basis']=self.time_basis.currentData();self.settings_changed()

    def resource_schema(self,r):
        cfg=self.schema_store['resources'].get(self.resource_key(r),{})
        try:spec=preset(cfg.get('format'),self.schema_store['presets'])
        except ValueError as e:raise DemoError((r.get('name') or r['resource_id'])+'：'+str(e)) from None
        basis=cfg.get('basis','inherit');basis=self.time_basis.currentData() if basis=='inherit' else basis
        return dict(r,schema=spec,basis=basis)

    def edit_schema(self,r,new=False):
        cfg=self.schema_store['resources'].get(self.resource_key(r),{})
        try:spec=preset(cfg.get('format'),self.schema_store['presets']) if not new else None
        except ValueError:spec=None
        spec=spec or dict(name='カスタム',lat='',lon='',time='',time_format='iso',values=[])
        rows=[]
        for batch in self.raw_batches or []:
            src=batch['source']
            if (src.get('profile'),src.get('coverage'),src['resource_type'],src['resource_id'])==(self.inputs['profile'].text().strip(),r.get('coverage',self.inputs['coverage'].text().strip()),r['resource_type'],r['resource_id']):rows=batch['raw_rows'][:3];break
        dialog=SchemaDialog(self,spec,r,rows)
        if dialog.exec_()==QDialog.Accepted:
            key='custom-'+uuid.uuid4().hex;self.schema_store['presets'][key]=dialog.definition
            self.schema_store['resources'].setdefault(self.resource_key(r),{})['format']=key
            self.render_formats();self.settings_changed()

    def reprocess_data(self, _checked=False, automatic=False):
        self.auto_apply_timer.stop()
        if self.task:
            if self.schema_pending:self.auto_apply_timer.start()
            return
        if not self.raw_batches:self.status.setText('再処理できる取得済みデータがありません。Harvestから取得してください。');return
        if self.source_config and any(self.config()[k]!=self.source_config[k] for k in ('profile',)):
            self.auto_apply_error='接続設定を取得時のプロファイルに戻してください。';self.show_pending_guidance();self.render_formats();return
        try:
            points=[];records=[];bad=0
            for batch in self.raw_batches:
                src=batch['source'];resource=self.resource_schema(src);current_records=[]
                current,rejected=normalize_schema(batch['raw_rows'],resource['schema'],src,current_records,resource['basis'])
                run=(self.source_config or {}).get('run_filter','')
                if run:
                    current=[p for p in current if p['run']==run];current_records=[p for p in current_records if p['run']==run];rejected=sum(p['disposition']=='invalid' for p in current_records)
                points.extend(current);records.extend(current_records);bad+=rejected
            if not points and self.layer and self.layer.featureCount():raise DemoError('表示できる位置が0件です。緯度・経度の項目とデータ形式を確認してください。')
            config=dict(self.source_config or self.config());batches=self.raw_batches
            from qgis.core import QgsRectangle
            extent=QgsRectangle(self.iface.mapCanvas().extent())
            if not self.apply_points(points,bad,config['crs'],False,records):return
            if automatic:self.iface.mapCanvas().setExtent(extent);self.iface.mapCanvas().refresh()
            self.raw_batches=batches;self.source_config=config;self.auto_apply_error=''
            self.schema_pending=False;self.store_dataset_config();self.status.setText(('設定を自動反映しました' if automatic else '設定を反映しました')+'（API再取得なし）。\n'+self.status.text().replace('Harvest取得完了','再処理完了'))
        except (DemoError,ValueError) as e:
            self.auto_apply_error=str(e);self.show_pending_guidance();self.render_formats()

    def store_dataset_config(self):
        self.store_camera_bindings()
        if self.layer and self.source_config:
            self.layer.setCustomProperty('soracom/source_config',json.dumps(self.source_config,ensure_ascii=False))
            self.layer.setCustomProperty('soracom/formats',json.dumps({'presets':self.schema_store['presets'],'resources':{self.resource_key(b['source']):self.schema_store['resources'].get(self.resource_key(b['source']),{}) for b in self.raw_batches or []},'basis':self.time_basis.currentData()},ensure_ascii=False))
        self.render_formats()

    def update_time_note(self):
        if not hasattr(self,'schema_note'):return
        missing=[]
        for r in self.selected_resources():
            try:
                cfg=self.resource_schema(r)
                if cfg['basis']=='measured' and not cfg['schema'].get('time'):missing.append(r.get('name') or r['resource_id'])
            except DemoError:pass
        self.schema_note.setText(('測位時刻なし：'+ '、'.join(missing)+'。点は表示し、軌跡・画像は対象外です。データ到達時刻への切替が可能です。') if missing else '選んだ時刻を軌跡・映像要求に使用します。元の測位時刻とデータ到達時刻は両方保持します。')

    def restore_dataset_config(self):
        self.restore_camera_bindings()
        self.raw_batches=None;self.schema_pending=False
        try:
            cfg=json.loads(self.layer.customProperty('soracom/source_config','{}'))
            if not cfg or not self.records:return
            batches={}
            names={str(f['resource_id']):str(f['sim_name']) for f in self.layer.getFeatures()}
            for record in self.records.getFeatures():
                key=str(record['source_key']);profile,coverage,resource_type,resource_id=json.loads(key)
                batch=batches.setdefault(key,dict(source=dict(profile=profile,coverage=coverage,resource_type=resource_type,resource_id=resource_id,name=names.get(resource_id,resource_id)),raw_rows=[]))
                batch['raw_rows'].append(json.loads(str(record['raw_entry_json'])))
            self.source_config=cfg;self.raw_batches=list(batches.values())
            saved=json.loads(self.layer.customProperty('soracom/formats','{}'))
            if saved:
                # Restore missing definitions only. Existing local edits stay current;
                # the layer property remains the snapshot actually applied to data.
                imported={}
                for id,spec in saved.get('presets',{}).items():
                    preset(id,{id:spec})
                    target=id
                    if id in self.schema_store['presets'] and self.schema_store['presets'][id]!=spec:
                        target=next((key for key,value in self.schema_store['presets'].items() if value==spec),'custom-'+uuid.uuid4().hex)
                    self.schema_store['presets'].setdefault(target,copy.deepcopy(spec));imported[id]=target
                if 'basis' not in self.schema_store:
                    self.schema_store['basis']=saved.get('basis','measured')
                    self.time_basis.blockSignals(True)
                    self.time_basis.setCurrentIndex(max(0,self.time_basis.findData(self.schema_store['basis'])))
                    self.time_basis.blockSignals(False)
                for key,selection in saved.get('resources',{}).items():
                    entry=copy.deepcopy(selection)
                    entry['format']=imported.get(entry.get('format'),entry.get('format'))
                    self.schema_store['resources'].setdefault(key,entry)
                for key in batches:
                    applied=saved.get('resources',{}).get(key,{})
                    current=self.schema_store['resources'].get(key,{})
                    old_basis=applied.get('basis','inherit');old_basis=saved.get('basis','measured') if old_basis=='inherit' else old_basis
                    new_basis=current.get('basis','inherit');new_basis=self.time_basis.currentData() if new_basis=='inherit' else new_basis
                    if old_basis!=new_basis or preset(applied.get('format'),saved.get('presets',{}))!=preset(current.get('format'),self.schema_store['presets']):
                        self.schema_pending=True
                if all(self.config()[k]==cfg[k] for k in ('profile',)):
                    sources=[b['source'] for b in self.raw_batches]
                    existing=[self.sim_list.item(i).data(Qt.UserRole) for i in range(self.sim_list.count())]
                    self.set_sim_choices(existing+sources,sources)
                self.save_formats();self.render_formats()
                if self.schema_pending:self.schedule_auto_apply()
        except (ValueError,TypeError,KeyError):
            self.raw_batches=None

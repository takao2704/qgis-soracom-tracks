"""SIM-to-camera routing, independent from applied position schemas."""
import copy
import json
from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QListWidget,QListWidgetItem,QPushButton
from .soracom_io import DemoError


class CameraBindings:
    def populate_binding_combo(self, resource):
        combo=self.sim_camera_combo;combo.blockSignals(True);combo.clear()
        combo.addItem('共通カメラを使用','inherit');combo.addItem('連携しない','none')
        cfg=self.schema_store.get('cameras',{}).get(self.resource_key(resource),{})
        mode=cfg.get('mode','inherit');value='camera:'+cfg.get('device_id','') if mode=='fixed' else mode
        if mode=='fixed':
            device=cfg.get('device_id','');label='個別指定 / '+device
            if hasattr(self,'camera_combo'):
                i=self.camera_combo.findData(device)
                if i>=0:label='個別: '+self.camera_combo.itemText(i)
            combo.addItem(label,value)
        combo.setCurrentIndex(max(0,combo.findData(value)));combo.setToolTip(combo.currentText())
        combo.setAccessibleName((resource.get('name') or resource['resource_id'])+'の連携カメラ');combo.blockSignals(False)

    def camera_summary(self,resource):
        cfg=self.schema_store.get('cameras',{}).get(self.resource_key(resource),{})
        mode=cfg.get('mode','inherit')
        if mode=='none':return 'カメラ連携なし'
        device=cfg.get('device_id','') if mode=='fixed' else self.inputs['camera'].text().strip()
        index=self.camera_combo.findData(device) if hasattr(self,'camera_combo') else -1
        name=self.camera_combo.itemText(index).split(' / ')[0] if index>=0 else device or '未選択'
        if mode=='fixed' and (cfg.get('profile')!=self.inputs['profile'].text().strip() or cfg.get('coverage')!=self.inputs['camera_coverage'].text().strip()):name+='（接続設定を確認）'
        return ('個別: ' if mode=='fixed' else '共通: ')+name

    def pick_resource_camera(self):
        resource=self.editing_resource()
        if not resource:return
        dialog=QDialog(self);dialog.setWindowTitle('このSIMの連携カメラ');dialog.resize(640,540);layout=QVBoxLayout(dialog)
        title=QLabel('対象SIM: '+(resource.get('name') or resource['resource_id']));title.setTextFormat(Qt.PlainText);layout.addWidget(title)
        search=QLineEdit();search.setPlaceholderText('カメラ名・IDで絞り込み');search.setAccessibleName('連携カメラの検索');search.setClearButtonEnabled(True);layout.addWidget(search)
        refresh=QPushButton('カメラ一覧を更新');layout.addWidget(refresh)
        choices=QListWidget();choices.setAccessibleName('連携するカメラ');choices.setStyleSheet('QListWidget::item { padding: 5px; }');layout.addWidget(choices,1)
        notice=QLabel();notice.setWordWrap(True);notice.setTextFormat(Qt.PlainText);layout.addWidget(notice)
        hint=QLabel('同じカメラを複数のSIMに指定できます。接続状態がオフラインでも過去の録画を取得できる場合があります。指定時刻の録画の有無は映像取得時に確認します。');hint.setWordWrap(True);layout.addWidget(hint)
        row=QHBoxLayout();cancel=QPushButton('キャンセル');use=QPushButton('選択したカメラをこのSIMに紐づける');row.addWidget(cancel);row.addWidget(use);layout.addLayout(row)
        cancel.setAutoDefault(False);refresh.setAutoDefault(False);use.setDefault(True)
        state={'alive':True,'task':None,'error':''}
        def update(*_):
            visible=0
            for i in range(choices.count()):
                item=choices.item(i);item.setHidden(search.text().strip().casefold() not in item.text().casefold());visible+=not item.isHidden()
            item=choices.currentItem();use.setEnabled(not state['task'] and item is not None and not item.isHidden())
            notice.setText(state['error'] or ('一覧を取得中…' if state['task'] else f'{visible}件の候補' if visible else '一致するカメラがありません。検索条件を変えるか、一覧を更新してください。'))
        def populate():
            selected=choices.currentItem().data(Qt.UserRole) if choices.currentItem() else self.schema_store.get('cameras',{}).get(self.resource_key(resource),{}).get('device_id',self.inputs['camera'].text().strip())
            choices.clear()
            for i in range(self.camera_combo.count()):
                device=self.camera_combo.itemData(i)
                if not device:continue
                item=QListWidgetItem(self.camera_combo.itemText(i));item.setData(Qt.UserRole,device);choices.addItem(item)
                if device==selected:choices.setCurrentItem(item)
            update()
        def reload():
            if state['task']:return
            state['error']='';refresh.setEnabled(False)
            try:
                config=self.config();client=self.client({**config,'coverage':config['camera_coverage']})
                def done(cameras):
                    if not state['alive']:return
                    state['task']=None;self.set_camera_choices(cameras);populate();refresh.setEnabled(True)
                def failed(error):
                    if not state['alive']:return
                    state['task']=None;state['error']=error;refresh.setEnabled(True);update()
                self.launch('カメラ一覧を取得中…',lambda cancel:client.cameras(cancel),done,on_error=failed)
                state['task']=self.task;update()
            except DemoError as e:state['error']=str(e);refresh.setEnabled(True);update()
        def finish(*_):
            state['alive']=False
            if state['task'] and self.task is state['task']:
                self.task.done=lambda *_:None;self.task.cancel();self.task=None;self.busy(False)
        search.textChanged.connect(update);choices.currentItemChanged.connect(update);refresh.clicked.connect(reload);cancel.clicked.connect(dialog.reject);dialog.finished.connect(finish)
        def accept():
            if use.isEnabled():dialog.accept()
        use.clicked.connect(accept);choices.itemDoubleClicked.connect(lambda *_:accept());populate()
        if dialog.exec_()==QDialog.Accepted:self.set_camera_binding(resource,'camera:'+choices.currentItem().data(Qt.UserRole))

    def set_camera_binding(self, resource, value):
        if not isinstance(value,str):return
        binding={'mode':value}
        if value.startswith('camera:'):
            binding=dict(mode='fixed',device_id=value[7:],profile=self.inputs['profile'].text().strip(),coverage=self.inputs['camera_coverage'].text().strip())
        elif value not in ('inherit','none'):return
        self.schema_store.setdefault('cameras',{})[self.resource_key(resource)]=binding
        self.save_formats();self.store_camera_bindings();self.render_formats()
        self.clear_video();self.pixmap=None;self.image_label.clear();self.update_camera_target()
        self.status.setText('このSIMのカメラ設定を保存しました。次の点選択・映像取得から使用します。')

    def effective_camera(self, allow_unselected=False):
        config=self.config();key=(self.point or {}).get('source_key')
        binding=self.schema_store.get('cameras',{}).get(key,{})
        if key:
            try:
                identity=json.loads(key)
                if not isinstance(identity,list) or len(identity)!=4:raise ValueError('source identity')
                profile=identity[0]
            except (ValueError,TypeError,IndexError):raise DemoError('地点の接続情報を確認できません。位置レイヤを読み直してください') from None
            if identity[2] and identity[3] and profile!=config['profile']:raise DemoError('取得時と同じプロファイルを指定してください')
        mode=binding.get('mode','inherit')
        if mode=='none':raise DemoError('このSIMはカメラを連携しない設定です')
        device=config['camera'];coverage=config['camera_coverage']
        if mode=='fixed':
            if binding.get('profile')!=config['profile'] or binding.get('coverage')!=coverage:
                raise DemoError('個別カメラのプロファイル／カバレッジが異なります。元の接続設定に戻すか、連携カメラを指定し直してください')
            device=binding.get('device_id','')
        if not device and not allow_unselected:raise DemoError('共通カメラ、またはこのSIMの連携カメラを選択してください')
        label=device
        idx=self.camera_combo.findData(device)
        if idx>=0:label=self.camera_combo.itemText(idx)
        return dict(device=device,coverage=coverage,profile=config['profile'],mode=mode,label=label)

    def update_camera_target(self):
        if not hasattr(self,'camera_target'):return
        if not self.point:self.camera_target.setText('地点を選ぶと、使用するカメラと要求時刻を表示します。');return
        try:
            target=self.effective_camera();kind='個別指定' if target['mode']=='fixed' else '共通'
            text='この地点のカメラ（'+kind+'）: '+target['label']
            stamp=self.point.get('active_ms')
            if stamp is not None:
                from .soracom_io import utc_text
                if hasattr(self,'media_mode') and self.media_mode.currentData()=='video':
                    start,end=self.video_window();text+='\n動画要求: '+utc_text(start)+' 〜 '+utc_text(end)
                else:text+='\n映像要求: '+utc_text(int(stamp)+round(self.offset.value()*1000))
            self.camera_target.setText(text)
        except DemoError as e:self.camera_target.setText(str(e))

    def store_camera_bindings(self):
        if not self.layer:return
        keys={str(f['source_key']) for f in self.layer.getFeatures()} if 'source_key' in self.layer.fields().names() else set()
        saved={key:copy.deepcopy(value) for key,value in self.schema_store.get('cameras',{}).items() if key in keys}
        self.layer.setCustomProperty('soracom/camera_bindings',json.dumps(saved,ensure_ascii=False))
        QgsProject.instance().setDirty(True)

    def restore_camera_bindings(self):
        if not self.layer:return
        try:
            saved=json.loads(self.layer.customProperty('soracom/camera_bindings','{}'))
            if not isinstance(saved,dict):return
            for key,value in saved.items():
                if isinstance(value,dict) and value.get('mode') in ('inherit','fixed','none'):
                    self.schema_store.setdefault('cameras',{}).setdefault(key,copy.deepcopy(value))
        except (ValueError,TypeError):pass

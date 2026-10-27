"""Camera media controls and local video playback."""
from pathlib import Path
from qgis.PyQt.QtCore import Qt,QUrl,QTimer
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QComboBox,QSpinBox,QPushButton,QLabel,QSlider
from .soracom_io import DemoError,utc_text,atomic_json
import json
from .video_io import cached_video,export_video


class CameraMedia:
    def build_media_controls(self,layout):
        self.video_path=None;self.player=None
        self.media_mode=QComboBox();self.media_mode.addItem('静止画','image');self.media_mode.addItem('動画','video');self.media_mode.setAccessibleName('取得する映像の形式')
        form=QFormLayout();form.addRow('取得する映像',self.media_mode);layout.addLayout(form)
        self.video_range=QWidget();r=QHBoxLayout(self.video_range);r.setContentsMargins(0,0,0,0)
        self.video_before=QSpinBox();self.video_after=QSpinBox()
        for label,spin in [('前',self.video_before),('後',self.video_after)]:
            spin.setRange(0,450);spin.setValue(5);spin.setSuffix(' 秒');spin.setAccessibleName('動画の'+label+'の秒数');r.addWidget(QLabel(label));r.addWidget(spin)
        layout.addWidget(self.video_range);self.video_range.hide()
        self.media_mode.currentIndexChanged.connect(self.media_changed)
        self.video_before.valueChanged.connect(self.media_changed);self.video_after.valueChanged.connect(self.media_changed)

    def build_video_player(self,layout):
        self.video_panel=QWidget();v=QVBoxLayout(self.video_panel);v.setContentsMargins(0,0,0,0)
        self.video_caption=QLabel();self.video_caption.setWordWrap(True);v.addWidget(self.video_caption)
        try:
            from qgis.PyQt.QtMultimedia import QMediaPlayer,QMediaContent
            try:from qgis.PyQt.QtMultimediaWidgets import QVideoWidget
            except ImportError:from PyQt5.QtMultimediaWidgets import QVideoWidget
            self.video_widget=QVideoWidget();self.video_widget.setMinimumHeight(220);v.addWidget(self.video_widget)
            self.player=QMediaPlayer(self);self.player.setVideoOutput(self.video_widget)
            self.player.setMuted(True)
            self.video_seek=QSlider(Qt.Horizontal);self.video_seek.setAccessibleName('動画の再生位置');v.addWidget(self.video_seek)
            self.player.durationChanged.connect(lambda n:self.video_seek.setRange(0,n))
            self.player.positionChanged.connect(self.video_seek.setValue);self.video_seek.sliderMoved.connect(self.player.setPosition)
            self.player.error.connect(self.video_error)
        except ImportError:pass
        row=QHBoxLayout();self.video_play=QPushButton('再生 / 一時停止');self.video_play.setEnabled(self.player is not None);row.addWidget(self.video_play)
        self.video_play.clicked.connect(self.toggle_video)
        if self.player:self.player.stateChanged.connect(lambda state:self.video_play.setText('一時停止' if state==self.player.PlayingState else '再生'))
        self.video_open=QPushButton('外部プレーヤーで開く');row.addWidget(self.video_open);self.video_open.clicked.connect(self.open_video)
        v.addLayout(row);layout.addWidget(self.video_panel);self.video_panel.hide()

    def save_media_preferences(self,*_):
        path=self.storage/'settings.json'
        try:
            data=json.loads(path.read_text()) if path.exists() else {}
            data.update(auto_media=self.auto_image.isChecked(),media_mode=self.media_mode.currentData(),video_before_seconds=self.video_before.value(),video_after_seconds=self.video_after.value())
            atomic_json(path,data)
        except (ValueError,OSError):self.status.setText('映像の設定を保存できませんでした')

    def clear_video(self):
        if getattr(self,'player',None):self.player.stop()
        self.video_path=None
        if hasattr(self,'video_panel'):self.video_panel.hide()

    def media_changed(self,*_):
        video=self.media_mode.currentData()=='video';self.video_range.setVisible(video)
        if not hasattr(self,'image_button'):return
        self.clear_video();self.pixmap=None;self.image_label.clear();self.image_label.setVisible(not video)
        self.image_button.setText('選んだ時刻の動画を取得' if video else '選んだ時刻の画像を取得')
        self.update_camera_target()
        if self.point:self.status.setText('取得形式・範囲を変更しました。点をクリックするか取得ボタンを押してください')

    def video_window(self):
        if not self.point or self.point.get('active_ms') is None:raise DemoError('選択した基準の時刻がありません')
        center=int(self.point['active_ms'])+round(self.offset.value()*1000)
        start=center-self.video_before.value()*1000;end=center+self.video_after.value()*1000
        if start==end:raise DemoError('動画の前後どちらかを1秒以上にしてください')
        return start,end

    def find_video(self):
        self.clear_video()
        if self.sample:self.status.setText('サンプルの動画はありません。APIは呼び出しません');return False
        try:
            target=self.effective_camera();start,end=self.video_window()
            client=self.client({**self.config(),'coverage':target['coverage']})
            result=cached_video(client,target['device'],start,end)
            if result:self.show_video(result,False);return True
            self.status.setText('このカメラ・時間範囲の保存済み動画はありません');return False
        except (DemoError,ValueError) as e:self.status.setText(str(e));return False

    def get_video(self):
        if self.task:return
        if self.schema_pending:self.show_pending_guidance();return
        if self.sample:self.status.setText('サンプルの動画はありません。APIは呼び出しません');return
        try:
            config=self.config()
            if self.source_config and config['profile']!=self.source_config['profile']:raise DemoError('取得時と同じプロファイルを指定してください')
            target=self.effective_camera();start,end=self.video_window()
            client=self.client({**config,'coverage':target['coverage']})
            self.launch('ソラカメ動画を取得中…',lambda cancel:export_video(client,target['device'],start,end,cancel),lambda result:self.show_video(result,True))
        except (DemoError,ValueError) as e:self.status.setText(str(e))

    def show_video(self,result,autoplay=False):
        self.video_path=str(result['path']);self.pixmap=None;self.image_label.clear();self.image_label.hide();self.video_panel.show()
        self.video_caption.setText('録画動画（音声ミュート）\n'+utc_text(result['from_ms'])+' 〜\n'+utc_text(result['to_ms']))
        self.status.setText(('保存済み動画' if result['cached'] else 'ソラカメAPIの動画')+'を表示。要求範囲と実際の録画区間は異なる場合があります。')
        if self.player:
            from qgis.PyQt.QtMultimedia import QMediaContent
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(self.video_path)))
            if autoplay:self.player.play()
        else:self.video_caption.setText(self.video_caption.text()+'\nこの環境では外部プレーヤーで再生してください。')
        QTimer.singleShot(0,lambda:self.scroll.ensureWidgetVisible(self.video_play) if self.video_path else None)

    def video_error(self,*_):
        if self.video_path:self.status.setText('QGIS内で再生できませんでした。「外部プレーヤーで開く」で再生できます。')

    def toggle_video(self):
        if self.player and self.video_path:
            if self.player.state()==self.player.PlayingState:self.player.pause()
            else:self.player.play()

    def open_video(self):
        if self.video_path and Path(self.video_path).is_file():QDesktopServices.openUrl(QUrl.fromLocalFile(self.video_path))

"""Compact native dock: acquisition summary, per-SIM dialogs and media focus."""
import json
from qgis.PyQt.QtCore import Qt,QTimer,pyqtSignal
from qgis.PyQt.QtWidgets import (QLabel,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,
    QPushButton,QDialog,QDialogButtonBox,QGroupBox,QScrollArea,QSizePolicy,QApplication,QStyleFactory)
from .soracom_io import DemoError


class StatusLabel(QLabel):
    changed=pyqtSignal(str)
    def setText(self,text):
        super().setText(text);self.changed.emit(text)


class CompactUI:
    @staticmethod
    def opaque_surface(widget):
        # QWidget is transparent by default. Explicitly repaint the panel's
        # background so scrolled text cannot remain behind fixed controls.
        from qgis.PyQt.QtGui import QPalette
        widget.setBackgroundRole(QPalette.Window)
        widget.setAutoFillBackground(True)

    @staticmethod
    def stable_scroll(area):
        # Native macOS overlay scrollbars can disappear even while content is
        # overflowing. Keep a reserved, non-overlay track inside this plugin.
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setCursor(Qt.ArrowCursor);area.viewport().setCursor(Qt.ArrowCursor)
        CompactUI.opaque_surface(area.viewport())
        area.verticalScrollBar().setCursor(Qt.ArrowCursor)
        # A widget-local non-transient style avoids macOS overlay behavior.
        # Never pass QApplication.style(): QStyle ownership would be transferred.
        area._scroll_style=QStyleFactory.create('Fusion')
        area.verticalScrollBar().setStyle(area._scroll_style)


    def folding(self,title,widgets):
        box=QGroupBox(title);box.setCheckable(True);box.setChecked(False)
        outer=QVBoxLayout(box);body=QWidget();inner=QVBoxLayout(body);inner.setContentsMargins(0,0,0,0)
        for widget in widgets:inner.addWidget(widget)
        outer.addWidget(body);body.hide();box.toggled.connect(body.setVisible)
        return box

    def window_dialog(self,title,widget):
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.setWindowModality(Qt.WindowModal)
        dialog.resize(610,570);layout=QVBoxLayout(dialog);scroll=QScrollArea();scroll.setWidgetResizable(True)
        self.stable_scroll(scroll);dialog.setCursor(Qt.ArrowCursor)
        scroll.setWidget(widget);layout.addWidget(scroll,1)
        notice=QLabel();notice.setTextFormat(Qt.PlainText);notice.setWordWrap(True);layout.addWidget(notice)
        self.status.changed.connect(notice.setText)
        close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(dialog.reject);layout.addWidget(close)
        dialog.setProperty('compact',True)
        return dialog

    def build_compact_ui(self):
        # Reuse the existing widgets and callbacks. The old containers stay owned
        # until shutdown, so authentication/task connections retain their lifetime.
        self._legacy_panel=self.widget();self._legacy_panel.hide()
        self._legacy_body=self.scroll.takeWidget();self._legacy_body.setParent(self);self._legacy_body.hide()
        self.settings_box.setCheckable(False);self.settings_body.show()
        self.connection_dialog=self.window_dialog('接続設定',self.settings_box)
        self.sim_box.setCheckable(False);self.sim_body.show();self.sim_list.setMaximumHeight(360)
        self.sim_dialog=self.window_dialog('SIMを追加・変更',self.sim_box)
        self.sim_dialog.findChild(QDialogButtonBox).hide()
        actions=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        actions.button(QDialogButtonBox.Ok).setText('選択を反映')
        actions.accepted.connect(self.sim_dialog.accept);actions.rejected.connect(self.sim_dialog.reject)
        self.sim_dialog.layout().addWidget(actions);self.sim_dialog.finished.connect(self.finish_sim_selection)
        self._sim_selection_snapshot=None
        self.format_table.hide()
        hint=self.format_box.layout().itemAt(0).widget();hint.setText('このSIMの形式・時刻は取得済みデータへ自動反映します。カメラは次の地点選択から使います。');hint.setWordWrap(True)
        self.editor_dialog=self.window_dialog('SIMごとの設定',self.format_box)
        self.editor_dialog.finished.connect(self.restore_editor_focus)
        self.edit_format_button.setText('サンプル確認・カスタム形式を設定')
        self.schema_note.setWordWrap(True)
        self.editor_dialog.layout().insertWidget(1,self.schema_note)
        self.camera_dialog=self.window_dialog('共通カメラ',self.camera_box)
        # Rehouse the main dock in the same QScrollArea to preserve map tools.
        panel=QWidget();self.opaque_surface(panel);panel.setCursor(Qt.ArrowCursor);root=QVBoxLayout(panel);root.setContentsMargins(8,8,8,8)
        top=QHBoxLayout();title=QLabel('SORACOM Tracks');title.setStyleSheet('font-weight:bold;');top.addWidget(title);top.addStretch()
        self.connection_button=QPushButton('接続設定');self.connection_button.clicked.connect(self.connection_dialog.open);top.addWidget(self.connection_button);root.addLayout(top)
        body=QWidget();self.opaque_surface(body);layout=QVBoxLayout(body);layout.setContentsMargins(0,0,0,0);layout.setAlignment(Qt.AlignTop)
        self.acquisition_box=QGroupBox('次回取得');self.acquisition_box.setCheckable(True);self.acquisition_box.setChecked(True)
        acquisition_outer=QVBoxLayout(self.acquisition_box);self.acquisition_body=QWidget();acq=QVBoxLayout(self.acquisition_body);acq.setContentsMargins(0,0,0,0);acquisition_outer.addWidget(self.acquisition_body)
        self.acquisition_box.toggled.connect(self.acquisition_body.setVisible)
        header=QHBoxLayout();self.next_summary=QLabel();self.next_summary.setWordWrap(True);self.next_summary.setTextFormat(Qt.PlainText);header.addWidget(self.next_summary,1)
        self.manage_button=QPushButton('SIMを追加・変更');self.manage_button.clicked.connect(self.open_sim_selection);header.addWidget(self.manage_button);acq.addLayout(header)
        self.cards=QWidget();self.cards_layout=QVBoxLayout(self.cards);self.cards_layout.setContentsMargins(0,0,0,0)
        # One scroll surface: SIM cards must not consume the wheel inside a
        # second independently scrolling viewport.
        self.cards.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Maximum);acq.addWidget(self.cards)
        self.common_button=QPushButton('共通カメラ');self.common_button.clicked.connect(self.camera_dialog.open);acq.addWidget(self.common_button)
        acq.addWidget(self.period_box)
        self.selection_hint=QLabel();self.selection_hint.setWordWrap(True);self.selection_hint.setTextFormat(Qt.PlainText);acq.addWidget(self.selection_hint)
        tools=QWidget();t=QVBoxLayout(tools);t.setContentsMargins(0,0,0,0);t.addWidget(self.attach_button);t.addWidget(self.sample_button)
        f=QFormLayout();f.addRow('時刻の既定値',self.time_basis);t.addLayout(f)
        t.addWidget(QLabel('SIM設定の「一括設定に合わせる」に使用します。'))
        acq.addWidget(self.folding('その他の操作・既定値',[tools]));layout.addWidget(self.acquisition_box)
        media=QGroupBox('地点と映像');ml=QVBoxLayout(media)
        self.focus_media_button=QPushButton('映像を広く表示');self.focus_media_button.clicked.connect(lambda:self.acquisition_box.setChecked(not self.acquisition_box.isChecked()))
        self.acquisition_box.toggled.connect(lambda checked:self.focus_media_button.setText('映像を広く表示' if checked else '取得条件を表示'))
        ml.addWidget(self.focus_media_button)
        self.point_summary=QLabel('地図上の点を選択してください');self.point_summary.setWordWrap(True);self.point_summary.setTextFormat(Qt.PlainText);ml.addWidget(self.point_summary)
        self.selected.changed.connect(self.update_point_summary)
        form=QFormLayout();form.addRow('取得する映像',self.media_mode);ml.addLayout(form);ml.addWidget(self.video_range)
        ml.addWidget(self.camera_target);ml.addWidget(self.image_label);ml.addWidget(self.video_panel)
        row=QHBoxLayout();row.addWidget(self.pick_button);row.addWidget(self.image_button);ml.addLayout(row)
        media_options=QWidget();mo=QVBoxLayout(media_options);mo.setContentsMargins(0,0,0,0);mo.addWidget(self.auto_image);mo.addWidget(self.selected)
        note=QLabel('新規の静止画1枚は1秒、動画は取得秒数分のエクスポート枠を使用します。指定時刻は録画フレームの時刻を保証しません。');note.setWordWrap(True);mo.addWidget(note)
        ml.addWidget(self.folding('地点の詳細・映像取得設定',[media_options]));layout.addWidget(media)
        self.scroll.setWidget(body);self.stable_scroll(self.scroll);root.addWidget(self.scroll,1)
        self.fixed_footer=QWidget();self.fixed_footer.setObjectName('SoracomFixedFooter');self.opaque_surface(self.fixed_footer)
        self.fixed_footer.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Maximum)
        footer=QVBoxLayout(self.fixed_footer);footer.setContentsMargins(0,6,0,0)
        self.loaded_summary=QLabel('表示中：データ未取得');self.loaded_summary.setTextFormat(Qt.PlainText);self.loaded_summary.setWordWrap(True);footer.addWidget(self.loaded_summary)
        self.status_summary=QLabel();self.status_summary.setTextFormat(Qt.PlainText);self.status_summary.setWordWrap(True);self.status_summary.setAccessibleName('処理結果と次の操作');footer.addWidget(self.status_summary)
        footer.addWidget(self.pending_settings_button)
        self.status.setTextInteractionFlags(Qt.NoTextInteraction);self.status.setCursor(Qt.ArrowCursor)
        self.copy_status_button=QPushButton('処理結果をコピー')
        self.copy_status_button.clicked.connect(lambda:QApplication.clipboard().setText(self.status.text()))
        self.status_details=self.folding('処理結果の詳細',[self.status,self.copy_status_button]);footer.addWidget(self.status_details)
        bottom=QHBoxLayout();bottom.addWidget(self.save_button);bottom.addWidget(self.cancel_button);footer.addLayout(bottom)
        root.addWidget(self.fixed_footer)
        self.setWidget(panel);self.setMinimumWidth(410)
        self.status.changed.connect(self.update_compact_status)
        for key in ('start','end','profile'):self.inputs[key].textChanged.connect(self.update_compact_ui)
        self.inputs['camera'].textChanged.connect(self.update_compact_ui)
        self._card_signature=None;self._compact_busy=False
        self.update_compact_ui();self.update_compact_status(self.status.text())

    def open_sim_selection(self):
        self._sim_selection_snapshot=[dict(r) for r in self.selected_resources()]
        self.sim_dialog.open();self.sim_filter.setFocus()

    def finish_sim_selection(self,result):
        if self._sim_selection_snapshot is None:return
        if self.task:
            self.task.done=lambda *_:None;self.task.cancel();self.task=None;self.busy(False)
        if result!=QDialog.Accepted:
            choices=[self.sim_list.item(i).data(Qt.UserRole) for i in range(self.sim_list.count())]
            self.set_sim_choices(choices,self._sim_selection_snapshot)
        self._sim_selection_snapshot=None;self.update_compact_ui()
        self.manage_button.setFocus()

    def open_resource_settings(self,key):
        for row in range(self.format_table.rowCount()):
            r=self.format_table.item(row,0).data(Qt.UserRole)
            if self.resource_key(r)==key:
                self.format_table.setCurrentCell(row,0);self.show_resource_editor()
                self.editor_dialog.setWindowTitle('SIMごとの設定 — '+(r.get('name') or r['resource_id']))
                self.editor_dialog.open();self.format_combo.setFocus();return

    def restore_editor_focus(self,*_):
        r=self.editing_resource();button=self._card_buttons.get(self.resource_key(r)) if r else None
        if button:button.setFocus(Qt.OtherFocusReason)

    def update_point_summary(self,text):
        lines=text.splitlines();brief=[x for x in lines if not x.startswith(('測位:','到達:','処理基準:'))]
        self.point_summary.setText('\n'.join(brief[:2]) or '地図上の点を選択してください')

    def update_compact_status(self,text):
        if not hasattr(self,'status_summary'):return
        first=text.split('\n')[0]
        if '取得完了 / ' in first:first=first.split(' / ')[0]+'。詳細は「処理結果の詳細」へ。'
        if len(first)>150:first=first[:147]+'…'
        self.status_summary.setText(first)
        self.update_compact_ui()

    @staticmethod
    def period_summary(config):
        start=config.get('start','');end=config.get('end','')
        if not start or not end:return '期間不明'
        # Preserve the supplied timezone instead of silently reinterpreting it.
        return start.replace('T',' ')+' 〜 '+end.replace('T',' ')

    def update_compact_ui(self,*_):
        if not hasattr(self,'cards_layout') or getattr(self,'_compact_rendering',False):return
        self._compact_rendering=True
        try:
            resources=self.selected_resources();invalid=[];summaries=[]
            for r in resources:
                cfg=self.schema_store['resources'].get(self.resource_key(r),{});name=r.get('name') or '…'+r['resource_id'][-6:]
                try:
                    spec=self.resource_schema(r);title={'gnss':'QLM / CLAS','multi':'GPSマルチユニット','beacon':'ビーコンGW'}.get(cfg.get('format'),spec['schema']['name'])
                    basis='測位時刻' if spec['basis']=='measured' else '到達時刻'
                except DemoError:title='形式未設定';basis='';invalid.append(r)
                state=self.resource_apply_state(r) if title!='形式未設定' else '要設定'
                summaries.append((self.resource_key(r),name,title+' · '+basis+' · '+state,self.camera_summary(r),state))
            signature=tuple(summaries)
            if signature!=self._card_signature:
                self._card_signature=signature
                while self.cards_layout.count():
                    item=self.cards_layout.takeAt(0)
                    if item.widget():item.widget().hide();item.widget().deleteLater()
                self._card_buttons={}
                for key,name,summary,camera,state in summaries:
                    card=QWidget();v=QVBoxLayout(card);v.setContentsMargins(4,4,4,6)
                    row=QHBoxLayout();label=QLabel(name);label.setTextFormat(Qt.PlainText);label.setWordWrap(True);label.setStyleSheet('font-weight:bold;');row.addWidget(label,1)
                    button=QPushButton('形式を設定' if state=='要設定' else '設定');button.setAccessibleName(name+'の設定');button.clicked.connect(lambda _,k=key:self.open_resource_settings(k));row.addWidget(button);self._card_buttons[key]=button;v.addLayout(row)
                    label=QLabel(summary+'\nカメラ：'+camera);label.setTextFormat(Qt.PlainText);label.setWordWrap(True);v.addWidget(label);self.cards_layout.addWidget(card)
            self.next_summary.setText(str(len(resources))+' SIM選択中')
            self.acquisition_box.setTitle('次回取得：'+str(len(resources))+' SIM'+(' / 要設定' if invalid else ''))
            name=self.camera_combo.currentText().split(' / ')[0] if self.inputs['camera'].text() else '未選択'
            self.common_button.setText('共通カメラ：'+name+'（変更）')
            self.common_button.setToolTip(self.camera_combo.currentText())
            hint=''
            if invalid:hint='形式未設定のSIMがあります。「形式を設定」から既知の形式を選ぶか、サンプルを取得して項目を設定してください。'
            elif not resources:hint='「SIMを追加・変更」から取得するSIMを選んでください。'
            elif len(resources)>20:hint='SIMは20件以内にしてください。'
            self.selection_hint.setText(hint);self.selection_hint.setVisible(bool(hint))
            self.load_button.setEnabled(not self._compact_busy and not invalid and 1<=len(resources)<=20)
            self.load_button.setText(str(len(resources))+' SIMのHarvestから取得')
            if not self.layer:self.loaded_summary.setText('表示中：データ未取得')
            elif self.sample:self.loaded_summary.setText('表示中：サンプル（架空データ） / '+str(self.layer.featureCount())+'点')
            else:
                batches=self.raw_batches or [];names=[b['source'].get('name') or '…'+b['source']['resource_id'][-6:] for b in batches]
                summary='表示中：'+str(self.layer.featureCount())+'点 / '+(str(len(names))+' SIM' if names else '保存済みレイヤ')
                if names:summary+='（'+'、'.join(names[:2])+('ほか' if len(names)>2 else '')+'）'
                summary+='\n取得済み期間：'+self.period_summary(self.source_config or {})
                loaded_keys={self.resource_key(b['source']) for b in batches};next_keys={self.resource_key(r) for r in resources}
                cfg=self.source_config or {}
                if loaded_keys!=next_keys or any(cfg.get(k)!=self.inputs[k].text().strip() for k in ('start','end','profile')):
                    summary+='\n次回取得の条件は変更済み。現在の地図は前回のデータです。'
                self.loaded_summary.setText(summary)
        finally:self._compact_rendering=False

    def compact_busy(self,value):
        self._compact_busy=value
        if not hasattr(self,'manage_button'):return
        for w in (self.manage_button,self.connection_button,self.common_button,self.cards):w.setEnabled(not value)
        self.cancel_button.setVisible(value);self.update_compact_ui()

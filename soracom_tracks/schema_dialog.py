"""Native Qt custom schema editor with real sample preview and typed attributes."""
import json
import copy
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QComboBox,
    QPushButton,QLabel,QPlainTextEdit,QTableWidget,QTableWidgetItem,QHeaderView,QScrollArea,QWidget,QTabWidget,QGroupBox,QCompleter,QAbstractItemView)
from .schema import validate_spec,normalize_schema,leaf_paths,get_path,MISSING
from .soracom_io import utc_ms,DemoError


class SchemaDialog(QDialog):
    def __init__(self,parent,spec,resource,samples=None):
        super().__init__(parent);self.owner=parent;self.resource=resource;self.valid_spec=None
        self.samples=samples or [];self.definition=None
        self.setWindowTitle('カスタム形式の項目設定');self.resize(1100,780)
        layout=QVBoxLayout(self)
        title=QLabel('対象SIM：'+str(resource.get('name') or resource['resource_id']));title.setTextFormat(Qt.PlainText);title.setWordWrap(True);layout.addWidget(title)
        self.name=QLineEdit(spec.get('name','カスタム')+'（コピー）');self.name.setAccessibleName('保存する形式名')
        form=QFormLayout();form.addRow('保存する形式名',self.name);layout.addLayout(form)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        sample_page=QWidget();sample_layout=QVBoxLayout(sample_page);self.tabs.addTab(sample_page,'1. サンプル')
        hint=QLabel('取得済みのデータを最大3件表示します。別のデータはAPIから取得するか、contentのJSONを貼り付けてください。');hint.setWordWrap(True);sample_layout.addWidget(hint)
        self.fetch=QPushButton('指定期間からサンプルを取得（最大3件）');sample_layout.addWidget(self.fetch);self.fetch.clicked.connect(self.fetch_samples)
        self.sample_text=QPlainTextEdit();self.sample_text.setAccessibleName('確認するサンプルJSON');self.sample_text.setPlaceholderText('{"location":{"lat":35.68,"lon":139.76},"speed":12.3}')
        self.sample_text.setTabChangesFocus(True);self.sample_text.textChanged.connect(self.invalidate);sample_layout.addWidget(self.sample_text,1)
        note=QLabel('取得済みサンプルは元の到達時刻を保持します。contentだけを貼り付けた場合は、確認用の現在時刻を使用します。');note.setWordWrap(True);sample_layout.addWidget(note)
        self.sample_next=QPushButton('サンプルの項目を読んで次へ');sample_layout.addWidget(self.sample_next);self.sample_next.clicked.connect(self.sample_to_fields)

        scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();content=QVBoxLayout(body);scroll.setWidget(body);self.tabs.addTab(scroll,'2. 項目の割り当て')
        hint=QLabel('パスはHarvestのcontent内が起点です。候補を選んで割り当てるか、入力欄に直接指定できます。');hint.setWordWrap(True);content.addWidget(hint)
        picker=QFormLayout();self.target=QComboBox();self.leaf=QComboBox();self.leaf.setEditable(True);self.leaf.setInsertPolicy(QComboBox.NoInsert)
        self.leaf.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);self.leaf.setMinimumContentsLength(18);self.leaf.setAccessibleName('JSON項目の候補')
        picker.addRow('割り当て先',self.target);picker.addRow('サンプルの項目',self.leaf);content.addLayout(picker)
        self.assign_button=QPushButton('選んだ項目のパスを割り当て');content.addWidget(self.assign_button);self.assign_button.clicked.connect(self.assign)
        group=QGroupBox('位置と時刻');form=QFormLayout(group);content.addWidget(group);self.paths={};self.path_samples={}
        for key,label in [('lat','緯度（必須）'),('lon','経度（必須）'),('time','測位時刻（任意）')]:
            edit=QLineEdit(spec.get(key,''));edit.setPlaceholderText({'lat':'例: location.latitude','lon':'例: location.longitude','time':'例: measured_at'}[key]);edit.setAccessibleName(label+'のJSONパス');form.addRow(label,edit);self.paths[key]=edit;edit.textChanged.connect(self.invalidate)
            sample=QLabel();sample.setTextFormat(Qt.PlainText);sample.setWordWrap(True);form.addRow('サンプル値',sample);self.path_samples[key]=sample
        self.time_format=QComboBox()
        for label,key in [('ISO 8601','iso'),('Unix秒','seconds'),('Unixミリ秒','millis'),('既存互換（ISO / 数値ミリ秒）','auto')]:self.time_format.addItem(label,key)
        self.time_format.setCurrentIndex(max(0,self.time_format.findData(spec.get('time_format','iso'))));form.addRow('時刻の形式',self.time_format)
        self.zone=QComboBox();self.zone.addItems(['UTC','Asia/Tokyo']);self.zone.setCurrentText(spec.get('timezone','UTC'));form.addRow('タイムゾーン省略時',self.zone)
        self.time_format.currentIndexChanged.connect(self.invalidate);self.zone.currentIndexChanged.connect(self.invalidate)
        note=QLabel('測位時刻を持たないデータは空欄にし、SIMの時刻基準に「到達時刻」を選べます。');note.setWordWrap(True);form.addRow(note)
        extra=QGroupBox('追加の値（任意）');extra_layout=QVBoxLayout(extra);content.addWidget(extra)
        note=QLabel('速度・電池残量・状態などを保存できます。セルをダブルクリックして編集します。単位は表示用です。');note.setWordWrap(True);extra_layout.addWidget(note)
        self.values=QTableWidget(0,4);self.values.setHorizontalHeaderLabels(['項目名','JSONパス','型','単位（任意）']);self.values.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.values.setMinimumHeight(120);self.values.setMaximumHeight(180);extra_layout.addWidget(self.values)
        self.values.setTabKeyNavigation(False);self.values.setAccessibleName('追加の値の項目設定');self.values.cellChanged.connect(self.invalidate)
        for value in spec.get('values',[]):self.add_value(value)
        row=QHBoxLayout();self.add=QPushButton('値を追加');remove=QPushButton('選択した値を削除');row.addWidget(self.add);row.addWidget(remove);extra_layout.insertLayout(1,row)
        self.add.clicked.connect(lambda:self.add_value());remove.clicked.connect(self.remove_value)
        advanced=QGroupBox('受信機・走行・測位状態（任意）');advanced.setCheckable(True);advanced.setChecked(False)
        advanced_body=QWidget();form=QFormLayout(advanced_body);outer=QVBoxLayout(advanced);outer.addWidget(advanced_body);advanced_body.hide();advanced.toggled.connect(advanced_body.setVisible);content.addWidget(advanced)
        for key,label in [('receiver','受信機名'),('run','走行ID'),('fix','測位状態')]:
            edit=QLineEdit(spec.get(key,''));form.addRow(label,edit);edit.setAccessibleName(label+'のJSONパス');self.paths[key]=edit;edit.textChanged.connect(self.invalidate)
        content.addStretch()
        preview_page=QWidget();preview_layout=QVBoxLayout(preview_page);self.tabs.addTab(preview_page,'3. 変換確認')
        self.preview_summary=QLabel('変換結果を確認してください。');self.preview_summary.setWordWrap(True);preview_layout.addWidget(self.preview_summary)
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);self.preview.setAccessibleName('変換プレビュー');self.preview.setTabChangesFocus(True);details=QGroupBox('詳細ログ');details.setCheckable(True);details.setChecked(False);detail_layout=QVBoxLayout(details);detail_layout.addWidget(self.preview);self.preview.hide();self.preview.setMaximumHeight(150);details.toggled.connect(self.preview.setVisible)
        self.preview_table=QTableWidget();self.preview_table.setTabKeyNavigation(False);self.preview_table.setAccessibleName('サンプルごとの変換結果');self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.preview_table.setWordWrap(True);self.preview_table.verticalHeader().hide();self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows);preview_layout.addWidget(self.preview_table,1);preview_layout.addWidget(details)
        note=QLabel('新しい形式として保存し、このSIMだけに割り当てます。元の形式や他のSIMは変更しません。取得済みの地図には自動反映します。新しく追加したSIMは設定後にメイン画面からデータを取得してください。');note.setWordWrap(True);preview_layout.addWidget(note)
        buttons=QHBoxLayout();check=QPushButton('変換結果を確認');cancel=QPushButton('キャンセル');self.save=QPushButton('保存してこのSIMに割り当て');buttons.addWidget(check);buttons.addWidget(cancel);buttons.addWidget(self.save);layout.addLayout(buttons)
        check.clicked.connect(self.validate);cancel.clicked.connect(self.reject);self.save.clicked.connect(self.save_definition);self.save.setEnabled(False)
        self.message=QLabel('サンプル → 項目の割り当て → 変換確認の順で設定します。');self.message.setWordWrap(True);self.message.setTextFormat(Qt.PlainText);layout.addWidget(self.message)
        self.name.textChanged.connect(self.invalidate)
        for button in self.findChildren(QPushButton):button.setAutoDefault(False)
        for combo in self.findChildren(QComboBox):combo.setFocusPolicy(Qt.StrongFocus)
        self.target.currentIndexChanged.connect(self.sync_candidate);self.leaf.currentTextChanged.connect(self.update_assign_button)
        self.show_samples();self.read_paths()

    def sample_to_fields(self):
        if self.read_paths():self.tabs.setCurrentIndex(1)

    def invalidate(self,*_):
        self.valid_spec=None
        if hasattr(self,'path_samples'):self.update_samples()
        if hasattr(self,'save'):self.save.setEnabled(False)
        if hasattr(self,'preview_summary'):self.preview_summary.setText('設定が変更されています。もう一度変換結果を確認してください。')
        if hasattr(self,'message'):self.message.setText('設定を変更しました。変換結果を確認してください。')

    def add_value(self,value=None):
        if self.values.rowCount()>=50:return
        value=value or {};row=self.values.rowCount();self.values.insertRow(row)
        for col,key in [(0,'name'),(1,'path'),(3,'unit')]:self.values.setItem(row,col,QTableWidgetItem(value.get(key,'')))
        combo=QComboBox()
        for name,key in [('数値','number'),('文字列','string'),('真偽値','boolean')]:combo.addItem(name,key)
        combo.setCurrentIndex(max(0,combo.findData(value.get('type','number'))));combo.currentIndexChanged.connect(self.invalidate);self.values.setCellWidget(row,2,combo)
        self.invalidate()
        if hasattr(self,'target') and hasattr(self,'paths'):self.targets()

    def remove_value(self):
        row=self.values.currentRow()
        if row>=0:self.values.removeRow(row);self.targets();self.invalidate()

    def targets(self):
        old=self.target.currentData();self.target.blockSignals(True);self.target.clear()
        for key,name in [('lat','緯度'),('lon','経度'),('time','測位時刻'),('receiver','受信機'),('run','走行ID'),('fix','測位状態')]:self.target.addItem(name,key)
        for i in range(self.values.rowCount()):self.target.addItem('追加の値: '+(self.values.item(i,0).text() or str(i+1)),i)
        self.target.setCurrentIndex(max(0,self.target.findData(old)));self.target.blockSignals(False);self.sync_candidate()

    def spec(self):
        result=dict(name=self.name.text().strip(),kind='custom',time_format=self.time_format.currentData(),timezone=self.zone.currentText(),values=[])
        result.update({k:v.text().strip() for k,v in self.paths.items()})
        if not result['name']:raise ValueError('形式名を入力してください')
        for i in range(self.values.rowCount()):
            result['values'].append(dict(name=self.values.item(i,0).text().strip(),path=self.values.item(i,1).text().strip(),type=self.values.cellWidget(i,2).currentData(),unit=self.values.item(i,3).text().strip()))
        return validate_spec(result)

    def entries(self):
        value=json.loads(self.sample_text.toPlainText())
        # UI always displays a wrapper, so a payload containing "time" and "content" is unambiguous.
        if isinstance(value,dict) and set(value)=={'harvest_entries'}:return value['harvest_entries']
        values=value if isinstance(value,list) else [value]
        from datetime import datetime,timezone
        now=utc_ms(datetime.now(timezone.utc).isoformat())
        return [dict(time=now,content=v) for v in values]

    def show_samples(self):
        shown=copy.deepcopy(self.samples[:3])
        for row in shown:
            if isinstance(row,dict) and isinstance(row.get('content'),str):
                try:row['content']=json.loads(row['content'])
                except ValueError:pass
        self.sample_text.setPlainText(json.dumps({'harvest_entries':shown},ensure_ascii=False,indent=2) if shown else '')

    def read_paths(self):
        self.targets();self.leaf.clear();self.leaf.addItem('項目を選択してください',None)
        try:
            entries=self.entries()
            if not isinstance(entries,list) or not entries:raise ValueError('サンプルを取得するかJSONを貼り付けてください')
            for row in entries[:3]:
                payload=row['content'];payload=json.loads(payload) if isinstance(payload,str) else payload
                for path,value in leaf_paths(payload):
                    if self.leaf.findData(path)<0:self.leaf.addItem(path+' : '+json.dumps(value,ensure_ascii=False)[:60],path)
            completer=self.leaf.completer();completer.setCompletionMode(QCompleter.PopupCompletion);completer.setFilterMode(Qt.MatchContains);completer.setCaseSensitivity(Qt.CaseInsensitive)
            self.sync_candidate();self.update_samples()
            self.message.setText(str(min(3,len(entries)))+'件を読み込みました。現在の割り当てとサンプル値を確認し、必要な項目を変更してください。')
            return True
        except (ValueError,TypeError,KeyError) as e:
            self.message.setText('サンプルを確認してください: '+str(e));return False

    def sync_candidate(self,*_):
        if not hasattr(self,'paths'):return
        target=self.target.currentData()
        path=self.values.item(target,1).text() if isinstance(target,int) and target<self.values.rowCount() else self.paths[target].text() if target in self.paths else ''
        self.leaf.setCurrentIndex(max(0,self.leaf.findData(path)))
        self.update_assign_button()

    def update_assign_button(self,*_):
        if hasattr(self,'assign_button'):
            i=self.leaf.findText(self.leaf.currentText())
            self.assign_button.setEnabled(i>=0 and bool(self.leaf.itemData(i)))

    def update_samples(self):
        try:
            rows=self.entries();payload=rows[0]['content'] if rows else {}
            if isinstance(payload,str):payload=json.loads(payload)
        except (ValueError,TypeError,KeyError,IndexError):payload={}
        for key,label in self.path_samples.items():
            try:
                path=self.paths[key].text().strip();value=get_path(payload,path)
                text='未指定' if not path else 'このパスはサンプルにありません' if value is MISSING else json.dumps(value,ensure_ascii=False)
            except ValueError:text='パスの書式を確認してください'
            label.setText(text[:160]);label.setToolTip(text)

    def fill_preview_table(self,rows,spec):
        headers=['記録','緯度\n'+spec['lat'],'経度\n'+spec['lon'],'測位時刻 (UTC)\n'+(spec.get('time') or '未指定'),'到達時刻 (UTC)\nHarvest time']
        headers += [v['name']+(' ('+v.get('unit')+')' if v.get('unit') else '')+'\n'+v['path'] for v in spec.get('values',[])]
        headers+=['変換状態']
        table=self.preview_table;table.setColumnCount(len(headers));table.setHorizontalHeaderLabels(headers);table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            recs=[];points,_=normalize_schema([row],spec,self.resource,recs,'received');rec=recs[0];point=points[0] if points else {}
            vals=[str(i+1),str(point.get('lat','—')),str(point.get('lon','—')),rec['measured_at'] or 'なし',rec['received_at'] or 'なし']
            states=json.loads(rec['value_status_json']);status_names={'ok':'正常','missing':'欠落','null':'null','invalid':'変換失敗'}
            for key,definition in rec.get('extra_definitions',{}).items():
                value=rec['extra_values'][key];state=states[key];vals.append((str(value) if value is not None else '—')+'\n'+status_names.get(state,state))
            while len(vals)<len(headers)-1:vals.append('—')
            vals.append(' / '.join(filter(None,[rec['reason'],rec['time_error']])) or '正常')
            for j,value in enumerate(vals):
                display=value.replace('T','\n') if j in (3,4) else value
                item=QTableWidgetItem(display);item.setToolTip(headers[j]+'\n'+value);table.setItem(i,j,item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.resizeColumnsToContents()
        for col in range(len(headers)):table.setColumnWidth(col,min(300,max(55,table.columnWidth(col)+12)))
        table.resizeRowsToContents()

    def assign(self):
        index=self.leaf.findText(self.leaf.currentText());path=self.leaf.itemData(index) if index>=0 else None;target=self.target.currentData()
        if not path:self.message.setText('サンプルの候補から項目を選択してください。直接入力は各パス欄を使えます。');return
        if isinstance(target,int):self.values.item(target,1).setText(path)
        elif target in self.paths:self.paths[target].setText(path)
        self.message.setText(self.target.currentText()+'に '+path+' を割り当てました。')

    def fetch_samples(self):
        try:
            cfg=self.owner.config();cfg['coverage']=self.resource.get('coverage',cfg['coverage']);client=self.owner.client(cfg);start=utc_ms(cfg['start']);end=utc_ms(cfg['end'])
            if not 0<end-start<=86400000:raise ValueError('取得期間は24時間以内で指定してください')
            resource=dict(self.resource);self.fetch.setEnabled(False);self.save.setEnabled(False)
            def done(rows):
                self.fetch.setEnabled(True);self.save.setEnabled(False)
                if not isinstance(rows,list):failed('サンプル応答が不正です');return
                self.samples=rows[:3];self.show_samples();self.read_paths();self.message.setText(f'{len(self.samples)}件を取得しました。変換結果を確認してください。')
            def failed(message):
                self.fetch.setEnabled(True);self.save.setEnabled(False);self.message.setText(message)
            # Parent task cancellation and close handler prevent callbacks into a deleted editor.
            self.owner.launch('形式確認用サンプルを取得中…',lambda cancel:client.runner(['data','get-entries','--resource-type',resource['resource_type'],'--resource-id',resource['resource_id'],'--from',str(start),'--to',str(end),'--sort','desc','--limit','3'],cancel),done,on_error=failed)
        except (ValueError,DemoError) as e:
            self.fetch.setEnabled(True);self.save.setEnabled(False);self.message.setText(str(e))

    def validate(self):
        try:
            spec=self.spec();rows=self.entries()
            if not isinstance(rows,list) or not rows:raise ValueError('サンプルがありません。APIから取得するかJSONを貼り付けてください')
            records=[];points,bad=normalize_schema(rows[:3],spec,self.resource,records,'received')
            lines=[f'位置あり {len(points)} / {len(records)}件、位置なし {bad}件（記録として保持）']
            for i,rec in enumerate(records,1):
                lines.append(f"{i}: 測位 {rec['measured_at'] or 'なし'} / 受信 {rec['received_at']} / {rec['reason']}")
                if rec['time_error']:lines.append('  時刻の確認: '+rec['time_error'])
                for key,definition in rec.get('extra_definitions',{}).items():lines.append(f"  {definition['name']}: {rec['extra_values'][key]!r} {definition.get('unit','')} ({json.loads(rec['value_status_json'])[key]})")
            for i,point in enumerate(points,1):lines.append(f"位置 {i}: 緯度 {point['lat']} / 経度 {point['lon']}")
            self.preview.setPlainText('\n'.join(lines));self.fill_preview_table(rows[:3],spec);self.valid_spec=spec
            self.preview_summary.setText(f'地図に表示できる位置: {len(points)} / {len(records)}件・追加の値: {len(spec.get("values",[]))}項目')
            self.save.setEnabled(True);self.tabs.setCurrentIndex(2)
            self.message.setText(('有効な位置を作れません。地図には表示されず、受信記録として保持されます。確認後に保存できます。' if not points else '確認しました。')+' 保存するとこのSIMに割り当てます。他のSIMの形式は変更しません。')
        except (ValueError,TypeError,KeyError) as e:self.valid_spec=None;self.save.setEnabled(False);self.message.setText(str(e))

    def save_definition(self):
        if self.valid_spec is None:self.message.setText('保存前に変換結果を確認してください');return
        self.definition=self.valid_spec;self.accept()

    def done(self,result):
        if self.owner.task:
            self.owner.task.done=lambda *_:None;self.owner.task.cancel();self.owner.task=None;self.owner.busy(False)
        super().done(result)

"""Connection widgets and encrypted QGIS authentication storage."""
import json
from pathlib import Path
from qgis.core import QgsApplication, QgsAuthMethodConfig
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QLabel,QLineEdit,QComboBox,
    QPushButton,QWidget,QDialogButtonBox,QCompleter)
from .auth import ApiSession, login, provision, session_login, PERMISSIONS
from .soracom_io import DemoError, atomic_json

class Vault:
    def __init__(self):self.manager=QgsApplication.authManager()
    def unlock(self):
        if self.manager.isDisabled() or not self.manager.setMasterPassword(True):
            raise DemoError('QGISの認証ストアを解除してください。認証情報は平文では保存しません')
    def save(self, credentials, authcfg=''):
        self.unlock()
        config=QgsAuthMethodConfig();config.setMethod('Basic');config.setName('GNSS Tracks for SORACOM')
        config.setConfig('username','soracom-tracks');config.setConfig('password',json.dumps(credentials))
        if authcfg:config.setId(authcfg)
        ok,config=self.manager.storeAuthenticationConfig(config,bool(authcfg))
        if not ok:raise DemoError('QGIS認証ストアへの保存に失敗しました。保存を再試行してください')
        return config.id()
    def load(self, authcfg):
        self.unlock();config=QgsAuthMethodConfig()
        ok,config=self.manager.loadAuthenticationConfig(authcfg,config,True)
        if not ok or config.method()!='Basic' or config.config('username')!='soracom-tracks':
            raise DemoError('保存した認証情報がありません。接続設定から設定してください')
        try:return json.loads(config.config('password'))
        except ValueError:raise DemoError('保存した認証情報を読めません') from None

class AuthDialog(QDialog):
    def __init__(self, owner, mode):
        super().__init__(owner);self.owner=owner;self.mode=mode;self.running=False;self.pending=None;self.state=None;self.ref=''
        self.setWindowTitle('SORACOMへのログイン' if mode in ('login','session') else 'API認証情報の設定');self.setMinimumWidth(570)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.kind=QComboBox()
        if mode in ('login','session'):self.kind.addItems(['SAMユーザー'] if mode=='session' else ['SAMユーザー','ルートユーザー'])
        else:self.kind.addItems(['認証キー ID / シークレット（継続利用）','APIキー / APIトークン（期限付き）'])
        form.addRow('認証情報',self.kind)
        self.fields={};self.labels={}
        for key,label,secret in [('operatorId','オペレーターID',False),('userName','ユーザー名',False),('email','メールアドレス',False),('password','パスワード',True),('mfaOTPCode','MFAコード（設定時）',True),('authKeyId','認証キー ID',True),('authKey','認証キー シークレット',True),('apiKey','APIキー',True),('token','APIトークン',True)]:
            edit=QLineEdit();edit.setAccessibleName(label)
            if secret:edit.setEchoMode(QLineEdit.Password)
            self.fields[key]=edit;self.labels[key]=QLabel(label);form.addRow(self.labels[key],edit)
        self.note=QLabel();self.note.setWordWrap(True);layout.addWidget(self.note)
        if mode=='login':
            policy=QLabel('作成する専用SAMの権限:\n'+'\n'.join(PERMISSIONS));policy.setWordWrap(True);layout.addWidget(policy)
        self.status=QLabel();self.status.setWordWrap(True);self.status.setTextFormat(Qt.PlainText);layout.addWidget(self.status)
        self.submit=QPushButton('このSAMでログイン' if mode=='session' else 'ログインして専用SAM・認証キーを作成' if mode=='login' else '認証を確認して保存');layout.addWidget(self.submit);self.submit.clicked.connect(self.start)
        self.close_button=QPushButton('閉じる');layout.addWidget(self.close_button);self.close_button.clicked.connect(self.reject)
        self.kind.currentIndexChanged.connect(self.changed);self.changed()
    def changed(self):
        sam=self.kind.currentIndex()==0
        shown=({'operatorId','userName','password','mfaOTPCode'} if sam else {'email','password','mfaOTPCode'}) if self.mode in ('login','session') else ({'authKeyId','authKey'} if sam else {'apiKey','token'})
        for key,edit in self.fields.items():edit.setVisible(key in shown);self.labels[key].setVisible(key in shown)
        self.note.setText('専用SAMを新規作成します。既存ユーザーの権限は変更しません。SAMでログインする場合はユーザー作成・権限設定・認証キー発行権限が必要です。パスワードは保存しません。' if self.mode in ('login','session') else 'QGISの暗号化された認証ストアに保存します。認証キーはAPIトークンを自動更新できます。APIキーとAPIトークンを直接指定した場合は、有効期限後に再入力が必要です。')
        if self.mode=='session':self.note.setText('このSAMの権限でAPIに直接アクセスします。専用SAMや認証キーは作りません。パスワード・トークンは保存せず、QGIS終了時またはログアウトで破棄します。有効期限は1時間で、期限後は再ログインが必要です。')
    def reject(self):
        if self.running:return
        if self.pending:
            self.status.setText('発行済みの認証情報が未保存です。保存を再試行してください。');return
        for field in self.fields.values():field.clear()
        super().reject()
    def start(self):
        if self.running:return
        if self.pending:self.finish_save();return
        required=({'operatorId','userName','password'} if self.kind.currentIndex()==0 else {'email','password'}) if self.mode in ('login','session') else ({'authKeyId','authKey'} if self.kind.currentIndex()==0 else {'apiKey','token'})
        values={k:self.fields[k].text() for k in required}
        if any(not v.strip() for v in values.values()):self.status.setText('必要な認証情報を入力してください');return
        if self.mode in ('login','session') and self.fields['mfaOTPCode'].text():values['mfaOTPCode']=self.fields['mfaOTPCode'].text().strip()
        try:
            # Verify that encrypted storage is writable before making cloud changes.
            if self.mode!='session':self.ref=Vault().save({'kind':'pending'})
        except DemoError as e:self.status.setText(str(e));return
        self.running=True;self.submit.setEnabled(False);self.kind.setEnabled(False);self.close_button.setEnabled(False)
        for field in self.fields.values():field.clear();field.setEnabled(False)
        self.status.setText('認証と設定を確認しています…')
        def work(cancel):
            try:
                if self.mode=='session':return session_login(values,cancel),None
                if self.mode=='login':return provision(values,self.owner.storage/'provision.json',cancel)
                credentials={'kind':'key' if 'authKey' in values else 'token',**values}
                if credentials['kind']=='key':login(values)
                else:
                    api=ApiSession(credentials);failures=[]
                    for coverage in ('jp','g'):
                        try:api.call(coverage,'GET','/subscribers?limit=1',cancelled=cancel);break
                        except DemoError as e:failures.append(str(e))
                    else:raise DemoError('API認証を確認できません。'+' / '.join(failures))
                return credentials,None
            finally:values.clear()
        def failed(message):
            self.running=False;self.submit.setEnabled(True);self.close_button.setEnabled(True);self.kind.setEnabled(True)
            for field in self.fields.values():field.setEnabled(True)
            try:QgsApplication.authManager().removeAuthenticationConfig(self.ref) if self.ref else None
            except Exception:pass
            self.ref='';self.status.setText(message)
        def done(result):
            self.pending,self.state=result;self.running=False;self.submit.setEnabled(True)
            if self.mode=='session':
                try:self.owner.apply_session(self.pending)
                finally:self.pending.clear();self.pending=None
                self.accept()
            else:self.finish_save()
        def dispose(result):
            credentials,_=result
            if self.mode=='session':
                ApiSession(credentials).close();credentials.clear()
        if self.mode=='session':self.owner.launch('SORACOMの認証を設定中…',work,done,failed,dispose=dispose)
        else:self.owner.launch('SORACOMの認証を設定中…',work,done,failed)
    def finish_save(self):
        try:
            self.ref=Vault().save(self.pending,self.ref)
            # Persist reference before switching the current connection; no secret enters settings.
            self.owner.apply_auth(self.mode,self.ref,self.pending)
            if self.state:
                self.state['stage']='saved';atomic_json(self.owner.storage/'provision.json',self.state)
            self.pending=None;self.accept()
        except (DemoError,OSError):
            self.status.setText('認証情報の保存を完了できませんでした。発行は繰り返さず、保存を再試行してください。')
            self.submit.setText('保存を再試行');self.close_button.setEnabled(False)

class AuthUI:
    def build_auth_ui(self, form):
        self.auth_ref='';self.api_session=None;self.cli_profile='';self.auth_identity='';self.auth_contexts={};self.auth_generation=0
        QgsApplication.instance().aboutToQuit.connect(self.end_auth_session)
        self.auth_mode=QComboBox()
        for text,key in [('セットアップ済みのCLI設定を使う','cli'),('専用SAM・認証キーを作成して継続利用','login'),('API認証情報を入力','api'),('既存SAMでログイン（終了時にログアウト）','session')]:self.auth_mode.addItem(text,key)
        form.addRow('認証方法',self.auth_mode)
        self.cli_widget=QWidget();cli_form=QFormLayout(self.cli_widget);cli_form.setContentsMargins(0,0,0,0)
        cli_form.addRow('CLI',self.inputs['cli']);cli_form.addRow('プロファイル',self.inputs['profile']);form.addRow(self.cli_widget)
        self.inputs['cli'].show();self.inputs['profile'].show()
        profiles=[p.stem for p in (Path.home()/'.soracom').glob('*.json') if p.is_file() and not p.name.startswith('.')]
        self.inputs['profile'].setCompleter(QCompleter(profiles,self))
        self.auth_button=QPushButton('認証情報を設定');form.addRow(self.auth_button)
        self.auth_status=QLabel();self.auth_status.setWordWrap(True);form.addRow(self.auth_status)
        note=QLabel('SIMは日本・グローバルの両方から自動取得します。');note.setWordWrap(True);form.addRow(note)
        self.auth_button.clicked.connect(self.open_auth)
        self.logout_button=QPushButton('ログアウト');self.logout_button.clicked.connect(self.logout_session);form.addRow(self.logout_button)
    def restore_auth(self, config):
        self.auth_ref=config.get('auth_ref','');self.cli_profile=config.get('cli_profile',config.get('profile',''))
        self.auth_identity=config.get('auth_identity','')
        self.auth_mode.setCurrentIndex(max(0,self.auth_mode.findData(config.get('auth_mode','cli'))))
        self.auth_mode.currentIndexChanged.connect(self.auth_changed);self.show_auth_state()
    def show_auth_state(self):
        cli=self.auth_mode.currentData()=='cli';self.cli_widget.setVisible(cli);self.auth_button.setVisible(not cli)
        self.auth_button.setText('SAMでログイン' if self.auth_mode.currentData()=='session' else 'ログインして専用SAMを設定' if self.auth_mode.currentData()=='login' else 'API認証情報を設定')
        session=self.auth_mode.currentData()=='session'
        self.logout_button.setVisible(session);self.logout_button.setEnabled(session and self.api_session is not None)
        self.auth_status.setText('既存のCLI設定を使用します。' if cli else ('QGIS認証ストアに保存済み。' if self.auth_ref else '認証情報を設定してください。'))
        if session:self.auth_status.setText('SAMでログイン中。終了時にログアウトします。' if self.api_session else '未ログインです。SAMでログインしてください。')
    def auth_changed(self):
        if self.api_session and (self.api_session.credentials.get('kind')=='session' or self.auth_mode.currentData()=='session'):self.end_auth_session()
        current=self.inputs['profile'].text().strip()
        if not current.startswith(('qgis-auth:','qgis-session:')):self.cli_profile=current
        target=self.cli_profile if self.auth_mode.currentData()=='cli' else self.auth_identity or 'qgis-auth:未設定'
        if target!=current:
            self.auth_contexts[current]=dict(
                resources=[self.sim_list.item(i).data(Qt.UserRole) for i in range(self.sim_list.count())],
                selected=self.selected_resources(),camera=self.inputs['camera'].text(),
                cameras=[dict(device_id=self.camera_combo.itemData(i),name=self.camera_combo.itemText(i).split(' / ')[0],status=self.camera_combo.itemText(i).split(' / ')[1]) for i in range(1,self.camera_combo.count())])
            self.inputs['profile'].setText(target)
            previous=self.auth_contexts.get(target)
            if previous:
                self.set_sim_choices(previous['resources'],previous['selected'])
                self.inputs['camera'].setText(previous['camera']);self.set_camera_choices(previous['cameras'])
        self.show_auth_state()
    def open_auth(self):AuthDialog(self,self.auth_mode.currentData()).exec_()
    def apply_auth(self, mode, ref, credentials):
        self.end_auth_session()
        identity='qgis-auth:'+ref
        if self.auth_mode.currentData()=='cli':self.cli_profile=self.inputs['profile'].text().strip()
        path=self.storage/'settings.json'
        data=json.loads(path.read_text()) if path.exists() else {}
        data.update(auth_mode=mode,auth_ref=ref,auth_identity=identity,profile=identity,cli_profile=self.cli_profile,selected_resources=[],camera='')
        atomic_json(path,data)
        self.auth_ref=ref;self.auth_identity=identity;self.api_session=ApiSession(credentials)
        self.auth_mode.blockSignals(True);self.auth_mode.setCurrentIndex(self.auth_mode.findData(mode));self.auth_mode.blockSignals(False)
        self.inputs['profile'].setText(identity);self.show_auth_state()
        self.status.setText('認証情報を適用しました。SIM一覧を取得してください。')
    def auth_config(self):
        return dict(auth_mode=self.auth_mode.currentData(),auth_ref=self.auth_ref,auth_identity=self.auth_identity,
                    cli_profile=self.inputs['profile'].text().strip() if self.auth_mode.currentData()=='cli' else self.cli_profile)
    def api_runner(self,coverage):
        if self.auth_mode.currentData()=='cli':return None
        if self.auth_mode.currentData()=='session':
            if self.api_session is None:raise DemoError('接続設定からSAMでログインしてください')
            return self.api_session.runner(coverage)
        if not self.auth_ref:raise DemoError('接続設定から認証情報を設定してください')
        if self.api_session is None:self.api_session=ApiSession(Vault().load(self.auth_ref))
        return self.api_session.runner(coverage)

    def apply_session(self, credentials):
        self.end_auth_session()
        if self.auth_mode.currentData()=='cli':self.cli_profile=self.inputs['profile'].text().strip()
        self.auth_ref='';self.auth_identity='qgis-session:'+credentials['identity']
        self.auth_mode.blockSignals(True);self.auth_mode.setCurrentIndex(self.auth_mode.findData('session'));self.auth_mode.blockSignals(False)
        self.inputs['profile'].setText(self.auth_identity)
        self.api_session=ApiSession(credentials)
        self.show_auth_state()
        self.save_settings()  # Only the mode and non-secret identity are persisted.
        self.status.setText('SAMでログインしました。SIM一覧を取得してください。'+
                            (' 一部のカバレッジでログインできませんでした: '+', '.join(credentials['failures']) if credentials.get('failures') else ''))

    def end_auth_session(self):
        self.auth_generation+=1
        if self.api_session:
            session=self.api_session;self.api_session=None
            return session.close()
        return []

    def logout_session(self):
        if self.task:self.task.cancel()
        failures=self.end_auth_session()
        self.clear_video();self.pixmap=None;self.image_label.clear()
        self.auth_contexts.pop(self.auth_identity,None)
        self.set_sim_choices([],[]);self.clear_camera_context()
        self.show_auth_state()
        self.status.setText('ログアウトしました。再利用するには接続設定からログインしてください。'+
                            (' サーバー側の失効確認に失敗しました。PC内のトークンは破棄済みで、発行後1時間以内に期限切れになります。' if failures else ''))

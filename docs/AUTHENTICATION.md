# 認証と権限

## 既存CLIの利用

設定済みのSORACOM CLIとプロファイルを使います。秘密情報はCLI側が管理し、プラグインに複製しません。CLIを配置したパスを接続設定で指定できます。

## API認証情報の入力

- 認証キーID／シークレット：必要に応じて短期トークンを更新します。
- APIキー／APIトークン：期限付きの組を使います。期限後は再入力してください。

QGIS認証ストアに暗号化保存し、設定ファイルには参照IDだけを記録します。QGISのマスターパスワードを設定・解除できなければ保存を停止します。平文保存への切り替えはありません。

## SAM／ルートでログイン

ログイン後、`qgis-tracks-` で始まる専用SAMユーザーを作成します。既存ユーザーの権限は変更しません。権限設定を読み戻して確認してから認証キーを発行し、QGIS認証ストアへ保存します。ログインパスワードは保存しません。

発行先の権限は次のAPIです。全SIM・カメラを対象とし、特定リソースへの制限は設定しません。

- `Subscriber:listSubscribers`
- `DataEntry:getDataEntries`
- `SoraCam:listSoraCamDevices`
- `SoraCam:exportSoraCamDeviceRecordedImage`
- `SoraCam:getSoraCamDeviceExportedImage`
- `SoraCam:exportSoraCamDeviceRecordedVideo`
- `SoraCam:getSoraCamDeviceExportedVideo`
- `OAuth2:authorize`

ログイン元のSAMには専用ユーザーの作成・確認、権限の設定・確認、認証キー発行の管理権限が必要です。発行先にSIM変更・削除やSAM管理の権限は付与しません。既存キーを直接入力した場合はその権限を変更しません。

作成中断時はQGISプロファイル内の `soracom_tracks/provision.json` にある専用SAM名と段階を確認します。発行結果が不明な要求は自動再送しません。画面の案内に従って管理画面で該当する専用SAMを確認し、不要なものを削除してから再実行してください。アプリ内で発行済み・未保存の場合は「保存を再試行」を使います。

プラグイン削除や接続変更で専用SAM・キーを自動削除しません。不要な認証情報はSORACOM管理画面で失効してください。

## 検証範囲

CLI経由のHarvest取得、ソラカメ画像・動画の取得・キャッシュ・表示はmacOSの実環境で確認しています。専用SAM発行と直接認証はAPIを置き換えた自動テスト、QGIS認証ストアはアダプターによるテストを含みます。すべての認証方式を実アカウントで一貫検証したものではありません。

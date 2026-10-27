# データ形式

以下はすべて説明用の架空データです。Harvestの `content` に保存するJSONを示しています。

## QLM・CLAS共通形式

```json
{
  "measured_at": "2026-01-01T00:00:00Z",
  "lat": 35.0,
  "lon": 139.0,
  "receiver": "RTK",
  "run": "example-drive",
  "fix_status": "FIX",
  "position_valid": true
}
```

`measured_at` はタイムゾーン付きISO日時、または2000〜2099年のUnixミリ秒です。`lat` / `lon` は有限の緯度・経度です。`position_valid=false`、`NO_FIX`、`INCOMPLETE` は位置として採用しません。無効な記録も全受信記録に残します。

`source=qlm29h_nmea` の旧QLM形式では、有効なRMCの日付とGGAの時刻を組み合わせます。チェックサム・状態・時刻の整合性を確認し、日付境界を補正します。任意のNMEA文字列を自動解析する機能ではありません。

## カスタム形式

```json
{
  "location": {"latitude": 35.0, "longitude": 139.0, "time": "2026-01-01T00:00:00Z"},
  "sensors": {"speed": 12.5, "state": "moving", "active": true}
}
```

| 設定 | パス | 型・形式 |
| --- | --- | --- |
| 緯度 | `location.latitude` | 数値 |
| 経度 | `location.longitude` | 数値 |
| 測位時刻（任意） | `location.time` | ISO日時 |
| 速度（任意） | `sensors.speed` | 数値 |
| 状態（任意） | `sensors.state` | 文字列 |
| 稼働（任意） | `sensors.active` | 真偽値 |

JSONパスは `content` を含めず指定します。固定の配列添字（`values[0].value`）は使えます。ワイルドカード、式の実行、配列内の全要素を複数地点に展開する指定には対応しません。

時刻はISO日時、Unix秒、Unixミリ秒を明示します。ISO日時に時差がなければ、設定したUTC／Asia/Tokyoを使います。数値を秒かミリ秒か推測しません。追加属性の単位は表示用で、値の換算はしません。欠落・null・型エラーを区別し、0で埋めません。

## GPSマルチユニット・ビーコン対応GPSトラッカーGW

HarvestにJSON変換済みで保存されているデータを扱います。バイナリの直接デコードは行いません。

- GPSマルチユニット：`lat` / `lon`、温度・湿度・電池残量等に対応。標準JSONに測位日時がないため、通常は到達時刻を選びます。
- ビーコン対応GPSトラッカーGW：`type=1` の位置通知を扱い、N/S・E/Wを反映します。その他の通知は全受信記録に保持します。日付・年のないデバイス時刻を推測補完しません。BLEセンサー配列は原文を保持し、専用の展開は行いません。

これら2機種の変換処理はフィクスチャによるテストです。本リリースで実機の送信からの一貫検証を行ったという意味ではありません。

"""Bounded SORACOM CLI/API adapter; no QGIS dependency and no credential storage."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import math
import os
import subprocess
import time
import urllib.request
import urllib.parse


class DemoError(Exception):
    pass


def utc_ms(value):
    # Numeric timestamps must be explicitly Unix milliseconds, never guessed seconds.
    if isinstance(value, bool):
        raise ValueError('時刻が不正です')
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or not 946684800000 <= value < 4102444800000:
            raise ValueError('数値時刻は2000〜2099年のUnixミリ秒を指定してください')
        return int(value)
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('時刻にはZまたは+09:00などのタイムゾーンが必要です')
    return int(dt.timestamp() * 1000)


def utc_text(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def qlm_position(payload):
    """Pair GGA time with a valid RMC date; never use cloud arrival/sent_at."""
    nmea=payload['nmea']
    rmcs=[v for k,v in nmea.items() if k in ('GNRMC','GPRMC') and isinstance(v,dict)
          and v.get('checksum_valid') is True and v.get('fields',{}).get('status')=='A']
    if not rmcs: raise ValueError('Valid dated RMC is required')
    rmc=max(rmcs,key=lambda v:utc_ms(v['fields']['date']+'T'+v['fields']['utc_time']))
    rf=rmc['fields']; anchor=utc_ms(rf['date']+'T'+rf['utc_time'])
    sentence_id=payload.get('latest_position',{}).get('sentence_id')
    item=nmea.get(sentence_id)
    if not isinstance(item,dict) or item.get('checksum_valid') is not True:
        raise ValueError('Valid position sentence is required')
    f=item['fields']
    if sentence_id in ('GNGGA','GPGGA'):
        quality=int(f['fix_quality'])
        if quality<=0: raise ValueError('No fix')
        day=datetime.fromisoformat(rf['date'])
        candidates=[utc_ms((day+timedelta(days=delta)).date().isoformat()+'T'+f['utc_time']) for delta in (-1,0,1)]
        stamp=min(candidates,key=lambda t:abs(t-anchor))
        if abs(stamp-anchor)>15000: raise ValueError('GGA and RMC epochs are inconsistent')
        fix={1:'SPS',2:'DGPS',4:'FIX',5:'FLOAT',6:'DR'}.get(quality,str(quality))
        basis='GNSS GGA UTC + RMC date'
    elif sentence_id in ('GNRMC','GPRMC') and f.get('status')=='A':
        stamp=utc_ms(f['date']+'T'+f['utc_time']);fix='RMC VALID';basis='GNSS RMC UTC'
    else: raise ValueError('Unsupported position sentence')
    return dict(measured_at=utc_text(stamp),lat=f['latitude'],lon=f['longitude'],
                receiver='QLM29H / RTK',run='QLM29H-'+utc_text(stamp)[:10],fix_status=fix,time_basis=basis)


def normalize(entries, fields=None, source=None, records=None):
    fields = fields or dict(time='measured_at', lat='lat', lon='lon', receiver='receiver', run='run', fix='fix_status')
    points, rejected, seen = [], 0, set()
    for row in entries:
        record = dict(raw_entry_json=json.dumps(row, ensure_ascii=False), receiver='', run='', fix_status='unknown',
                      measured_at='', received_at='', resource_id=str((source or {}).get('resource_id') or row.get('resourceId') or ''),
                      position_valid=False, disposition='invalid')
        if records is not None: records.append(record)
        try:
            payload = row['content']
            payload = json.loads(payload) if isinstance(payload, str) else payload
            record.update(receiver=str(payload.get(fields['receiver']) or ''), run=str(payload.get(fields['run']) or ''),
                          fix_status=str(payload.get(fields['fix']) or 'unknown'), measured_at=str(payload.get(fields['time']) or ''),
                          received_at=utc_text(utc_ms(row['time'])))
            if payload.get('position_valid') is False or str(payload.get(fields['fix'], '')).upper() in ('NO_FIX', 'INCOMPLETE'):
                raise ValueError('No valid position')
            if payload.get('source')=='qlm29h_nmea':
                payload={**payload,**qlm_position(payload)}
                active_fields=dict(time='measured_at',lat='lat',lon='lon',receiver='receiver',run='run',fix='fix_status')
            else: active_fields=fields
            stamp = utc_ms(payload[active_fields['time']])
            lat, lon = float(payload[active_fields['lat']]), float(payload[active_fields['lon']])
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError('範囲外の座標')
            # Resource identity prevents two devices without receiver fields being joined.
            receiver = str(payload.get(active_fields['receiver']) or row.get('resourceId') or 'unknown')
            run = str(payload.get(active_fields['run']) or 'unspecified')
            fix = str(payload.get(active_fields['fix']) or 'unknown')
            origin = dict(source or {})
            resource_id = str(origin.get('resource_id') or row.get('resourceId') or '')
            resource_type = origin.get('resource_type', row.get('resourceType', ''))
            coverage = origin.get('coverage', '')
            source_key = json.dumps([origin.get('profile', ''), coverage, resource_type, resource_id], separators=(',', ':'))
            key = (source_key, receiver, run, stamp, lon, lat, fix)
            record.update(receiver=receiver, run=run, fix_status=fix, measured_at=utc_text(stamp), position_valid=True)
            if key in seen:
                record['disposition']='duplicate'
                continue
            seen.add(key)
            received = utc_ms(row['time'])
            record['disposition']='mapped'
            points.append(dict(raw_entry_json=record['raw_entry_json'], position_valid=True, measured_ms=stamp, measured_at=utc_text(stamp), received_at=utc_text(received),
                               lon=lon, lat=lat, receiver=receiver, run=run, fix_status=fix,
                               resource_id=resource_id, resource_type=resource_type, coverage=coverage,
                               sim_name=str(origin.get('name') or resource_id), source_key=source_key,
                               event_id=str(payload.get('event_id') or ''),
                               synthetic=payload.get('synthetic') is True,time_basis=payload.get('time_basis','explicit measured_at')))
        except (ValueError, KeyError, TypeError, OverflowError):
            rejected += 1
    points.sort(key=lambda p: (p['source_key'], p['receiver'], p['run'], p['measured_ms']))
    return points, rejected


def segments(points, gap_ms=5000):
    result, segment = [], []
    for p in sorted((p for p in points if p.get('process_ms', p.get('measured_ms')) is not None), key=lambda x: (x.get('source_key', ''), x['receiver'], x['run'], x.get('process_ms', x['measured_ms']))):
        if segment:
            prev = segment[-1]
            gap = p.get('process_ms', p['measured_ms']) - prev.get('process_ms', prev['measured_ms'])
            if (p.get('source_key', ''), p['receiver'], p['run']) != (prev.get('source_key', ''), prev['receiver'], prev['run']) or gap <= 0 or gap > gap_ms:
                if len(segment) > 1:
                    result.append(segment)
                segment = []
        segment.append(p)
    if len(segment) > 1:
        result.append(segment)
    return result


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
    tmp.chmod(0o600)
    tmp.replace(path)


class SoracomClient:
    def __init__(self, executable, profile, cache_dir, runner=None, download=None, coverage="jp"):
        if not profile.strip():
            raise DemoError('使用するCLIプロファイルを指定してください')
        if coverage not in ('jp','g'): raise DemoError('カバレッジはjpまたはgを指定してください')
        self.coverage=coverage
        self.executable, self.profile = executable, profile.strip()
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.runner = runner or self._run
        self.download = download or self._download

    def _run(self, args, cancelled=lambda: False):
        command = [self.executable, *args, '--profile', self.profile,
                   '--coverage-type', self.coverage, '--no-retry-on-error']
        try:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            raise DemoError('SORACOM CLIを起動できません。実行ファイルのパスを確認してください') from None
        deadline = time.monotonic() + 90
        try:
            while True:
                if cancelled() or time.monotonic() > deadline:
                    proc.kill()
                    proc.communicate()
                    raise DemoError('中止またはタイムアウト。エクスポート要求は自動再送しません')
                try:
                    output, errors = proc.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if proc.returncode:
                # Never show raw CLI output: it may contain identifiers or signed URLs.
                raise DemoError(f'SORACOM CLIが失敗しました（終了コード {proc.returncode}）。認証・権限・対象・録画期間を確認してください')
            try:
                return json.loads(output)
            except (ValueError, UnicodeError):
                raise DemoError('CLIからJSONを取得できませんでした') from None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def harvest(self, resource_type, resource_id, start, end, cancelled=lambda: False):
        if resource_type not in ('Subscriber', 'Sim') or not resource_id.strip():
            raise DemoError('SubscriberとIMSI、またはSimとSIM IDを指定してください')
        start, end = utc_ms(start), utc_ms(end)
        if not 0 < end - start <= 24 * 3600000:
            raise DemoError('取得終了は開始より後、取得期間は24時間以内にしてください')
        rows, seen = [], set()
        window_start = start
        total = (end - start + 3599999) // 3600000
        for index in range(1, total + 1):
            if cancelled():
                raise DemoError('中止しました。元のレイヤを保持しています')
            window_end = min(window_start + 3600000, end)
            try:
                batch = self.runner(['data', 'get-entries', '--resource-type', resource_type,
                                     '--resource-id', resource_id.strip(), '--from', str(window_start), '--to', str(window_end),
                                     '--sort', 'asc', '--limit', '1000', '--fetch-all'], cancelled)
            except DemoError as e:
                raise DemoError(f'時間区間 {index}/{total} の取得失敗。{e}') from None
            if cancelled():
                raise DemoError('中止しました。元のレイヤを保持しています')
            if not isinstance(batch, list) or len(batch) > 100000:
                raise DemoError('応答形式または取得件数が想定外です。期間を短くしてください')
            # Adjacent API ranges share their boundary. Remove only repeats from
            # earlier windows; retain original duplicates within a single response.
            keys = [json.dumps(row, sort_keys=True, ensure_ascii=False) for row in batch]
            rows.extend(row for row, key in zip(batch, keys) if key not in seen)
            seen.update(keys)
            if len(rows) > 100000:
                raise DemoError('1 SIMの取得が10万件を超えました。期間を短くしてください')
            window_start = window_end
        return rows

    def subscribers(self, cancelled=lambda: False):
        rows = self.runner(['subscribers', 'list', '--limit', '100', '--fetch-all'], cancelled)
        if not isinstance(rows, list) or len(rows) > 10000:
            raise DemoError('SIM一覧の応答が想定外です。手動追加を使用してください')
        result = {}
        for row in rows:
            resource = str(row.get('imsi') or '')
            if not resource:
                continue
            tags = row.get('tags') or {}
            result[resource] = dict(resource_type='Subscriber', resource_id=resource,
                                    name=str(tags.get('name') or resource), status=str(row.get('status') or ''), sim_id=str(row.get('simId') or ''),coverage=self.coverage)
        return sorted(result.values(), key=lambda x: (x['name'], x['resource_id']))

    def cameras(self, cancelled=lambda: False):
        rows = self.runner(['sora-cam', 'devices', 'list'], cancelled)
        if not isinstance(rows, list) or len(rows) > 10000:
            raise DemoError('カメラ一覧の応答が想定外です。カメラIDを手動指定してください')
        result = {}
        for row in rows:
            if not isinstance(row, dict):
                raise DemoError('カメラ一覧の応答形式が不正です')
            device = row.get('deviceId')
            if not isinstance(device, str) or not device.strip():
                continue
            connected = row.get('connected')
            result[device] = dict(device_id=device, name=str(row.get('name') or device),
                                  status='オンライン' if connected is True else 'オフライン' if connected is False else '状態不明')
        return sorted(result.values(), key=lambda x: (x['status'] != 'オンライン', x['name'].casefold(), x['device_id']))

    def harvest_many(self, resources, start, end, fields=None, run_filter='', cancelled=lambda: False):
        # Collect all results before replacing the map. Any failed SIM keeps the previous layers.
        unique = {(r.get('coverage',self.coverage),r['resource_type'], r['resource_id']): r for r in resources}
        if not 1 <= len(unique) <= 20:
            raise DemoError('SIMを1〜20件選択してください')
        points, rejected, counts = [], 0, []
        for index, resource in enumerate(unique.values(), 1):
            if cancelled():
                raise DemoError('中止しました。元のレイヤを保持しています')
            try:
                coverage=resource.get('coverage',self.coverage)
                client=self if coverage==self.coverage else getattr(self,'coverage_clients',{}).get(coverage)
                if client is None:raise DemoError('SIMのカバレッジの接続設定がありません')
                rows = client.harvest(resource['resource_type'], resource['resource_id'], start, end, cancelled)
            except DemoError as e:
                raise DemoError(f'SIM {index}/{len(unique)} の取得失敗。元のレイヤを保持しています。{e}') from None
            source = {**resource, 'profile': self.profile, 'coverage': coverage}
            records=[]
            if resource.get('schema'):
                from .schema import normalize_schema
                current, invalid = normalize_schema(rows, resource['schema'], source, records, resource.get('basis','measured'))
            else:
                current, invalid = normalize(rows, fields, source, records)
            if run_filter:
                current = [p for p in current if p['run'] == run_filter]
                records = [r for r in records if r['run'] == run_filter]
                invalid = sum(r['disposition']=='invalid' for r in records)
            points.extend(current); rejected += invalid
            counts.append(dict(name=resource.get('name') or resource['resource_id'], points=len(current), rejected=invalid, records=records, raw_rows=rows, source=source))
            if len(points) > 100000:
                raise DemoError('合計10万点を超えました。期間またはSIM数を減らしてください')
        if cancelled():
            raise DemoError('中止しました。元のレイヤを保持しています')
        return points, rejected, counts

    @staticmethod
    def _download(url):
        if urllib.parse.urlparse(url).scheme != 'https':
            raise DemoError('画像URLがHTTPSではありません')
        try:
            # A separate request: no SORACOM auth header is sent to the image host.
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(15 * 1024 * 1024 + 1)
            if len(data) > 15 * 1024 * 1024 or not data.startswith(b'\xff\xd8\xff'):
                raise DemoError('応答がJPEGではないか、画像サイズの上限を超えました')
            return data
        except DemoError:
            raise
        except Exception:
            raise DemoError('画像をダウンロードできませんでした。通信状態とURL有効期限を確認してください') from None

    def image(self, device, stamp, cancelled=lambda: False):
        if not device.strip():
            raise DemoError('カメラのデバイスIDを指定してください')
        stamp = utc_ms(stamp)
        # Preserve the original Japan-cache key; isolate global coverage.
        key = [self.profile, device, stamp] if self.coverage == 'jp' else [self.coverage, self.profile, device, stamp]
        digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
        image_path = self.cache / (digest + '.jpg')
        state_path = self.cache / (digest + '.json')
        lock_path = self.cache / (digest + '.lock')
        if image_path.exists():
            return dict(path=str(image_path), requested_ms=stamp, cached=True)
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise DemoError('この画像は取得中か、前回の処理が中断されています。キャッシュの状態を確認してください') from None
        os.close(lock_fd)
        try:
            state = json.loads(state_path.read_text()) if state_path.exists() else None
            if state and not state.get('exportId'):
                raise DemoError('前回の要求結果が不明です。重複を避けるため再送を停止しました。コンソールで確認してください')
            if not state:
                if cancelled():
                    raise DemoError('中止しました')
                # Write intent before POST. An uncertain failure must not cause another POST.
                atomic_json(state_path, dict(status='requesting', requested_ms=stamp))
                state = self.runner(['sora-cam', 'devices', 'images', 'export', '--device-id', device,
                                     '--time', str(stamp)], cancelled)
                if not isinstance(state, dict) or not state.get('exportId'):
                    raise DemoError('エクスポートIDを取得できませんでした。自動再送はしません')
                atomic_json(state_path, dict(exportId=state['exportId'], requested_ms=stamp, status=state.get('status')))
            deadline = time.monotonic() + 120
            while not cancelled() and time.monotonic() < deadline:
                result = self.runner(['sora-cam', 'devices', 'images', 'get-exported', '--device-id', device,
                                      '--export-id', state['exportId']], cancelled)
                status = result.get('status')
                atomic_json(state_path, dict(exportId=state['exportId'], requested_ms=stamp, status=status))
                if status == 'completed':
                    if cancelled():
                        raise DemoError('中止しました。次回は同じエクスポートIDを再利用します')
                    data = self.download(result['url'])
                    tmp = image_path.with_suffix('.part')
                    tmp.write_bytes(data)
                    tmp.chmod(0o600)
                    tmp.replace(image_path)
                    return dict(path=str(image_path), requested_ms=stamp, cached=False)
                if status in ('failed', 'limitExceeded', 'expired'):
                    raise DemoError(f'画像エクスポート: {status}。自動再送はしません')
                if status not in ('initializing', 'processing', 'retrying'):
                    raise DemoError('不明なエクスポート状態のため停止しました')
                for _ in range(20):
                    if cancelled():
                        break
                    time.sleep(0.1)
            raise DemoError('待機を終了しました。次回は同じエクスポートIDの確認を再開します')
        finally:
            lock_path.unlink(missing_ok=True)


def sample_entries():
    """Synthetic positions around Tokyo; NOT a real drive or accuracy measurement."""
    base = utc_ms('2026-10-22T00:00:00Z')
    rows = []
    for receiver, offset in [('RTK', 0), ('CLAS', 0.000035)]:
        for i in range(36):
            if 15 <= i <= 20:  # Deliberate recording gap, not a continuous track.
                continue
            payload = dict(measured_at=utc_text(base+i*1000), lat=35.6800+math.sin(i/10)*0.0005+offset,
                           lon=139.7650+i*0.00008, receiver=receiver, run='SAMPLE',
                           fix_status='FIX' if i < 11 or i > 25 else 'FLOAT', synthetic=True)
            rows.append(dict(time=base+i*1000+2500, content=json.dumps(payload), resourceId='SAMPLE'))
    return rows

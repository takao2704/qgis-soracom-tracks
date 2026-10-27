"""Bounded, resumable SoraCam MP4 export. No credentials in cache state."""
from pathlib import Path
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from .soracom_io import DemoError, atomic_json, utc_ms


def video_paths(client, device, start, end):
    start,end=utc_ms(start),utc_ms(end)
    if not device.strip() or not 0 < end-start <= 900000:
        raise DemoError('動画はカメラと1ミリ秒〜900秒の範囲を指定してください')
    key=['video',client.coverage,client.profile,device,start,end]
    digest=hashlib.sha256(json.dumps(key).encode()).hexdigest()
    return client.cache/(digest+'.mp4'),client.cache/(digest+'.video.json'),client.cache/(digest+'.video.lock')


def cached_video(client,device,start,end):
    path,_,_=video_paths(client,device,start,end)
    if path.is_file():
        with path.open('rb') as f:valid=b'ftyp' in f.read(64)
        if valid:return dict(path=str(path),from_ms=start,to_ms=end,cached=True)
    return None


def download_video(url,path,cancelled):
    if urllib.parse.urlparse(url).scheme!='https':raise DemoError('動画URLがHTTPSではありません')
    tmp=path.with_suffix('.part');size=0;head=b''
    try:
        with urllib.request.urlopen(url,timeout=30) as response, tmp.open('wb') as out:
            tmp.chmod(0o600)
            if urllib.parse.urlparse(response.geturl()).scheme!='https':raise DemoError('動画URLがHTTPSではありません')
            while True:
                if cancelled():raise DemoError('中止しました。次回は同じエクスポートIDを再利用します')
                chunk=response.read(256*1024)
                if not chunk:break
                size+=len(chunk)
                if size>256*1024*1024:raise DemoError('動画が256MBを超えました。期間を短くしてください')
                if not head:head=chunk[:64]
                out.write(chunk)
        if b'ftyp' not in head:raise DemoError('応答がMP4動画ではありません')
        if cancelled():raise DemoError('中止しました')
        tmp.replace(path)
    except DemoError:raise
    except Exception:raise DemoError('動画をダウンロードできませんでした。通信状態とURL有効期限を確認してください') from None
    finally:tmp.unlink(missing_ok=True)


def export_video(client,device,start,end,cancelled=lambda:False,download=None):
    start,end=utc_ms(start),utc_ms(end)
    path,state_path,lock_path=video_paths(client,device,start,end)
    cached=cached_video(client,device,start,end)
    if cached:return cached
    try:fd=os.open(lock_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise DemoError('この動画は取得中か、前回の処理が中断されています') from None
    os.close(fd)
    try:
        state=json.loads(state_path.read_text()) if state_path.exists() else None
        if state and not state.get('exportId'):raise DemoError('前回の動画要求結果が不明です。重複を避けるため再送を停止しました')
        if not state:
            if cancelled():raise DemoError('中止しました')
            atomic_json(state_path,dict(status='requesting',from_ms=start,to_ms=end))
            state=client.runner(['sora-cam','devices','videos','export','--device-id',device,'--from',str(start),'--to',str(end),'--archive=false'],cancelled)
            if not isinstance(state,dict) or not state.get('exportId'):raise DemoError('動画のエクスポートIDを取得できませんでした。自動再送はしません')
            atomic_json(state_path,dict(exportId=state['exportId'],from_ms=start,to_ms=end,status=state.get('status')))
        deadline=time.monotonic()+300
        while not cancelled() and time.monotonic()<deadline:
            result=client.runner(['sora-cam','devices','videos','get-exported','--device-id',device,'--export-id',state['exportId']],cancelled)
            status=result.get('status')
            atomic_json(state_path,dict(exportId=state['exportId'],from_ms=start,to_ms=end,status=status))
            if status=='completed':
                if cancelled():raise DemoError('中止しました。次回は同じエクスポートIDを再利用します')
                (download or download_video)(result['url'],path,cancelled)
                return dict(path=str(path),from_ms=start,to_ms=end,cached=False)
            if status in ('failed','limitExceeded','expired'):raise DemoError(f'動画エクスポート: {status}。自動再送はしません')
            if status not in ('initializing','processing','retrying'):raise DemoError('不明な動画エクスポート状態のため停止しました')
            for _ in range(20):
                if cancelled():break
                time.sleep(.1)
        raise DemoError('待機を終了しました。次回は同じエクスポートIDの確認を再開します')
    finally:lock_path.unlink(missing_ok=True)

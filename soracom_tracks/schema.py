"""Explicit per-resource schemas. No guessing timestamps, coordinates or units."""
import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone, timedelta

MISSING = object()
BUILTINS = {
    'gnss': dict(name='QLM・CLAS共通形式', lat='lat', lon='lon', time='measured_at', time_format='auto', receiver='receiver', run='run', fix='fix_status', values=[]),
    'multi': dict(name='GPSマルチユニット', lat='lat', lon='lon', time='', time_format='iso', values=[
        dict(name=n, path=p, type='number', unit=u) for n,p,u in [('温度','temp','°C'),('湿度','humi','%'),('電池残量','bat',''),('電波強度','rs',''),('加速度X','x','mG'),('加速度Y','y','mG'),('加速度Z','z','mG')]]),
    'beacon': dict(name='ビーコン対応GPSトラッカーGW', lat='lat', lon='lon', time='', time_format='iso', values=[dict(name=n,path=p,type='number',unit=u) for n,p,u in [('電池残量','bat',''),('長軸誤差','major_axis','m'),('短軸誤差','minor_axis','m')]])
}


def path_tokens(path):
    """content-relative dot keys and fixed array indices only; no code execution."""
    if not isinstance(path,str) or not path or len(path)>512:
        raise ValueError('JSONパスを指定してください')
    tokens=[]; pos=0
    pattern=re.compile(r'(?:^|\.)([^.\[\]\s]+)|\[(\d+)\]')
    for match in pattern.finditer(path):
        if match.start()!=pos: raise ValueError('パスは location.lat / values[0].value の形式で指定してください')
        tokens.append(match.group(1) if match.group(1) is not None else int(match.group(2)));pos=match.end()
    if pos!=len(path) or not tokens: raise ValueError('JSONパスが不正です')
    return tokens


def get_path(data,path):
    if not path: return MISSING
    for key in path_tokens(path):
        if isinstance(key,int):
            if not isinstance(data,list) or key>=len(data): return MISSING
        elif not isinstance(data,dict) or key not in data: return MISSING
        data=data[key]
    return data


def leaf_paths(data,prefix='',depth=0):
    if depth>12: return []
    out=[]
    if isinstance(data,dict):
        for k,v in data.items():
            path=f'{prefix}.{k}' if prefix else str(k)
            try: path_tokens(path)
            except ValueError: continue
            out.extend(leaf_paths(v,path,depth+1))
            if len(out)>=200: break
    elif isinstance(data,list):
        for i,v in enumerate(data[:20]):out.extend(leaf_paths(v,f'{prefix}[{i}]',depth+1))
    elif prefix: out.append((prefix,data))
    return out[:200]


def number(value):
    if value is MISSING or value is None or isinstance(value,bool): raise ValueError('数値なし')
    value=float(value)
    if not math.isfinite(value): raise ValueError('有限の数値が必要です')
    return value


def parse_time(value,fmt='iso',zone='UTC'):
    from .soracom_io import utc_ms
    if value is MISSING or value is None or value=='': return None
    if fmt=='auto': return utc_ms(value)  # compatibility: ISO with zone or numeric milliseconds
    if fmt in ('seconds','millis'):
        return utc_ms(number(value)*(1000 if fmt=='seconds' else 1))
    if fmt!='iso' or not isinstance(value,str): raise ValueError('ISO日時を指定してください')
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:
        if zone not in ('UTC','Asia/Tokyo'): raise ValueError('タイムゾーンが必要です')
        dt=dt.replace(tzinfo=timezone.utc if zone=='UTC' else timezone(timedelta(hours=9)))
    return utc_ms(dt.isoformat())


def validate_spec(spec):
    for key in ('lat','lon'):path_tokens(spec.get(key,''))
    for key in ('time','receiver','run','fix'):
        if spec.get(key):path_tokens(spec[key])
    if spec.get('time_format','iso') not in ('iso','seconds','millis','auto'):raise ValueError('時刻形式が不正です')
    if spec.get('timezone','UTC') not in ('UTC','Asia/Tokyo'):raise ValueError('タイムゾーンが不正です')
    names=set()
    if len(spec.get('values',[]))>50:raise ValueError('追加の値は50項目までです')
    for v in spec.get('values',[]):
        name=v.get('name','').strip()
        if not name or name.casefold() in names: raise ValueError('追加の値の項目名は空欄・重複にできません')
        names.add(name.casefold());path_tokens(v.get('path',''))
        if v.get('type') not in ('number','string','boolean'):raise ValueError('追加の値の型が不正です')
    return spec


def preset(key,custom=None):
    result=copy.deepcopy(BUILTINS.get(key) or (custom or {}).get(key))
    if not result: raise ValueError('データ形式を選択してください')
    result['kind']=key if key in BUILTINS else 'custom'
    return validate_spec(result)


def extras(payload,spec):
    values={}; states={}; definitions={}
    for v in spec.get('values',[]):
        signature=json.dumps([v['name'],v['type'],v.get('unit','')],ensure_ascii=False)
        key='value_'+hashlib.sha256(signature.encode()).hexdigest()[:12]
        definitions[key]=v
        raw=get_path(payload,v['path']); state='ok';value=None
        if raw is MISSING: state='missing'
        elif raw is None:state='null'
        else:
            try:
                if v['type']=='number':value=number(raw)
                elif v['type']=='string':
                    if not isinstance(raw,str):raise ValueError()
                    value=raw
                elif v['type']=='boolean':
                    if not isinstance(raw,bool):raise ValueError()
                    value=raw
            except (TypeError,ValueError,OverflowError):state='invalid'
        values[key]=value;states[key]=state
    return values,states,definitions


def normalize_schema(entries,spec,source=None,records=None,basis='measured'):
    from .soracom_io import utc_ms,utc_text,qlm_position
    validate_spec(spec)
    if basis not in ('measured','received'): raise ValueError('時刻基準が不正です')
    source=source or {}; points=[];rejected=0;seen=set()
    for row in entries:
        resource=str(source.get('resource_id') or row.get('resourceId') or '')
        source_key=json.dumps([source.get('profile',''),source.get('coverage',''),source.get('resource_type',''),resource],separators=(',',':'))
        rec=dict(raw_entry_json=json.dumps(row,ensure_ascii=False),resource_id=resource,source_key=source_key,
                 receiver=source.get('name') or resource or 'unknown',run='unspecified',fix_status='unknown',
                 position_valid=False,disposition='invalid',measured_at='',received_at='',process_at='',
                 time_basis=basis,time_error='',value_status_json='{}',extra_values_json='{}',reason='')
        if records is not None:records.append(rec)
        try:
            received=utc_ms(row['time']);rec['received_at']=utc_text(received)
            payload=row['content'];payload=json.loads(payload) if isinstance(payload,str) else payload
            if not isinstance(payload,dict):raise ValueError('contentはJSONオブジェクトが必要です')
            if spec.get('kind')=='gnss' and payload.get('source')=='qlm29h_nmea':payload={**payload,**qlm_position(payload)}
            values,states,defs=extras(payload,spec)
            rec.update(extra_values_json=json.dumps(values,ensure_ascii=False),value_status_json=json.dumps(states),extra_values=values,extra_definitions=defs)
            for out,key in [('receiver','receiver'),('run','run'),('fix_status','fix')]:
                val=get_path(payload,spec.get(key,''))
                if val is not MISSING and val is not None and str(val):rec[out]=str(val)
            measured=None
            try: measured=parse_time(get_path(payload,spec.get('time','')),spec.get('time_format','iso'),spec.get('timezone','UTC'))
            except (ValueError,TypeError,OverflowError):rec['time_error']='測位時刻の変換失敗'
            if measured is None and not rec['time_error']:rec['time_error']='測位時刻なし'
            process=measured if basis=='measured' else received
            rec.update(measured_at=utc_text(measured) if measured is not None else '',process_at=utc_text(process) if process is not None else '')
            if payload.get('position_valid') is False or rec['fix_status'].upper() in ('NO_FIX','INCOMPLETE'):raise ValueError('有効な位置なし')
            if spec.get('kind')=='multi' and payload.get('type')==-1:raise ValueError('デバイス異常通知')
            if spec.get('kind')=='beacon' and payload.get('type')!=1:raise ValueError('位置情報通知以外（記録として保持）')
            lat=number(get_path(payload,spec['lat']));lon=number(get_path(payload,spec['lon']))
            if spec.get('kind')=='beacon':
                if payload.get('ns') not in ('N','S') or payload.get('ew') not in ('E','W'):raise ValueError('緯度・経度の方位が不明です')
                lat=abs(lat)*(-1 if payload['ns']=='S' else 1);lon=abs(lon)*(-1 if payload['ew']=='W' else 1)
            if not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('範囲外の座標')
            rec['position_valid']=True
            # Preserve distinct receiver events even when measured time is unavailable.
            event=payload.get('event_id')
            dedup=(source_key,str(event)) if event else (source_key,json.dumps(row,sort_keys=True,ensure_ascii=False))
            if dedup in seen:rec['disposition']='duplicate';continue
            seen.add(dedup);rec['disposition']='mapped' if process is not None else 'mapped_without_time'
            points.append(dict(rec,lat=lat,lon=lon,measured_ms=measured,received_ms=received,process_ms=process,
                               resource_type=source.get('resource_type',row.get('resourceType','')),coverage=source.get('coverage',''),
                               sim_name=source.get('name') or resource,event_id=str(event or ''),synthetic=payload.get('synthetic') is True))
        except (ValueError,TypeError,KeyError,OverflowError) as e:
            rejected+=1;rec['reason']=str(e)
    points.sort(key=lambda p:(p['source_key'],p['receiver'],p['run'],p['process_ms'] if p['process_ms'] is not None else -1))
    return points,rejected

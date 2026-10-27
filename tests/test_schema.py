import copy
import json
import tempfile
import unittest
from soracom_tracks.schema import get_path,MISSING,preset,normalize_schema,parse_time,validate_spec
from soracom_tracks.soracom_io import utc_ms,segments,SoracomClient

BASE=utc_ms('2026-10-22T00:00:00Z')
def row(payload,delay=0):return dict(time=BASE+delay,content=payload)
def custom():return dict(name='test',kind='custom',lat='position[0].lat',lon='position[0].lon',time='timestamp',time_format='seconds',values=[dict(name='速度',path='speed',type='number',unit='km/h'),dict(name='状態',path='state',type='string',unit=''),dict(name='稼働',path='active',type='boolean',unit='')])

class SchemaTests(unittest.TestCase):
    def test_paths_missing_null_and_no_eval(self):
        self.assertIs(get_path({'x':None},'y'),MISSING);self.assertIsNone(get_path({'x':None},'x'))
        self.assertEqual(get_path({'a':[{'b':0}]},'a[0].b'),0)
        for bad in ('a[*]','a[-1]','a..b','a[0]b'):
            with self.assertRaises(ValueError):get_path({},bad)

    def test_explicit_timestamps(self):
        self.assertEqual(parse_time(BASE/1000,'seconds'),BASE)
        self.assertEqual(parse_time(str(BASE),'millis'),BASE)
        self.assertEqual(parse_time('2026-10-22T09:00:00','iso','Asia/Tokyo'),BASE)
        for value,fmt in [(BASE/1000,'millis'),(BASE,'seconds'),(True,'seconds')]:
            with self.assertRaises(ValueError):parse_time(value,fmt)

    def test_arrival_and_measured_order_and_gap(self):
        rows=[row(dict(measured_at=BASE+i*5000,lat=35,lon=139,receiver='RTK'),delay) for i,delay in [(0,20000),(1,10000)]]
        a,_=normalize_schema(rows,preset('gnss'),basis='received');b,_=normalize_schema(rows,preset('gnss'),basis='measured')
        self.assertEqual(a[0]['measured_ms'],BASE+5000);self.assertEqual(b[0]['measured_ms'],BASE)
        self.assertEqual(len(segments(a,6000)),0);self.assertEqual(len(segments(b,6000)),1)
        self.assertEqual(a[0]['received_at'],b[1]['received_at'])

    def test_missing_time_retains_position_no_silent_fallback(self):
        rows=[row(dict(lat=0,lon=0,temp=12))]
        points,bad=normalize_schema(rows,preset('multi'),basis='measured')
        self.assertEqual(bad,0);self.assertIsNone(points[0]['process_ms']);self.assertIsNone(points[0]['measured_ms']);self.assertEqual(segments(points),[])
        arrival,_=normalize_schema(rows,preset('multi'),basis='received');self.assertEqual(arrival[0]['process_ms'],BASE)

    def test_values_types_null_missing_invalid_and_retention(self):
        records=[];spec=custom();data=dict(position=[dict(lat='35',lon='139')],timestamp=BASE/1000,speed='32.1',state='moving',active=True)
        points,bad=normalize_schema([row(data)],spec,records=records)
        self.assertEqual(bad,0);self.assertEqual(set(points[0]['extra_values'].values()),{32.1,'moving',True})
        data.update(speed=None,state=25);data.pop('active')
        points,bad=normalize_schema([row(data)],spec,records=[])
        self.assertEqual(bad,0);self.assertEqual(set(json.loads(points[0]['value_status_json']).values()),{'null','invalid','missing'})
        self.assertEqual(json.loads(records[0]['raw_entry_json'])['content']['active'],True)

    def test_custom_names_and_zero_false(self):
        spec=custom();spec['values'].append(dict(spec['values'][0]))
        with self.assertRaises(ValueError):validate_spec(spec)
        spec=custom();data=dict(position=[dict(lat=False,lon=139)],timestamp=BASE/1000,speed=0,state='',active=False)
        records=[];points,bad=normalize_schema([row(data)],spec,records=records)
        self.assertEqual(bad,1);self.assertEqual(len(records),1)
        self.assertIn(False,records[0]['extra_values'].values())

    def test_beacon_events_hemisphere_and_unresolved_date(self):
        records=[];rows=[row(dict(type=t,lat=35,lon=139,ns='S',ew='W',timestamp='102209000000',loc_data='00000000')) for t in (1,2,3,4)]
        points,bad=normalize_schema(rows,preset('beacon'),records=records,basis='received')
        self.assertEqual((len(points),bad,len(records)),(1,3,4));self.assertEqual(points[0]['lat'],-35);self.assertEqual(points[0]['lon'],-139);self.assertIsNone(points[0]['measured_ms'])

    def test_resource_identity_dedup_and_mixed_fetch(self):
        resources=[dict(resource_type='Subscriber',resource_id='FAKE-A',name='A',schema=preset('gnss'),basis='measured'),dict(resource_type='Subscriber',resource_id='FAKE-B',name='B',schema=preset('multi'),basis='received')]
        def runner(args,cancel):
            id=args[args.index('--resource-id')+1];data=dict(lat=35,lon=139)
            if id=='FAKE-A':data.update(measured_at=BASE,event_id='duplicate')
            return [row(data),row(data)]
        with tempfile.TemporaryDirectory() as tmp:
            points,bad,counts=SoracomClient('fake','test',tmp,runner=runner).harvest_many(resources,BASE,BASE+1000)
        self.assertEqual(len(points),2);self.assertEqual(bad,0);self.assertEqual(len(counts),2)
        self.assertEqual(sum(len(c['records']) for c in counts),4);self.assertEqual({p['resource_id'] for p in points},{'FAKE-A','FAKE-B'})

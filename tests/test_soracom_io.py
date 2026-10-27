import tempfile
import unittest
from pathlib import Path
from soracom_tracks.soracom_io import SoracomClient, DemoError, normalize, segments, sample_entries, utc_ms


class DataTests(unittest.TestCase):
    def test_timezones_and_no_arrival_fallback(self):
        self.assertEqual(utc_ms('2026-10-22T09:00:00+09:00'),utc_ms('2026-10-22T00:00:00Z'))
        for value in ('2026-10-22T09:00:00',1792627200,True):
            with self.assertRaises(ValueError):utc_ms(value)
        points,rejected=normalize([{'time':1792627200000,'content':'{"lat":35,"lon":139}'}])
        self.assertEqual((points,rejected),([],1))

    def test_gap_devices_duplicates_and_invalid_coordinates(self):
        rows=sample_entries()
        points,rejected=normalize(rows+[rows[0],{'time':1792627200000,'content':'{"measured_at":1792627200000,"lat":999,"lon":139}'}])
        self.assertEqual((len(points),rejected),(60,1))
        lines=segments(points)
        self.assertEqual(len(lines),4)
        self.assertTrue(all(len({p['receiver'] for p in line})==1 for line in lines))
        self.assertTrue(all(b['measured_ms']-a['measured_ms']<=5000 for line in lines for a,b in zip(line,line[1:])))
        self.assertNotEqual(points[0]['measured_at'],points[0]['received_at'])

    def test_qlm_midnight_epoch_and_invalid_rmc(self):
        import copy
        payload={'source':'qlm29h_nmea','sent_at':'2030-01-01T00:00:00Z',
          'latest_position':{'sentence_id':'GNGGA'},'nmea':{
          'GNGGA':{'checksum_valid':True,'fields':{'utc_time':'00:00:00.200Z','latitude':35,'longitude':139,'fix_quality':4}},
          'GNRMC':{'checksum_valid':True,'fields':{'date':'2026-10-21','utc_time':'23:59:59.900Z','status':'A'}}}}
        row={'time':1792627200000,'content':payload}
        points,rejected=normalize([row]);self.assertEqual(rejected,0)
        self.assertEqual(points[0]['measured_at'],'2026-10-22T00:00:00.200Z')
        self.assertEqual(points[0]['fix_status'],'FIX')
        bad=copy.deepcopy(row);bad['content']['nmea']['GNRMC']['fields']['status']='V'
        self.assertEqual(normalize([bad]),([],1))
        bad=copy.deepcopy(row);bad['content']['nmea']['GNGGA']['fields']['utc_time']='00:01:00Z'
        self.assertEqual(normalize([bad]),([],1))

    def test_harvest_bounded_paginated_request(self):
        calls=[]
        with tempfile.TemporaryDirectory() as temp:
            client=SoracomClient('unused','sample',temp,runner=lambda args,cancel:(calls.append(args) or []))
            client.harvest('Subscriber','SAMPLE','2026-10-22T00:00:00Z','2026-10-22T00:01:00Z')
            self.assertIn('--fetch-all',calls[0])
            with self.assertRaises(DemoError):client.harvest('Subscriber','SAMPLE','2026-10-22T00:00:00Z','2026-10-23T00:00:01Z')
            self.assertEqual(len(calls),1)

    def test_hour_windows_boundaries_and_track_continuity(self):
        base=utc_ms('2026-10-22T00:00:00Z');calls=[]
        rows=[dict(time=base+i,content=dict(measured_at=base+i,lat=35,lon=139,
              receiver='RTK',run='drive',fix_status='FIX')) for i in (3595000,3600000,3605000,6600000)]
        def runner(args,cancel):
            calls.append(args);start=int(args[args.index('--from')+1]);end=int(args[args.index('--to')+1])
            return [row for row in rows if start<=row['time']<=end]
        with tempfile.TemporaryDirectory() as temp:
            client=SoracomClient('unused','sample',temp,runner=runner)
            actual=client.harvest('Subscriber','SAMPLE',base,base+6600000)
        self.assertEqual(actual,rows)  # Shared endpoint appears only once.
        self.assertEqual(len(calls),2)
        bounds=[(int(c[c.index('--from')+1]),int(c[c.index('--to')+1])) for c in calls]
        self.assertEqual(bounds,[(base,base+3600000),(base+3600000,base+6600000)])
        self.assertTrue(all('--fetch-all' in c for c in calls))
        points,_=normalize(actual)
        self.assertEqual(len(segments(points,5000)[0]),3)  # No artificial gap at the hour.

    def test_hour_window_failure_cancellation_and_original_duplicates(self):
        base=utc_ms('2026-10-22T00:00:00Z');calls=[];stop=[False]
        row=dict(time=base,content={})
        def runner(args,cancel):
            calls.append(args)
            if len(calls)==2:raise DemoError('network unavailable')
            return [row,row]
        with tempfile.TemporaryDirectory() as temp:
            client=SoracomClient('unused','sample',temp,runner=runner)
            with self.assertRaisesRegex(DemoError,'時間区間 2/2'):
                client.harvest('Subscriber','SAMPLE',base,base+6600000)
            calls.clear()
            self.assertEqual(client.harvest('Subscriber','SAMPLE',base,base+60000),[row,row])
            def cancelling(args,cancel):stop[0]=True;return []
            client.runner=cancelling
            with self.assertRaisesRegex(DemoError,'中止'):
                client.harvest('Subscriber','SAMPLE',base,base+6600000,lambda:stop[0])

    def test_multiple_sim_identity_duplicates_gaps_and_summary(self):
        base=utc_ms('2026-10-22T00:00:00Z')
        def runner(args,cancel):
            resource=args[args.index('--resource-id')+1]
            if resource=='EMPTY':return []
            rows=[dict(time=base+i*5000,content=dict(measured_at=base+i*5000,
                    lat=35,lon=139,receiver='same',run='drive',fix_status='FIX',event_id=str(i))) for i in (0,1,4,5)]
            return rows+[rows[0],dict(time=base,content={})]
        resources=[dict(resource_type='Subscriber',resource_id=x,name=x) for x in ('A','B','EMPTY')]
        with tempfile.TemporaryDirectory() as temp:
            client=SoracomClient('unused','sample',temp,runner=runner,coverage='g')
            points,rejected,counts=client.harvest_many(resources+[resources[0]],base,base+60000)
        self.assertEqual((len(points),rejected),(8,2))
        self.assertEqual([c['points'] for c in counts],[4,4,0])
        lines=segments(points)
        self.assertEqual(len(lines),4)
        self.assertTrue(all(len({p['resource_id'] for p in line})==1 for line in lines))
        self.assertEqual(len({p['source_key'] for p in points}),2)
        self.assertEqual({p['coverage'] for p in points},{'g'})

    def test_multiple_sim_failure_cancel_and_selection_limit(self):
        calls=[]
        resources=[dict(resource_type='Subscriber',resource_id=x) for x in ('A','B')]
        def runner(args,cancel):
            calls.append(args)
            if len(calls)==2:raise DemoError('read failed')
            return []
        with tempfile.TemporaryDirectory() as temp:
            client=SoracomClient('unused','sample',temp,runner=runner)
            args=(resources,'2026-10-22T00:00:00Z','2026-10-22T00:01:00Z')
            with self.assertRaisesRegex(DemoError,'SIM 2/2'):client.harvest_many(*args)
            with self.assertRaisesRegex(DemoError,'中止'):client.harvest_many(*args,cancelled=lambda:True)
            self.assertEqual(len(calls),2)
            with self.assertRaises(DemoError):client.harvest_many([],args[1],args[2])
            many=[dict(resource_type='Subscriber',resource_id=str(i)) for i in range(21)]
            with self.assertRaises(DemoError):client.harvest_many(many,args[1],args[2])
            self.assertEqual(len(calls),2)

    def test_subscriber_list_name_and_manual_id_fallback(self):
        calls=[]
        def runner(args,cancel):
            calls.append(args)
            return [{'imsi':'SAMPLE-A','tags':{'name':'Tracker A'},'status':'active'},
                    {'imsi':'SAMPLE-B','tags':{}},{'imsi':'SAMPLE-A','tags':{'name':'Tracker A'}}]
        with tempfile.TemporaryDirectory() as temp:
            client=SoracomClient('unused','sample',temp,runner=runner)
            sims=client.subscribers()
            self.assertEqual(len(sims),2)
            self.assertEqual({s['name'] for s in sims},{'Tracker A','SAMPLE-B'})
            self.assertIn('--fetch-all',calls[0])

    def test_export_readback_download_and_cache(self):
        calls=[]
        def runner(args,cancel):
            calls.append(args)
            if 'export' in args:return {'exportId':'sample-export','status':'initializing'}
            return {'status':'completed','url':'https://example.invalid/test.jpg'}
        with tempfile.TemporaryDirectory() as temp:
            client=SoracomClient('unused','sample',temp,runner=runner,download=lambda url:b'\xff\xd8\xffsample')
            result=client.image('SAMPLE',1792627200000)
            self.assertFalse(result['cached'])
            self.assertEqual(len(calls),2)
            self.assertTrue(client.image('SAMPLE',1792627200000)['cached'])
            self.assertEqual(len(calls),2)
            self.assertTrue(Path(result['path']).exists())

    def test_uncertain_post_never_resent(self):
        calls=[]
        def runner(args,cancel):calls.append(args); raise DemoError('timeout')
        with tempfile.TemporaryDirectory() as temp:
            client=SoracomClient('unused','sample',temp,runner=runner)
            for _ in range(2):
                with self.assertRaises(DemoError):client.image('SAMPLE',1792627200000)
            self.assertEqual(len(calls),1)

    def test_failed_export_never_reposted(self):
        calls=[]
        def runner(args,cancel):
            calls.append(args)
            return {'exportId':'sample-export','status':'initializing'} if 'export' in args else {'status':'failed'}
        with tempfile.TemporaryDirectory() as temp:
            client=SoracomClient('unused','sample',temp,runner=runner)
            for _ in range(2):
                with self.assertRaises(DemoError):client.image('SAMPLE',1792627200000)
            self.assertEqual(sum('export' in c for c in calls),1)


if __name__=='__main__':unittest.main()

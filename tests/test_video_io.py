import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from soracom_tracks.soracom_io import SoracomClient,DemoError
from soracom_tracks.video_io import export_video,cached_video,video_paths,download_video
BASE=1792640400000
MP4=b'\x00\x00\x00\x18ftypmp42'+b'\x00'*16

class VideoTests(unittest.TestCase):
    def test_export_bounds_cache_and_account_camera_isolation(self):
        calls=[]
        def runner(args,cancel):
            calls.append(args)
            return {'exportId':'test'} if 'export' in args else {'status':'completed','url':'https://example.invalid/video.mp4'}
        def download(url,path,cancel):path.write_bytes(MP4)
        with tempfile.TemporaryDirectory() as tmp:
            client=SoracomClient('unused','test',tmp,runner=runner)
            result=export_video(client,'A',BASE-5000,BASE+8000,download=download)
            self.assertFalse(result['cached']);self.assertEqual(len(calls),2)
            self.assertIn('--archive=false',calls[0]);self.assertEqual(calls[0][calls[0].index('--from')+1],str(BASE-5000))
            self.assertTrue(export_video(client,'A',BASE-5000,BASE+8000)['cached']);self.assertEqual(len(calls),2)
            self.assertIsNone(cached_video(client,'B',BASE-5000,BASE+8000))
            self.assertIsNone(cached_video(client,'A',BASE-5000,BASE+9000))
            other=SoracomClient('unused','other',tmp,runner=runner)
            self.assertIsNone(cached_video(other,'A',BASE-5000,BASE+8000))
            for end in (BASE,BASE+900001):
                with self.assertRaises(DemoError):export_video(client,'A',BASE,end)
            self.assertEqual(len(calls),2)

    def test_uncertain_video_post_not_repeated_and_cancel_before_post(self):
        calls=[]
        def runner(args,cancel):calls.append(args);raise DemoError('unknown request outcome')
        with tempfile.TemporaryDirectory() as tmp:
            client=SoracomClient('unused','test',tmp,runner=runner)
            with self.assertRaises(DemoError):export_video(client,'A',BASE,BASE+5000,lambda:True)
            self.assertEqual(calls,[])
            for _ in range(2):
                with self.assertRaises(DemoError):export_video(client,'A',BASE,BASE+5000)
            self.assertEqual(len(calls),1)

    def test_download_failure_and_cancel_resume_existing_export(self):
        calls=[];stop=[False]
        def runner(args,cancel):
            calls.append(args)
            if 'export' in args:stop[0]=True;return {'exportId':'same'}
            return {'status':'completed','url':'https://example.invalid/video.mp4'}
        with tempfile.TemporaryDirectory() as tmp:
            client=SoracomClient('unused','test',tmp,runner=runner)
            with self.assertRaises(DemoError):export_video(client,'A',BASE,BASE+5000,lambda:stop[0])
            stop[0]=False
            def failed(*_):raise DemoError('download failed')
            with self.assertRaises(DemoError):export_video(client,'A',BASE,BASE+5000,download=failed)
            export_video(client,'A',BASE,BASE+5000,download=lambda u,p,c:p.write_bytes(MP4))
            self.assertEqual(sum('export' in c for c in calls),1)

    def test_failed_and_limit_exceeded_not_reexported(self):
        for status in ('failed','limitExceeded','expired'):
            calls=[]
            def runner(args,cancel):
                calls.append(args);return {'exportId':'same'} if 'export' in args else {'status':status}
            with tempfile.TemporaryDirectory() as tmp:
                client=SoracomClient('unused','test',tmp,runner=runner)
                for _ in range(2):
                    with self.assertRaisesRegex(DemoError,status):export_video(client,'A',BASE,BASE+5000)
                self.assertEqual(sum('export' in c for c in calls),1)

    def test_download_checks_mp4_and_removes_partial(self):
        import io
        class Response(io.BytesIO):
            def geturl(self):return 'https://example.invalid/v.mp4'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'v.mp4'
            for body,valid in [(b'<html>error</html>',False),(MP4,True)]:
                with patch('urllib.request.urlopen',return_value=Response(body)):
                    if valid:download_video('https://example.invalid/v.mp4',path,lambda:False)
                    else:
                        with self.assertRaises(DemoError):download_video('https://example.invalid/v.mp4',path,lambda:False)
                self.assertEqual(path.exists(),valid);self.assertFalse(path.with_suffix('.part').exists())
            path.unlink()
            with patch('urllib.request.urlopen',return_value=Response(MP4)):
                with self.assertRaises(DemoError):download_video('https://example.invalid/v.mp4',path,lambda:True)
            self.assertFalse(path.exists());self.assertFalse(path.with_suffix('.part').exists())

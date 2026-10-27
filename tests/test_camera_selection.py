import tempfile
import unittest
from soracom_tracks.soracom_io import SoracomClient, DemoError


class CameraListTests(unittest.TestCase):
    def test_list_deduplicates_and_preserves_offline_recordings(self):
        calls=[]
        rows=[{'deviceId':'CAM-B','name':'Offline','connected':False},
              {'deviceId':'CAM-A','name':'Online','connected':True},
              {'deviceId':'CAM-A','name':'Online','connected':True},
              {'deviceId':'CAM-C'},{}]
        def runner(args,cancelled):
            calls.append(args);return rows
        with tempfile.TemporaryDirectory() as tmp:
            result=SoracomClient('soracom','test',tmp,runner=runner).cameras()
        self.assertEqual(calls,[['sora-cam','devices','list']])
        self.assertEqual(len(result),3)
        self.assertEqual(result[0]['device_id'],'CAM-A')
        self.assertEqual({r['status'] for r in result},{'オンライン','オフライン','状態不明'})

    def test_invalid_response_and_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ({'error':'bad'},[None]):
                with self.assertRaises(DemoError):
                    SoracomClient('soracom','test',tmp,runner=lambda *_,value=bad:value).cameras()
            self.assertEqual(SoracomClient('soracom','test',tmp,runner=lambda *_:[]).cameras(),[])

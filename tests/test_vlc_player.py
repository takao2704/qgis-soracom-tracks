"""VLC discovery stays explicit, profile-scoped and optional."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
try:
    from soracom_tracks.vlc_player import vlc_directory
    HAS_QGIS=True
except ImportError:
    HAS_QGIS=False


@unittest.skipUnless(HAS_QGIS,'Requires QGIS Python')
class VlcDiscoveryTests(unittest.TestCase):
    def test_profile_path_and_environment_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);profile=root/'profile-vlc';override=root/'override-vlc'
            for directory in (profile,override):
                (directory/'plugins').mkdir(parents=True);(directory/'libvlc.dll').touch()
            with patch('soracom_tracks.vlc_player.sys.platform','win32'),\
                 patch('soracom_tracks.vlc_player.QSettings') as settings,\
                 patch.dict('os.environ',{},clear=True):
                settings.return_value.value.return_value=str(profile)
                self.assertEqual(vlc_directory(),profile.resolve())
                with patch.dict('os.environ',{'SORACOM_TRACKS_VLC_DIR':str(override)}):
                    self.assertEqual(vlc_directory(),override.resolve())

    def test_missing_installation_does_not_use_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('soracom_tracks.vlc_player.sys.platform','win32'),\
                 patch('soracom_tracks.vlc_player.QSettings') as settings,\
                 patch.dict('os.environ',{'SORACOM_TRACKS_VLC_DIR':temp},clear=True):
                settings.return_value.value.return_value=''
                with self.assertRaisesRegex(RuntimeError,'not installed'):vlc_directory()

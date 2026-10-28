"""Optional in-process VLC 3 playback for Windows Qt codec failures.

Uses an installed VLC C API without downloads, subprocesses or pip packages.
Poll on the Qt thread so libVLC worker threads never call Python UI callbacks.
"""
import ctypes
import os
import sys
from pathlib import Path
from qgis.PyQt.QtCore import QObject, QTimer, QSettings, pyqtSignal


def vlc_directory():
    if sys.platform != 'win32':
        raise RuntimeError('VLC fallback is available on Windows')
    configured = os.environ.get('SORACOM_TRACKS_VLC_DIR') or QSettings().value('soracom_tracks/vlc_directory','',type=str)
    candidates = [Path(configured)] if configured else [
        Path(os.environ.get(key, 'C:/Program Files')) / 'VideoLAN' / 'VLC'
        for key in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)')]
    for path in candidates:
        if (path / 'libvlc.dll').is_file() and (path / 'plugins').is_dir():
            return path.resolve()
    raise RuntimeError('VLC 3 is not installed or SORACOM_TRACKS_VLC_DIR is unset')


class VlcPlayer(QObject):
    StoppedState, PlayingState, PausedState = 0, 1, 2
    durationChanged = pyqtSignal(int)
    positionChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(int)
    error = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._instance = self._player = None
        self._dll_directory = None
        self._duration = self._position = 0
        self._state = self.StoppedState
        self._failed = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)
        try:
            directory = vlc_directory()
            self._dll_directory = os.add_dll_directory(str(directory))
            self._lib = ctypes.CDLL(str(directory / 'libvlc.dll'))
            self._bind('libvlc_get_version', ctypes.c_char_p)
            if not self._lib.libvlc_get_version().startswith(b'3.'):
                raise RuntimeError('VLC 3 is required')
            pointer, integer, time = ctypes.c_void_p, ctypes.c_int, ctypes.c_int64
            self._bind('libvlc_new', pointer, integer, ctypes.POINTER(ctypes.c_char_p))
            self._bind('libvlc_release', None, pointer)
            self._bind('libvlc_media_new_path', pointer, pointer, ctypes.c_char_p)
            self._bind('libvlc_media_release', None, pointer)
            self._bind('libvlc_media_player_new', pointer, pointer)
            self._bind('libvlc_media_player_release', None, pointer)
            self._bind('libvlc_media_player_set_media', None, pointer, pointer)
            self._bind('libvlc_media_player_set_hwnd', None, pointer, pointer)
            self._bind('libvlc_media_player_play', integer, pointer)
            self._bind('libvlc_media_player_set_pause', None, pointer, integer)
            self._bind('libvlc_media_player_stop', None, pointer)
            self._bind('libvlc_media_player_get_state', integer, pointer)
            self._bind('libvlc_media_player_get_length', time, pointer)
            self._bind('libvlc_media_player_get_time', time, pointer)
            self._bind('libvlc_media_player_set_time', None, pointer, time)
            self._bind('libvlc_video_set_mouse_input', None, pointer, ctypes.c_uint)
            self._bind('libvlc_video_set_key_input', None, pointer, ctypes.c_uint)
            options = [b'--no-audio', b'--no-video-title-show', b'--no-osd',
                       b'--no-metadata-network-access', b'--quiet']
            args = (ctypes.c_char_p * len(options))(*options)
            self._instance = self._lib.libvlc_new(len(options), args)
            if not self._instance:
                raise RuntimeError('VLC could not initialize')
            self._player = self._lib.libvlc_media_player_new(self._instance)
            if not self._player:
                raise RuntimeError('VLC could not create a player')
            self._lib.libvlc_video_set_mouse_input(self._player, 0)
            self._lib.libvlc_video_set_key_input(self._player, 0)
        except (OSError, AttributeError, RuntimeError):
            self.close()
            raise

    def _bind(self, name, result, *args):
        function = getattr(self._lib, name)
        function.restype, function.argtypes = result, args

    def setVideoOutput(self, widget):
        self._widget = widget
        self._lib.libvlc_media_player_set_hwnd(self._player, int(widget.winId()))

    def set_source(self, path):
        self.stop()
        if not Path(path).is_file():
            raise RuntimeError('Video file does not exist')
        media = self._lib.libvlc_media_new_path(self._instance, str(Path(path).resolve()).encode('utf-8'))
        if not media:
            raise RuntimeError('VLC could not open video')
        try:
            self._lib.libvlc_media_player_set_media(self._player, media)
        finally:
            self._lib.libvlc_media_release(media)
        self._failed = False
        self._duration = self._position = 0
        self.durationChanged.emit(0)
        self.positionChanged.emit(0)

    def play(self):
        if self._player:
            if self._lib.libvlc_media_player_play(self._player) == -1:
                self.error.emit(1)
            else:
                self._timer.start()

    def pause(self):
        if self._player:
            self._lib.libvlc_media_player_set_pause(self._player, 1)
            self._poll()

    def stop(self):
        self._timer.stop()
        if self._player:
            self._lib.libvlc_media_player_stop(self._player)
        self._set_state(self.StoppedState)

    def state(self):
        return self._state

    def duration(self):
        return self._duration

    def position(self):
        return self._position

    def setPosition(self, position):
        if self._player:
            self._lib.libvlc_media_player_set_time(self._player, max(0, min(int(position), self._duration)))

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self.stateChanged.emit(state)

    def _poll(self):
        if not self._player:return
        raw = self._lib.libvlc_media_player_get_state(self._player)
        self._set_state({3: self.PlayingState, 4: self.PausedState}.get(raw, self.StoppedState))
        duration = max(0, self._lib.libvlc_media_player_get_length(self._player))
        position = max(0, self._lib.libvlc_media_player_get_time(self._player))
        if duration != self._duration:
            self._duration = duration
            self.durationChanged.emit(duration)
        if position != self._position:
            self._position = position
            self.positionChanged.emit(position)
        if raw in (6, 7):self._timer.stop()
        if raw == 7 and not self._failed:
            self._failed = True
            self.error.emit(1)

    def close(self):
        self.stop()
        if self._player:
            self._lib.libvlc_media_player_release(self._player)
            self._player = None
        if self._instance:
            self._lib.libvlc_release(self._instance)
            self._instance = None
        if self._dll_directory:
            self._dll_directory.close()
            self._dll_directory = None

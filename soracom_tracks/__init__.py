"""SORACOM Tracks QGIS plugin."""

def classFactory(iface):
    from .plugin import SoracomTracksPlugin
    return SoracomTracksPlugin(iface)

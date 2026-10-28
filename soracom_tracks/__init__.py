"""GNSS Tracks for SORACOM, an unofficial QGIS plugin."""

def classFactory(iface):
    from .plugin import SoracomTracksPlugin
    return SoracomTracksPlugin(iface)

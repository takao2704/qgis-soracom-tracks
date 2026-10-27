from pathlib import Path
from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from .dock import SoracomDock


class SoracomTracksPlugin:
    def __init__(self, iface):
        self.iface=iface
        self.dock=None
        self.action=None

    def initGui(self):
        self.action=QAction(QIcon(str(Path(__file__).parent/'icon.svg')),'Harvestとソラカメ',self.iface.mainWindow())
        self.action.setObjectName('SoracomTracksAction')
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu('SORACOM Tracks',self.action)
        self.iface.addToolBarIcon(self.action)
        self.iface.projectRead.connect(self.project_read)
        self.iface.newProjectCreated.connect(self.new_project)

    def run(self):
        if self.dock is None:
            self.dock=SoracomDock(self.iface)
            self.iface.addDockWidget(Qt.RightDockWidgetArea,self.dock)
            self.dock.attach_existing(automatic=True)
        self.dock.show(); self.dock.raise_()

    def project_read(self):
        if self.dock:
            self.dock.reset_binding()
            self.dock.attach_existing(automatic=True)

    def new_project(self):
        if self.dock: self.dock.reset_binding()

    def unload(self):
        self.iface.projectRead.disconnect(self.project_read)
        self.iface.newProjectCreated.disconnect(self.new_project)
        if self.dock:
            self.dock.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater(); self.dock=None
        if self.action:
            self.iface.removePluginMenu('SORACOM Tracks',self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater(); self.action=None

import json
import tempfile
import unittest
from pathlib import Path
from soracom_tracks.soracom_io import normalize, utc_ms


def fixture():
    base=utc_ms('2026-10-22T00:00:00Z')
    rows=[]
    for i,status in enumerate(('FIX','FLOAT','SPS','DGPS','DR','NO_FIX','SPS','FIX')):
        rows.append(dict(time=base+i*5000+500,resourceId='SAMPLE',content=dict(
            measured_at=base+i*5000,receiver='RTK' if i%2==0 else 'CLAS',run='SAMPLE',
            fix_status=status,position_valid=i!=6,lat=999 if i==7 else 35+i*.001,lon=139+i*.001,
            satellites=19,custom_sensor={'preserve':'original'})))
    return rows


class QualityDataTests(unittest.TestCase):
    def test_valid_points_and_all_raw_records_preserved(self):
        rows=fixture();records=[]
        points,rejected=normalize(rows,records=records)
        self.assertEqual(len(points),5);self.assertEqual(rejected,3)
        self.assertEqual(len(records),8)
        self.assertEqual([json.loads(r['raw_entry_json']) for r in records],rows)
        self.assertEqual({p['fix_status'] for p in points},{'FIX','FLOAT','SPS','DGPS','DR'})
        self.assertEqual({p['receiver'] for p in points},{'RTK','CLAS'})
        self.assertEqual(sum(r['disposition']=='invalid' for r in records),3)
        self.assertTrue(all(json.loads(p['raw_entry_json'])['content']['satellites']==19 for p in points))

try:
    import test_qgis_integration as integration
    from qgis.core import QgsProject,QgsMapSettings,QgsMapRendererParallelJob,QgsRectangle
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor
    HAS_QGIS=True
except ImportError:
    HAS_QGIS=False

if HAS_QGIS:
    class QualityIntegrationTests(unittest.TestCase):
        setUpClass = classmethod(integration.IntegrationTests.setUpClass.__func__)
        setUp = integration.IntegrationTests.setUp
        tearDown = integration.IntegrationTests.tearDown
        def test_quality_legend_audit_save_reopen(self):
            records=[];points,rejected=normalize(fixture(),records=records)
            d=self.dock;d.apply_points(points,rejected,'EPSG:4326',False,records)
            self.assertEqual(d.layer.featureCount(),5);self.assertEqual(d.records.featureCount(),8)
            self.assertTrue(all(not f.hasGeometry() for f in d.records.getFeatures()))
            cats={c.value():c for c in d.layer.renderer().categories()}
            self.assertEqual(len({c.symbol().color().name() for c in cats.values()}),5)
            self.assertIn('単独測位',cats['SPS'].label())
            self.assertIn('差分測位',cats['DGPS'].label())
            self.assertIn('推測航法',cats['DR'].label())
            self.assertEqual({f['receiver'] for f in d.layer.getFeatures()},{'RTK','CLAS'})
            d.persist_layers(str(self.root/'quality.gpkg'))
            self.assertEqual(d.records.featureCount(),8)
            self.assertEqual(json.loads(next(d.records.getFeatures())['raw_entry_json']),fixture()[0])
            project=QgsProject.instance();project.setFileName(str(self.root/'quality.qgz'));self.assertTrue(project.write())
            project.clear();self.iface.active=None;self.assertTrue(project.read(str(self.root/'quality.qgz')))
            d.attach_existing(automatic=True)
            self.assertEqual(d.records.featureCount(),8)
            self.assertEqual(len(d.layer.renderer().categories()),5)

        def test_all_invalid_still_retained_without_positions(self):
            records=[];points,rejected=normalize(fixture()[5:],records=records)
            self.dock.apply_points(points,rejected,'EPSG:4326',False,records)
            self.assertEqual(self.dock.layer.featureCount(),0)
            self.assertEqual(self.dock.records.featureCount(),3)
            self.dock.persist_layers(str(self.root/'invalid.gpkg'))
            self.assertEqual(self.dock.records.featureCount(),3)

if __name__=='__main__':unittest.main()

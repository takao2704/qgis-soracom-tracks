import unittest
import test_qgis_integration as integration

@unittest.skipUnless(integration.HAS_QGIS,'Requires QGIS')
class NameFilterTests(unittest.TestCase):
    if integration.HAS_QGIS:
        setUpClass=classmethod(integration.IntegrationTests.setUpClass.__func__)
        setUp=integration.IntegrationTests.setUp
        tearDown=integration.IntegrationTests.tearDown

    def test_sim_name_filter_preserves_hidden_selection_and_refresh(self):
        d=self.dock
        rows=[dict(resource_type='Subscriber',resource_id='SIM-A',coverage='jp',name='営業車 Tokyo'),
              dict(resource_type='Subscriber',resource_id='SIM-B',coverage='g',name='配送車 Osaka')]
        d.set_sim_choices(rows,rows)
        d.sim_filter.setText('  tOKYo  ')
        self.assertFalse(d.sim_list.item(0).isHidden());self.assertTrue(d.sim_list.item(1).isHidden())
        self.assertEqual(len(d.selected_resources()),2)
        d.set_sim_choices(rows,d.selected_resources());self.assertTrue(d.sim_list.item(1).isHidden())
        d.sim_filter.setText('SIM-B');self.assertTrue(d.sim_list.item(0).isHidden())
        d.sim_filter.setText('no-match');self.assertIn('一致するSIMがありません',d.sim_filter_count.text())
        d.sim_filter.clear();self.assertFalse(d.sim_list.item(1).isHidden());self.assertEqual(len(d.selected_resources()),2)

    def test_common_camera_search_keeps_full_catalog_and_current_camera(self):
        d=self.dock
        rows=[dict(device_id='CAM-A',name='玄関 Tokyo',status='オンライン'),dict(device_id='CAM-B',name='車内 Osaka',status='オフライン')]
        d.set_camera_choices(rows);d.camera_combo.setCurrentIndex(1)
        d.camera_filter.setText('  oSAKa  ')
        self.assertTrue(d.camera_combo.view().isRowHidden(1));self.assertFalse(d.camera_combo.view().isRowHidden(2))
        self.assertFalse(d.camera_combo.model().item(1).isEnabled())
        self.assertEqual(d.camera_combo.currentData(),'CAM-A');self.assertEqual(d.inputs['camera'].text(),'CAM-A')
        self.assertEqual(d.camera_combo.count(),3) # Individual picker still sees the full catalog.
        self.assertIn('選択を保持',d.camera_filter_count.text())
        d.set_camera_choices(rows);self.assertEqual(d.camera_combo.currentData(),'CAM-A')
        self.assertTrue(d.camera_combo.view().isRowHidden(1))
        d.camera_combo.setCurrentIndex(2);self.assertEqual(d.inputs['camera'].text(),'CAM-B')
        d.camera_filter.setText('CAM-A');self.assertFalse(d.camera_combo.view().isRowHidden(1))
        d.camera_filter.setText('no-match');self.assertIn('一致するカメラがありません',d.camera_filter_count.text())
        self.assertEqual(d.inputs['camera'].text(),'CAM-B')
        d.camera_filter.clear();self.assertTrue(d.camera_combo.model().item(1).isEnabled())
        self.assertEqual(d.camera_combo.currentData(),'CAM-B')

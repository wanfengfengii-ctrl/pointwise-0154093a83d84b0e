from device.press import Press, SubmitUnknown
from device.sessions import SessionClosed
from device.simulator import DeviceStep
from tests.support import LabCase, cut_values


class DeviceTests(LabCase):
    def test_invalid_scale_scripts_are_rejected_before_any_device_use(self):
        for invalid in (True, 2.5, '23'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.script((None, invalid))
        self.assertEqual(self.press.cuts(), ())
        self.assertEqual(self.press.open_session().start_serial, 40)

    def test_device_reopen_preserves_serials_and_the_sealed_boundary(self):
        session = self.press.open_session()
        first = self.press.submit_cut(session.session_id, 'strip', lambda: 3)
        self.assertEqual(cut_values(first), [41, 3, 'P1/strip-10/cut/41'])
        self.press.close_session(session.session_id)
        after = Press(self.device_path)
        self.assertTrue(after.read_session(session.session_id).complete)
        self.assertEqual(after.read_session(session.session_id).cuts, (first,))
        with self.assertRaises(SessionClosed):
            after.submit_cut(session.session_id, 'strip', lambda: self.fail('sealed session read the scale'))
        next_session = after.open_session()
        self.assertEqual(next_session.start_serial, 41)
        self.assertEqual(after.submit_cut(next_session.session_id, 'round', lambda: None).serial, 42)
        with self.assertRaises(ValueError):
            Press(self.device_path, serial_floor=10)
        self.assertEqual([cut.serial for cut in after.cuts()], [41, 42])

    def test_device_reports_the_same_unknown_for_different_physical_outcomes(self):
        device = self.script((7,), (DeviceStep.LOSE_BEFORE_EXECUTION, DeviceStep.LOSE_AFTER_EXECUTION))
        sessions = [device.open_session(), device.open_session()]
        messages = []
        for session in sessions:
            with self.assertRaises(SubmitUnknown) as caught:
                device.submit_cut(session.session_id, 'round')
            messages.append(str(caught.exception))
            device.close_session(session.session_id)
        self.assertEqual(messages[0], messages[1])
        self.assertEqual(device.read_session(sessions[0].session_id).cuts, ())
        recovered = device.read_session(sessions[1].session_id).cuts
        self.assertEqual([cut_values(cut) for cut in recovered], [[41, 7, 'P1/round-25/cut/41']])

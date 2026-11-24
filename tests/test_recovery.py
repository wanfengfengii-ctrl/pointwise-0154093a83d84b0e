from contextlib import closing
import sqlite3

from archive.history import TicketStatus, read_ticket
from archive.intake import AttributionConflict, accept_terminal
from archive.progress import current_ticket
from archive.receipts import find_serial_owner
from archive.store import archive_transaction
from capture.attempts import load_pending_attempt, record_evidence
from capture.dispatch import advance
from capture.evidence import EvidenceStatus, evidence_from_receipt
from capture.reconcile import reconcile_attempt
from capture.resume import resume
from device.simulator import DeviceStep
from review.close import close_shift
from tests.support import LabCase, cut_values, fail_terminal_commit, report_values, scenario, ticket_cut


class RecoveryTests(LabCase):
    def test_late_effect(self):
        self.worklist()
        device = self.script((None, 23), (DeviceStep.DEFER_EXECUTION,))
        device.step(DeviceStep.BLOCK_CLOSE)
        first = advance(self.db, self.shift, device)
        session = device.submissions[0].session_id
        open_read = device.read_session(session)
        self.assertFalse(open_read.session.sealed)
        self.assertFalse(open_read.complete)
        self.assertEqual(open_read.cuts, ())
        waiting = resume(self.db, self.shift, device)
        actual = [first.status, waiting.status, read_ticket(self.db, self.shift, 'B').status,
                  len(device.submissions), len(self.press.cuts())]
        expected = ['pending', 'pending', 'todo', 1, 0]
        self.assertEqual(actual, expected)
        late = device.step(DeviceStep.RELEASE_QUEUED, session)
        self.assertEqual(cut_values(late), [41, None, 'P1/round-25/cut/41'])
        self.reopen()
        device.step(DeviceStep.ALLOW_CLOSE)
        closed = resume(self.db, self.shift, device)
        downstream_expected = ['A', [41, None, 'P1/round-25/cut/41'], 'B', 1, 1]
        downstream_actual = [closed.ticket.ticket_id, ticket_cut(closed),
                             current_ticket(self.db, self.shift).ticket_id,
                             len(device.submissions), len(self.press.cuts())]
        self.assertEqual(downstream_actual, downstream_expected)
        scenario('late', {'submission': 'deferred', 'first_read': [], 'initial_close': 'blocked'},
                 expected, actual, downstream_expected, downstream_actual,
                 ['capture/reconcile.py', 'capture/resume.py', 'device/simulator.py', 'archive/intake.py'],
                 'Before the queued request runs, one submission has made no cut. After release only its cut can close A.')

    def test_proven_no_effect(self):
        self.worklist()
        device = self.script((11, 23), (DeviceStep.LOSE_BEFORE_EXECUTION,))
        first = advance(self.db, self.shift, device)
        old = device.submissions[0].session_id
        self.assertEqual(first.status, TicketStatus.PENDING)
        self.assertEqual(self.press.cuts(), ())
        self.reopen()
        closed = resume(self.db, self.shift, device)
        old_state = device.read_session(old)
        actual = [old_state.session.sealed, old_state.complete, len(old_state.cuts), len(device.submissions)]
        expected = [True, True, 0, 2]
        self.assertNotEqual(old, device.submissions[-1].session_id)
        self.assertEqual(actual, expected)
        downstream_expected = ['A', [41, 11, 'P1/round-25/cut/41'], 'todo', 1]
        downstream_actual = [closed.ticket.ticket_id, ticket_cut(closed),
                             read_ticket(self.db, self.shift, 'B').status, len(self.press.cuts())]
        self.assertEqual(downstream_actual, downstream_expected)
        scenario('safe_retry', {'submission': 'lost before execution', 'readings': [11, 23]},
                 expected, actual, downstream_expected, downstream_actual,
                 ['capture/resume.py', 'capture/attempts.py', 'device/sessions.py', 'archive/progress.py'],
                 'The closed original range is empty. One fresh submission uses the first scale value for A.')

    def test_sealed_session_rejects_a_queued_request(self):
        self.worklist()
        device = self.script((11, 23), (DeviceStep.DEFER_EXECUTION,))
        self.assertEqual(advance(self.db, self.shift, device).status, TicketStatus.PENDING)
        old = device.submissions[0].session_id
        self.assertEqual(ticket_cut(resume(self.db, self.shift, device)), [41, 11, 'P1/round-25/cut/41'])
        sealed = device.read_session(old)
        self.assertTrue(sealed.complete)
        self.assertEqual(sealed.cuts, ())
        self.assertIsNone(device.step(DeviceStep.RELEASE_QUEUED, old))
        self.assertEqual(device.read_session(old), sealed)
        self.assertEqual(len(self.press.cuts()), 1)
        self.assertEqual(ticket_cut(advance(self.db, self.shift, device)), [42, 23, 'P1/round-25/cut/42'])

    def test_retry_authorization_survives_reopen_before_new_session(self):
        self.worklist()
        device = self.script((11,), (DeviceStep.LOSE_BEFORE_EXECUTION,))
        advance(self.db, self.shift, device)
        attempt = load_pending_attempt(self.db, self.shift)
        evidence = reconcile_attempt(device, attempt)
        self.assertEqual(evidence.status, EvidenceStatus.NO_EXECUTION)
        with archive_transaction(self.db):
            record_evidence(self.db, attempt, evidence)
        self.reopen()
        closed = resume(self.db, self.shift, device)
        self.assertEqual(ticket_cut(closed), [41, 11, 'P1/round-25/cut/41'])
        self.assertEqual(len(device.submissions), 2)
        self.assertNotEqual(device.submissions[0].session_id, device.submissions[1].session_id)

    def test_unavailable_then_resume(self):
        self.worklist()
        device = self.script((8,), (DeviceStep.LOSE_AFTER_EXECUTION,))
        self.assertEqual(advance(self.db, self.shift, device).status, TicketStatus.PENDING)
        session = device.submissions[0].session_id
        device.step(DeviceStep.BLOCK_READ)
        waiting = resume(self.db, self.shift, device)
        incomplete = device.read_session(session)
        actual = [waiting.status, incomplete.session.sealed, incomplete.complete, len(device.submissions)]
        expected = ['pending', True, False, 1]
        self.assertEqual(actual, expected)
        self.assertEqual(current_ticket(self.db, self.shift).ticket_id, 'A')
        self.assertIsNone(find_serial_owner(self.db, 41))
        device.step(DeviceStep.ALLOW_READ)
        self.reopen()
        closed = resume(self.db, self.shift, device)
        downstream_expected = [[41, 8, 'P1/round-25/cut/41'], 'todo', 1]
        downstream_actual = [ticket_cut(closed), read_ticket(self.db, self.shift, 'B').status,
                             len(self.press.cuts())]
        self.assertEqual(downstream_actual, downstream_expected)
        scenario('probe_unavailable', {'closed_read': 'temporarily incomplete', 'readings': [8]},
                 expected, actual, downstream_expected, downstream_actual,
                 ['capture/reconcile.py', 'capture/resume.py', 'archive/receipts.py'],
                 'The single committed device cut remains the only eligible evidence after complete reading returns.')

    def test_archive_commit_failure(self):
        self.worklist()
        device = self.script((None, 23), (DeviceStep.LOSE_AFTER_EXECUTION,))
        self.assertEqual(advance(self.db, self.shift, device).status, TicketStatus.PENDING)
        self.assertEqual([cut_values(cut) for cut in self.press.cuts()], [[41, None, 'P1/round-25/cut/41']])
        fail_terminal_commit(self.db)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            resume(self.db, self.shift, device)
        self.assertFalse(self.db.in_transaction)
        with closing(sqlite3.connect(self.archive_path)) as observer:
            a = read_ticket(observer, self.shift, 'A')
            b = read_ticket(observer, self.shift, 'B')
            expected = ['pending', 'todo', None, None, None, 'A', 1]
            actual = [a.status, b.status, a.terminal, b.terminal, find_serial_owner(observer, 41),
                      current_ticket(observer, self.shift).ticket_id, len(device.submissions)]
            self.assertEqual(actual, expected)
        self.reopen()
        a = resume(self.db, self.shift, device)
        self.assertEqual(ticket_cut(a), [41, None, 'P1/round-25/cut/41'])
        self.assertEqual(len(device.submissions), 1)
        self.assertEqual(find_serial_owner(self.db, 41), (self.shift, 'A'))
        self.assertEqual(current_ticket(self.db, self.shift).ticket_id, 'B')
        b = advance(self.db, self.shift, device)
        self.assertEqual(ticket_cut(b), [42, 23, 'P1/round-25/cut/42'])
        self.assertEqual(find_serial_owner(self.db, 42), (self.shift, 'B'))
        self.assertEqual(len(device.submissions), 2)
        downstream_expected = {
            'number': 'report:day-1',
            'tickets': [['A', [41, None, 'P1/round-25/cut/41']], ['B', [42, 23, 'P1/round-25/cut/42']]],
            'totals': {'cuts': 2, 'valid_readings': 1, 'grams': 23},
        }
        downstream_actual = report_values(close_shift(self.db, self.shift))
        self.assertEqual(downstream_actual, downstream_expected)
        self.assertEqual([cut_values(c) for c in self.press.cuts()], [ticket_cut(a), ticket_cut(b)])
        scenario('commit', {'readings': [None, 23], 'failed_commit': 'deferred foreign key'},
                 expected, actual, downstream_expected, downstream_actual,
                 ['capture/attempts.py', 'archive/intake.py', 'archive/store.py', 'review/close.py'],
                 'A keeps its original missing measurement on cut 41. B uses cut 42. Only 23 grams contribute.')

    def test_replayed_receipt_keeps_its_original_owner(self):
        self.worklist()
        device = self.script((None, 23), (DeviceStep.LOSE_AFTER_EXECUTION,))
        advance(self.db, self.shift, device)
        self.reopen()
        a = resume(self.db, self.shift, device)
        old = self.press.read_cut(41)
        evidence = evidence_from_receipt(old.session_id, 40, old)
        self.reopen()
        b = current_ticket(self.db, self.shift)
        self.assertEqual(b.ticket_id, 'B')
        self.assertEqual(accept_terminal(self.db, a.ticket, evidence), a.terminal)
        with self.assertRaises(AttributionConflict):
            accept_terminal(self.db, b, evidence)
        self.assertEqual(read_ticket(self.db, self.shift, 'A'), a)
        self.assertEqual(read_ticket(self.db, self.shift, 'B').status, TicketStatus.TODO)
        self.assertEqual(find_serial_owner(self.db, 41), (self.shift, 'A'))
        self.assertEqual(len(device.submissions), 1)
        self.assertEqual(ticket_cut(advance(self.db, self.shift, device)), [42, 23, 'P1/round-25/cut/42'])

    def test_direct_receipt_survives_archive_commit_failure(self):
        self.worklist((('A', 'round'),))
        device = self.script((0,))
        fail_terminal_commit(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            advance(self.db, self.shift, device)
        self.reopen()
        device.step(DeviceStep.BLOCK_CLOSE)
        self.assertEqual(ticket_cut(resume(self.db, self.shift, device)), [41, 0, 'P1/round-25/cut/41'])
        self.assertEqual(len(device.submissions), 1)

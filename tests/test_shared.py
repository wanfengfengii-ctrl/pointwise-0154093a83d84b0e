import sqlite3

from archive.history import TicketStatus, read_ticket
from capture.dispatch import advance
from capture.resume import resume, resume_shared
from device.simulator import DeviceStep
from review.close import IncompleteShift, close_shift
from review.lookup import lookup_report
from tests.support import LabCase, fail_terminal_commit, ticket_cut
from worklist.shifts import (
    RegistrationConflict, register, register_shared,
)

PAPER = 'sheet-7'
ENTRIES = [
    ('A', 'strip', ((0, 0, 2, 1), (2, 0, 2, 1), (4, 0, 2, 1))),
    ('B', 'round', ((0, 0, 1, 1), (6, 0, 1, 1))),
    ('C', 'round', ((1, 0, 1, 1),)),
]


class SharedRegistrationTests(LabCase):
    def setUp(self) -> None:
        super().setUp()
        self.press.load_paper(PAPER, 7, 1)

    def test_replay_is_idempotent(self):
        self.assertEqual(register_shared(self.db, self.shift, PAPER, 7, 1, ENTRIES), self.shift)
        self.assertEqual(register_shared(self.db, self.shift, PAPER, 7, 1, ENTRIES), self.shift)

    def test_changed_parameters_conflict(self):
        register_shared(self.db, self.shift, PAPER, 7, 1, ENTRIES)
        for args in (
            (PAPER, 7, 1, ENTRIES[::-1]),
            (PAPER, 7, 1, [('A', 'round', ENTRIES[0][2]), *ENTRIES[1:]]),
            (PAPER, 7, 1, [('A', 'strip', ENTRIES[0][2][1:]), *ENTRIES[1:]]),
            ('other', 7, 1, ENTRIES),
            (PAPER, 8, 1, ENTRIES),
        ):
            with self.subTest(args=args[0]):
                with self.assertRaises(RegistrationConflict):
                    register_shared(self.db, self.shift, *args)

    def test_one_paper_per_shift(self):
        register_shared(self.db, self.shift, PAPER, 7, 1, ENTRIES)
        with self.assertRaises(RegistrationConflict):
            register_shared(self.db, 'day-2', PAPER, 7, 1, [('D', 'round', ((0, 0, 1, 1),))])
        with self.assertRaises(RegistrationConflict):
            register(self.db, self.shift, (('A', 'round'),))
        with self.assertRaises(RegistrationConflict):
            register_shared(self.db, 'day-2', 'fresh', 7, 1, [('D', 'round', ((0, 0, 1, 1),))])
            register(self.db, 'day-2', (('D', 'round'),))

    def test_regular_and_shared_cannot_swap(self):
        register(self.db, 'day-2', (('D', 'round'),))
        with self.assertRaises(RegistrationConflict):
            register_shared(self.db, 'day-2', PAPER, 7, 1, [('D', 'round', ((0, 0, 1, 1),))])

    def test_invalid_batches_are_rejected_whole(self):
        for entries in (
            [],
            [('A', 'strip', ())],
            [('A', 'strip', ((0, 0, 0, 1),))],
            [('A', 'strip', ((6, 0, 2, 1),))],
            [('A', 'strip', ((-1, 0, 1, 1),))],
            [('A', 'round', ((0, 0, 1, 1),)), ('A', 'round', ((1, 0, 1, 1),))],
            [('A', 'unknown', ((0, 0, 1, 1),))],
        ):
            with self.subTest(entries=entries):
                with self.assertRaises(ValueError):
                    register_shared(self.db, self.shift, PAPER, 7, 1, entries)
                self.assertIsNone(
                    self.db.execute('SELECT 1 FROM shifts WHERE shift_id = ?', (self.shift,)).fetchone())

    def test_device_and_archive_dimensions_agree(self):
        with self.assertRaises(ValueError):
            self.press.load_paper(PAPER, 8, 1)
        self.assertEqual(self.press.load_paper(PAPER, 7, 1).width, 7)


class SharedDamageTests(LabCase):
    def setUp(self) -> None:
        super().setUp()
        self.press.load_paper(PAPER, 7, 1)

    def test_damage_validation_and_idempotence(self):
        self.press.mark_damage(PAPER, [(6, 0)])
        self.press.mark_damage(PAPER, [(6, 0)])
        self.assertEqual(self.press.paper_state(PAPER)[1], frozenset({(6, 0)}))
        for cells in ([(7, 0)], [(0, 1)], [(-1, 0)]):
            with self.assertRaises(ValueError):
                self.press.mark_damage(PAPER, cells)
        with self.assertRaises(ValueError):
            self.press.mark_damage('missing', [(0, 0)])

    def test_damage_over_a_cut_is_rejected(self):
        register_shared(self.db, self.shift, PAPER, 7, 1, ENTRIES)
        device = self.script((5,))
        self.assertEqual(resume_shared(self.db, self.shift, device).status, 'closed')
        with self.assertRaises(ValueError):
            self.press.mark_damage(PAPER, [(2, 0)])


class SharedFlowTests(LabCase):
    def setUp(self) -> None:
        super().setUp()
        self.press.load_paper(PAPER, 7, 1)
        register_shared(self.db, self.shift, PAPER, 7, 1, ENTRIES)

    def test_ordinary_entries_refuse_a_shared_shift(self):
        device = self.script((1,))
        with self.assertRaises(ValueError):
            advance(self.db, self.shift, device)
        with self.assertRaises(ValueError):
            resume(self.db, self.shift, device)
        self.assertEqual(self.press.cuts(), ())

    def test_managed_session_refuses_direct_submission(self):
        device = self.script((1,))
        resume_shared(self.db, self.shift, device)
        sid = device.submissions[-1].session_id
        self.assertTrue(self.press.read_session(sid).session.managed)
        with self.assertRaises(ValueError):
            self.press.submit_cut(sid, 'strip', lambda: 9)

    def test_continue_reports_paper_and_placement(self):
        self.press.mark_damage(PAPER, [(6, 0)])
        device = self.script((0, None, 23), (DeviceStep.DEFER_EXECUTION,))
        self.assertEqual(resume_shared(self.db, self.shift, device).status, 'pending')
        sid = device.submissions[-1].session_id
        device.step(DeviceStep.RELEASE_QUEUED, sid)
        a = resume_shared(self.db, self.shift, device)
        self.assertEqual((a.ticket.ticket_id, a.status), ('A', 'closed'))
        self.assertEqual(a.terminal.cut.paper_id, PAPER)
        self.assertNotEqual(a.terminal.cut.rect, ENTRIES[0][2][0])
        for name in ('B', 'C'):
            self.assertEqual(resume_shared(self.db, self.shift, device).ticket.ticket_id, name)
        self.assertIsNone(resume_shared(self.db, self.shift, device))
        report = lookup_report(self.db, close_shift(self.db, self.shift).number)
        self.assertEqual([row.cut.paper_id for row in report.detail.rows], [PAPER] * 3)
        self.assertEqual(report.totals.cuts, 3)
        self.assertEqual(len({row.cut.rect for row in report.detail.rows}), 3)

    def test_no_placement_blocks_without_spending(self):
        self.press.mark_damage(PAPER, [(0, 0), (6, 0)])
        device = self.script((0,))
        outcome = resume_shared(self.db, self.shift, device)
        self.assertEqual((outcome.ticket.ticket_id, outcome.status), ('A', 'blocked'))
        self.assertEqual(self.press.cuts(), ())
        self.assertEqual(device.submissions, ())
        self.assertIsNone(read_ticket(self.db, self.shift, 'A').terminal)
        self.assertEqual(read_ticket(self.db, self.shift, 'B').status, TicketStatus.TODO)
        with self.assertRaises(IncompleteShift):
            close_shift(self.db, self.shift)

    def test_lost_after_execution_recovers_actual_rect(self):
        self.press.mark_damage(PAPER, [(6, 0)])
        device = self.script((0, None, 23), (DeviceStep.LOSE_AFTER_EXECUTION,))
        first = resume_shared(self.db, self.shift, device)
        self.assertEqual(first.status, 'pending')
        physical = self.press.cuts()[0]
        self.press.mark_damage(PAPER, [(0, 0)])
        self.reopen()
        a = resume_shared(self.db, self.shift, device)
        self.assertEqual((a.ticket.ticket_id, a.status), ('A', 'closed'))
        self.assertEqual(a.terminal.cut.rect, physical.rect)
        self.assertEqual(a.terminal.cut.paper_id, PAPER)

    def test_archive_commit_failure_continues_on_the_same_cut(self):
        self.press.mark_damage(PAPER, [(6, 0)])
        device = self.script((0, None, 23), (DeviceStep.LOSE_AFTER_EXECUTION,))
        self.assertEqual(resume_shared(self.db, self.shift, device).status, 'pending')
        physical = self.press.cuts()[0]
        fail_terminal_commit(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            resume_shared(self.db, self.shift, device)
        self.assertIsNone(read_ticket(self.db, self.shift, 'A').terminal)
        self.reopen()
        a = resume_shared(self.db, self.shift, device)
        self.assertEqual((a.ticket.ticket_id, a.status), ('A', 'closed'))
        self.assertEqual(ticket_cut(a), [41, 0, physical.source])
        self.assertEqual(len(self.press.cuts()), 1)
        self.assertEqual(resume_shared(self.db, self.shift, device).ticket.ticket_id, 'B')

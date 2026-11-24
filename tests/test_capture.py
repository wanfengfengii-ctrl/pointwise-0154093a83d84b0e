from archive.history import TicketStatus, read_ticket
from archive.progress import ShiftInProgress, current_ticket
from capture.dispatch import advance
from lab import initialize
from tests.support import LabCase, cut_values, scenario, ticket_cut
from worklist.order import ordered_tickets
from worklist.shifts import RegistrationConflict, load_shift, register


class CaptureTests(LabCase):
    def test_same_recipe(self):
        entries = (('A', 'round'), ('B', 'round'))
        self.assertEqual(self.worklist(entries), self.shift)
        device = self.script((12, 23))
        a = advance(self.db, self.shift, device)
        self.assertEqual((a.ticket.ticket_id, a.status), ('A', TicketStatus.CLOSED))
        self.assertEqual(len(device.submissions), 1)
        self.assertEqual(len(self.press.cuts()), 1)
        self.assertEqual(read_ticket(self.db, self.shift, 'B').status, TicketStatus.TODO)
        self.assertEqual(current_ticket(self.db, self.shift).ticket_id, 'B')
        b = advance(self.db, self.shift, device)
        self.assertEqual((b.ticket.ticket_id, b.status), ('B', TicketStatus.CLOSED))
        self.assertIsNone(advance(self.db, self.shift, device))
        self.assertEqual([s.recipe for s in device.submissions], ['round', 'round'])
        self.assertEqual(len({s.session_id for s in device.submissions}), 2)
        expected = [[41, 12, 'P1/round-25/cut/41'], [42, 23, 'P1/round-25/cut/42']]
        actual = [cut_values(cut) for cut in self.press.cuts()]
        downstream_expected = dict(zip(('A', 'B'), expected))
        downstream_actual = {key: ticket_cut(read_ticket(self.db, self.shift, key)) for key in ('A', 'B')}
        self.assertEqual(actual, expected)
        self.assertEqual(downstream_actual, downstream_expected)
        scenario('normal', {'entries': entries, 'start_serial': 40, 'readings': [12, 23]},
                 expected, actual, downstream_expected, downstream_actual,
                 ['capture/dispatch.py', 'device/press.py', 'archive/intake.py', 'archive/history.py'],
                 'Two actual cuts in ticket order use serials 41 and 42 and the two scale inputs.')

    def test_duplicate_ticket(self):
        entries = (('A', 'round'), ('A', 'strip'))
        with self.assertRaisesRegex(ValueError, 'duplicate ticket'):
            self.worklist(entries)
        actual = [load_shift(self.db, self.shift), list(ordered_tickets(self.db, self.shift))]
        self.assertEqual(actual, [None, []])
        accepted = self.worklist((('A', 'strip'),))
        downstream = [accepted, [t.ticket_id for t in load_shift(self.db, self.shift)]]
        self.assertEqual(downstream, ['day-1', ['A']])
        scenario('invalid', {'entries': entries}, [None, []], actual,
                 ['day-1', ['A']], downstream, ['worklist/shifts.py', 'worklist/tickets.py'],
                 'A duplicate id makes the whole batch invalid, leaving the same shift id free.')

    def test_registration_replay(self):
        self.worklist()
        device = self.script((12, 23))
        first = advance(self.db, self.shift, device)
        original = load_shift(self.db, self.shift)
        self.assertEqual(self.worklist(), self.shift)
        self.assertEqual(load_shift(self.db, self.shift), original)
        self.assertEqual(read_ticket(self.db, self.shift, 'A'), first)
        self.assertEqual(current_ticket(self.db, self.shift).ticket_id, 'B')
        self.assertEqual(len(self.press.cuts()), 1)

    def test_reopen_initialization(self):
        self.worklist()
        device = self.script((12, 23))
        a = advance(self.db, self.shift, device)
        original = load_shift(self.db, self.shift)
        self.reopen()
        initialize(self.db)
        self.assertEqual(load_shift(self.db, self.shift), original)
        self.assertEqual(self.press.cuts(), (a.terminal.cut,))
        self.assertEqual(read_ticket(self.db, self.shift, 'A'), a)
        b = advance(self.db, self.shift, device)
        self.assertEqual(ticket_cut(b), [42, 23, 'P1/round-25/cut/42'])

    def test_zero_grams(self):
        self.worklist((('A', 'strip'),))
        device = self.script((0,))
        advance(self.db, self.shift, device)
        a = read_ticket(self.db, self.shift, 'A')
        self.assertEqual(a.status, TicketStatus.CLOSED)
        self.assertEqual(ticket_cut(a), [41, 0, 'P1/strip-10/cut/41'])

    def test_invalid_recipe(self):
        with self.assertRaisesRegex(ValueError, 'unknown paper recipe'):
            self.worklist((('A', 'round'), ('B', 'unknown')))
        self.assertIsNone(load_shift(self.db, self.shift))
        self.assertEqual(ordered_tickets(self.db, self.shift), ())
        self.assertEqual(self.worklist(), self.shift)

    def test_conflicting_order(self):
        self.worklist()
        device = self.script((3, 4))
        a = advance(self.db, self.shift, device)
        with self.assertRaises(RegistrationConflict):
            self.worklist((('B', 'round'), ('A', 'round')))
        with self.assertRaises(RegistrationConflict):
            self.worklist((('A', 'round'), ('B', 'strip')))
        self.assertEqual(read_ticket(self.db, self.shift, 'A'), a)
        self.assertEqual(advance(self.db, self.shift, device).ticket.ticket_id, 'B')
        self.assertEqual([t.recipe for t in load_shift(self.db, self.shift)], ['round', 'round'])

    def test_active_shift_is_exclusive_until_last_ticket_closes(self):
        self.worklist()
        register(self.db, 'day-2', (('C', 'strip'),))
        device = self.script((1, 2, 3))
        advance(self.db, self.shift, device)
        with self.assertRaises(ShiftInProgress):
            advance(self.db, 'day-2', device)
        self.assertEqual(len(device.submissions), 1)
        advance(self.db, self.shift, device)
        c = advance(self.db, 'day-2', device)
        self.assertEqual(ticket_cut(c), [43, 3, 'P1/strip-10/cut/43'])

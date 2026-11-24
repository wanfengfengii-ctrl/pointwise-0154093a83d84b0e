from archive.history import read_ticket
from capture.dispatch import advance
from capture.resume import resume
from device.simulator import DeviceStep
from review.close import IncompleteShift, close_shift
from review.detail import build_shift_detail
from review.lookup import ReportNotFound, lookup_report
from review.reports import find_report_for_shift
from tests.support import LabCase, report_values, scenario, ticket_cut
from worklist.shifts import register


class ReviewTests(LabCase):
    def _finish_first_shift(self, grams):
        self.worklist()
        device = self.script(grams)
        advance(self.db, self.shift, device)
        advance(self.db, self.shift, device)
        return close_shift(self.db, self.shift)

    def test_unfinished_shift(self):
        self.worklist()
        device = self.script((5, 23), (DeviceStep.RETURN, DeviceStep.LOSE_AFTER_EXECUTION))
        a = advance(self.db, self.shift, device)
        advance(self.db, self.shift, device)
        expected = [['A', 'closed'], ['B', 'pending']]
        detail = build_shift_detail(self.db, self.shift)
        actual = [[row.ticket.ticket_id, row.status] for row in detail.rows]
        self.assertEqual(actual, expected)
        self.assertFalse(detail.complete)
        with self.assertRaises(IncompleteShift) as caught:
            close_shift(self.db, self.shift)
        self.assertEqual(caught.exception.outstanding, ('B',))
        self.assertIsNone(find_report_for_shift(self.db, self.shift))
        with self.assertRaises(ReportNotFound):
            lookup_report(self.db, 'report:day-1')
        downstream_expected = [[41, 5, 'P1/round-25/cut/41'], None]
        downstream_actual = [ticket_cut(read_ticket(self.db, self.shift, 'A')),
                             ticket_cut(read_ticket(self.db, self.shift, 'B'))]
        self.assertEqual(read_ticket(self.db, self.shift, 'A'), a)
        self.assertEqual(downstream_actual, downstream_expected)
        scenario('incomplete', {'A': 'direct receipt', 'B': 'lost receipt'}, expected, actual,
                 downstream_expected, downstream_actual,
                 ['review/detail.py', 'review/close.py', 'review/lookup.py', 'archive/history.py'],
                 'A is fixed and B is still under investigation, so a two-ticket shift cannot be final.')
        resume(self.db, self.shift, device)
        self.assertEqual(close_shift(self.db, self.shift).totals.grams, 28)

    def test_reopen_totals(self):
        original = self._finish_first_shift((None, 23))
        expected = {
            'number': 'report:day-1',
            'tickets': [['A', [41, None, 'P1/round-25/cut/41']], ['B', [42, 23, 'P1/round-25/cut/42']]],
            'totals': {'cuts': 2, 'valid_readings': 1, 'grams': 23},
        }
        actual = report_values(original)
        self.assertEqual(actual, expected)
        self.reopen()
        fetched = lookup_report(self.db, original.number)
        self.assertEqual(fetched, original)
        downstream_actual = report_values(fetched)
        self.assertEqual(downstream_actual, expected)
        scenario('totals', {'ticket_order': ['A', 'B'], 'readings': [None, 23], 'start_serial': 40},
                 expected, actual, expected, downstream_actual,
                 ['review/detail.py', 'review/totals.py', 'review/reports.py', 'review/lookup.py'],
                 'Two cuts, one nonmissing reading, and an integer sum of 23 with the original order and sources.')

    def test_zero_reading_counts_as_valid(self):
        original = self._finish_first_shift((None, 0))
        self.assertEqual(report_values(original)['totals'], {'cuts': 2, 'valid_readings': 1, 'grams': 0})
        self.reopen()
        fetched = lookup_report(self.db, original.number)
        self.assertEqual([row.cut.grams for row in fetched.detail.rows], [None, 0])
        self.assertEqual([row.cut.serial for row in fetched.detail.rows], [41, 42])

    def test_all_missing(self):
        original = self._finish_first_shift((None, None))
        self.assertEqual(report_values(original)['totals'], {'cuts': 2, 'valid_readings': 0, 'grams': 0})
        self.assertEqual([row.cut.source for row in original.detail.rows],
                         ['P1/round-25/cut/41', 'P1/round-25/cut/42'])
        self.assertEqual([row.cut.grams for row in original.detail.rows], [None, None])

    def test_original_report_after_next_shift(self):
        first = self._finish_first_shift((None, 23))
        self.assertEqual(close_shift(self.db, self.shift), first)
        register(self.db, 'day-2', (('A', 'strip'),))
        device = self.script((9,))
        advance(self.db, 'day-2', device)
        second = close_shift(self.db, 'day-2')
        self.assertNotEqual(first.number, second.number)
        self.assertEqual(report_values(second), {
            'number': 'report:day-2', 'tickets': [['A', [43, 9, 'P1/strip-10/cut/43']]],
            'totals': {'cuts': 1, 'valid_readings': 1, 'grams': 9},
        })
        self.reopen()
        self.assertEqual(lookup_report(self.db, first.number), first)
        self.assertEqual(lookup_report(self.db, second.number), second)
        self.assertEqual(close_shift(self.db, self.shift), first)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM shift_reports').fetchone()[0], 2)

    def test_unknown_report(self):
        known = self._finish_first_shift((1, 2))
        with self.assertRaises(ReportNotFound):
            lookup_report(self.db, 'report:unknown')
        self.assertEqual(lookup_report(self.db, known.number), known)

    def test_exact_integer_grams_survive_both_stores(self):
        large = 2**80
        original = self._finish_first_shift((large, 3))
        self.reopen()
        self.assertEqual(self.press.read_cut(41).grams, large)
        self.assertEqual(read_ticket(self.db, self.shift, 'A').terminal.cut.grams, large)
        self.assertEqual(lookup_report(self.db, original.number).totals.grams, large + 3)

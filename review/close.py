import sqlite3

from archive.history import TicketStatus
from archive.store import archive_transaction
from review.detail import build_shift_detail
from review.reports import ShiftReport, find_report_for_shift, save_report
from review.totals import calculate_totals


class IncompleteShift(ValueError):
    def __init__(self, shift_id: str, outstanding: tuple[str, ...]) -> None:
        self.shift_id = shift_id
        self.outstanding = outstanding
        super().__init__(f'shift {shift_id!r} still has outstanding tickets {outstanding!r}')


def close_shift(db: sqlite3.Connection, shift_id: str) -> ShiftReport:
    with archive_transaction(db):
        saved = find_report_for_shift(db, shift_id)
        if saved is not None:
            return saved
        detail = build_shift_detail(db, shift_id)
        if not detail.complete:
            outstanding = tuple(row.ticket.ticket_id for row in detail.rows
                                if row.status != TicketStatus.CLOSED)
            raise IncompleteShift(shift_id, outstanding)
        report = ShiftReport(f'report:{shift_id}', detail, calculate_totals(detail))
        save_report(db, report)
        return report

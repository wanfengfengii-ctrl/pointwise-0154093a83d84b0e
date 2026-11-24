from dataclasses import dataclass
import sqlite3

from archive.history import TicketStatus, read_ticket
from archive.store import read_snapshot
from device.ledger import PhysicalCut
from worklist.order import ordered_tickets
from worklist.shifts import require_shift
from worklist.tickets import Ticket


@dataclass(frozen=True)
class DetailRow:
    ticket: Ticket
    status: TicketStatus
    cut: PhysicalCut | None


@dataclass(frozen=True)
class ShiftDetail:
    shift_id: str
    rows: tuple[DetailRow, ...]

    @property
    def complete(self) -> bool:
        return all(row.status == TicketStatus.CLOSED for row in self.rows)


def build_shift_detail(db: sqlite3.Connection, shift_id: str) -> ShiftDetail:
    with read_snapshot(db):
        require_shift(db, shift_id)
        rows = []
        for ticket in ordered_tickets(db, shift_id):
            view = read_ticket(db, shift_id, ticket.ticket_id)
            cut = None if view.terminal is None else view.terminal.cut
            rows.append(DetailRow(ticket, view.status, cut))
        return ShiftDetail(shift_id, tuple(rows))

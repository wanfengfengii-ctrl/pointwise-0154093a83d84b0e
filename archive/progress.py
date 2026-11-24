import sqlite3

from worklist.order import ticket_after
from worklist.shifts import require_shift
from worklist.tickets import Ticket


class ShiftInProgress(RuntimeError):
    pass


class TicketOutOfOrder(ValueError):
    pass


def current_ticket(db: sqlite3.Connection, shift_id: str) -> Ticket | None:
    require_shift(db, shift_id)
    row = db.execute(
        'SELECT done_position FROM shift_progress WHERE shift_id = ?', (shift_id,),
    ).fetchone()
    return ticket_after(db, shift_id, 0 if row is None else row[0])


def require_current_ticket(db: sqlite3.Connection, ticket: Ticket) -> None:
    if current_ticket(db, ticket.shift_id) != ticket:
        raise TicketOutOfOrder(f'ticket {ticket.ticket_id!r} is not the current ticket')


def claim_shift(db: sqlite3.Connection, ticket: Ticket) -> None:
    require_current_ticket(db, ticket)
    active = db.execute('SELECT shift_id FROM shift_progress WHERE active = 1').fetchone()
    if active is not None and active[0] != ticket.shift_id:
        raise ShiftInProgress(f'shift {active[0]!r} still owns the press')
    db.execute('''
        INSERT INTO shift_progress (shift_id, active) VALUES (?, 1)
        ON CONFLICT (shift_id) DO UPDATE SET active = 1
    ''', (ticket.shift_id,))


def advance_progress(db: sqlite3.Connection, ticket: Ticket) -> None:
    require_current_ticket(db, ticket)
    more = ticket_after(db, ticket.shift_id, ticket.position) is not None
    change = db.execute('''
        UPDATE shift_progress SET done_position = ?, active = ?
        WHERE shift_id = ? AND done_position = ? AND active = 1
    ''', (ticket.position, int(more), ticket.shift_id, ticket.position - 1))
    if change.rowcount != 1:
        raise TicketOutOfOrder(f'ticket {ticket.ticket_id!r} has no active position to advance')

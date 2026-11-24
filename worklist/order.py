import sqlite3

from worklist.tickets import Ticket


def ordered_tickets(db: sqlite3.Connection, shift_id: str) -> tuple[Ticket, ...]:
    rows = db.execute(
        'SELECT shift_id, ticket_id, position, recipe FROM tickets '
        'WHERE shift_id = ? ORDER BY position', (shift_id,),
    )
    return tuple(Ticket(*row) for row in rows)


def ticket_after(db: sqlite3.Connection, shift_id: str, position: int) -> Ticket | None:
    row = db.execute(
        'SELECT shift_id, ticket_id, position, recipe FROM tickets '
        'WHERE shift_id = ? AND position > ? ORDER BY position LIMIT 1',
        (shift_id, position),
    ).fetchone()
    return None if row is None else Ticket(*row)


def same_ticket_order(existing: tuple[Ticket, ...], requested: tuple[Ticket, ...]) -> bool:
    return existing == requested

from collections.abc import Iterable
from dataclasses import dataclass
import sqlite3

from worklist.recipes import resolve_recipe


@dataclass(frozen=True)
class Ticket:
    shift_id: str
    ticket_id: str
    position: int
    recipe: str


def validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{label} must be a nonempty text identifier')


def validate_ticket_batch(
    shift_id: str, entries: Iterable[tuple[str, str]]
) -> tuple[Ticket, ...]:
    validate_identifier(shift_id, 'shift id')
    tickets = []
    seen = set()
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise ValueError('each ticket must contain a ticket id and a recipe code')
        ticket_id, recipe = entry
        validate_identifier(ticket_id, 'ticket id')
        if ticket_id in seen:
            raise ValueError(f'duplicate ticket {ticket_id!r} in shift {shift_id!r}')
        seen.add(ticket_id)
        tickets.append(Ticket(shift_id, ticket_id, position, resolve_recipe(recipe).code))
    return tuple(tickets)


def insert_tickets(db: sqlite3.Connection, tickets: Iterable[Ticket]) -> None:
    db.executemany(
        'INSERT INTO tickets (shift_id, ticket_id, position, recipe) VALUES (?, ?, ?, ?)',
        ((t.shift_id, t.ticket_id, t.position, t.recipe) for t in tickets),
    )


def find_ticket(db: sqlite3.Connection, shift_id: str, ticket_id: str) -> Ticket | None:
    row = db.execute(
        'SELECT shift_id, ticket_id, position, recipe FROM tickets '
        'WHERE shift_id = ? AND ticket_id = ?', (shift_id, ticket_id),
    ).fetchone()
    return None if row is None else Ticket(*row)

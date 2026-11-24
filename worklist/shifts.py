from collections.abc import Iterable, Iterator
from contextlib import contextmanager
import sqlite3

from worklist.order import ordered_tickets, same_ticket_order
from worklist.tickets import Ticket, insert_tickets, validate_ticket_batch


class ShiftNotFound(LookupError):
    pass


class RegistrationConflict(ValueError):
    pass


@contextmanager
def _write(db: sqlite3.Connection) -> Iterator[None]:
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def initialize_shifts(db: sqlite3.Connection) -> None:
    with _write(db):
        db.execute('CREATE TABLE IF NOT EXISTS shifts (shift_id TEXT PRIMARY KEY NOT NULL)')
        db.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
                ticket_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position > 0),
                recipe TEXT NOT NULL,
                PRIMARY KEY (shift_id, ticket_id),
                UNIQUE (shift_id, position)
            )
        ''')


def _has_shift(db: sqlite3.Connection, shift_id: str) -> bool:
    return db.execute('SELECT 1 FROM shifts WHERE shift_id = ?', (shift_id,)).fetchone() is not None


def require_shift(db: sqlite3.Connection, shift_id: str) -> None:
    if not _has_shift(db, shift_id):
        raise ShiftNotFound(shift_id)


def load_shift(db: sqlite3.Connection, shift_id: str) -> tuple[Ticket, ...] | None:
    if _has_shift(db, shift_id):
        return ordered_tickets(db, shift_id)
    return None


def register(db: sqlite3.Connection, shift_id: str, entries: Iterable[tuple[str, str]]) -> str:
    requested = validate_ticket_batch(shift_id, entries)
    with _write(db):
        existing = load_shift(db, shift_id)
        if existing is None:
            db.execute('INSERT INTO shifts (shift_id) VALUES (?)', (shift_id,))
            insert_tickets(db, requested)
        elif not same_ticket_order(existing, requested):
            raise RegistrationConflict(f'shift {shift_id!r} already has a different worklist')
    return shift_id

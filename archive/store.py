from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3


@contextmanager
def archive_transaction(db: sqlite3.Connection) -> Iterator[None]:
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


@contextmanager
def read_snapshot(db: sqlite3.Connection) -> Iterator[None]:
    if db.in_transaction:
        yield
        return
    db.execute('BEGIN')
    try:
        yield
    finally:
        db.rollback()


def initialize_archive(db: sqlite3.Connection) -> None:
    with archive_transaction(db):
        db.execute('''
            CREATE TABLE IF NOT EXISTS shift_progress (
                shift_id TEXT PRIMARY KEY NOT NULL REFERENCES shifts(shift_id),
                done_position INTEGER NOT NULL DEFAULT 0 CHECK (done_position >= 0),
                active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1))
            )
        ''')
        db.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_shift
            ON shift_progress(active) WHERE active = 1
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS terminals (
                shift_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                serial INTEGER NOT NULL UNIQUE CHECK (serial > 0),
                session_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                grams TEXT,
                PRIMARY KEY (shift_id, ticket_id),
                FOREIGN KEY (shift_id, ticket_id) REFERENCES tickets(shift_id, ticket_id)
            )
        ''')

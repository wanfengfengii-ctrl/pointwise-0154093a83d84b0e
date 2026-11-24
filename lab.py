from pathlib import Path
import sqlite3

from archive.store import initialize_archive
from capture.attempts import initialize_attempts
from review.reports import initialize_reports
from worklist.shared import initialize_shared
from worklist.shifts import initialize_shifts


def initialize(db: sqlite3.Connection) -> None:
    if db.in_transaction:
        raise ValueError('initialize on an idle archive connection')
    db.execute('PRAGMA foreign_keys = ON')
    initialize_shifts(db)
    initialize_shared(db)
    initialize_archive(db)
    initialize_attempts(db)
    initialize_reports(db)


def open_archive(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    try:
        initialize(db)
    except BaseException:
        db.close()
        raise
    return db

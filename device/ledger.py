from dataclasses import dataclass
import sqlite3

from device.scale import ScaleReading, decode_grams, encode_grams
from device.sessions import DeviceSession


@dataclass(frozen=True)
class PhysicalCut:
    serial: int
    session_id: int
    source: str
    grams: int | None


def initialize_ledger(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_cuts (
            serial INTEGER PRIMARY KEY CHECK (serial > 0),
            session_id INTEGER NOT NULL REFERENCES device_sessions(session_id),
            source TEXT NOT NULL,
            grams TEXT
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS device_cuts_by_session ON device_cuts(session_id, serial)')


def latest_serial(db: sqlite3.Connection) -> int:
    return db.execute('''
        SELECT COALESCE(MAX(serial), (SELECT serial_floor FROM press_config WHERE singleton = 1))
        FROM device_cuts
    ''').fetchone()[0]


def append_cut(
    db: sqlite3.Connection, session: DeviceSession, device_id: str, die: str,
    reading: ScaleReading,
) -> PhysicalCut:
    serial = latest_serial(db) + 1
    source = f'{device_id}/{die}/cut/{serial}'
    db.execute(
        'INSERT INTO device_cuts (serial, session_id, source, grams) VALUES (?, ?, ?, ?)',
        (serial, session.session_id, source, encode_grams(reading.grams)),
    )
    return PhysicalCut(serial, session.session_id, source, reading.grams)


def _as_cut(row: tuple) -> PhysicalCut:
    serial, session_id, source, grams = row
    return PhysicalCut(serial, session_id, source, decode_grams(grams))


def read_cut(db: sqlite3.Connection, serial: int) -> PhysicalCut | None:
    row = db.execute(
        'SELECT serial, session_id, source, grams FROM device_cuts WHERE serial = ?', (serial,),
    ).fetchone()
    return None if row is None else _as_cut(row)


def list_session_cuts(db: sqlite3.Connection, session_id: int) -> tuple[PhysicalCut, ...]:
    rows = db.execute(
        'SELECT serial, session_id, source, grams FROM device_cuts WHERE session_id = ? ORDER BY serial',
        (session_id,),
    )
    return tuple(_as_cut(row) for row in rows)


def list_cuts(db: sqlite3.Connection) -> tuple[PhysicalCut, ...]:
    rows = db.execute('SELECT serial, session_id, source, grams FROM device_cuts ORDER BY serial')
    return tuple(_as_cut(row) for row in rows)

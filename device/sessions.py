from dataclasses import dataclass
import sqlite3


class SessionNotFound(LookupError):
    pass


class SessionClosed(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceSession:
    session_id: int
    start_serial: int
    sealed: bool
    managed: bool = False


def initialize_sessions(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_sessions (
            session_id INTEGER PRIMARY KEY,
            start_serial INTEGER NOT NULL CHECK (start_serial >= 0),
            sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1)),
            managed INTEGER NOT NULL DEFAULT 0 CHECK (managed IN (0, 1))
        )
    ''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(device_sessions)')}
    if 'managed' not in columns:
        db.execute('ALTER TABLE device_sessions ADD COLUMN managed INTEGER NOT NULL DEFAULT 0')


def create_session(
    db: sqlite3.Connection, start_serial: int, *, managed: bool = False,
) -> DeviceSession:
    cursor = db.execute(
        'INSERT INTO device_sessions (start_serial, managed) VALUES (?, ?)',
        (start_serial, int(managed)),
    )
    return DeviceSession(cursor.lastrowid, start_serial, False, managed)


def load_session(db: sqlite3.Connection, session_id: int) -> DeviceSession:
    row = db.execute(
        'SELECT session_id, start_serial, sealed, managed FROM device_sessions WHERE session_id = ?',
        (session_id,),
    ).fetchone()
    if row is None:
        raise SessionNotFound(session_id)
    return DeviceSession(row[0], row[1], bool(row[2]), bool(row[3]))


def require_open_session(db: sqlite3.Connection, session_id: int) -> DeviceSession:
    session = load_session(db, session_id)
    if session.sealed:
        raise SessionClosed(f'device session {session_id} is sealed')
    return session


def seal_session(db: sqlite3.Connection, session_id: int) -> DeviceSession:
    db.execute('UPDATE device_sessions SET sealed = 1 WHERE session_id = ?', (session_id,))
    return load_session(db, session_id)

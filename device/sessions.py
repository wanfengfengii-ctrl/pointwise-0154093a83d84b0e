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


def initialize_sessions(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_sessions (
            session_id INTEGER PRIMARY KEY,
            start_serial INTEGER NOT NULL CHECK (start_serial >= 0),
            sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1))
        )
    ''')


def create_session(db: sqlite3.Connection, start_serial: int) -> DeviceSession:
    cursor = db.execute('INSERT INTO device_sessions (start_serial) VALUES (?)', (start_serial,))
    return DeviceSession(cursor.lastrowid, start_serial, False)


def load_session(db: sqlite3.Connection, session_id: int) -> DeviceSession:
    row = db.execute(
        'SELECT session_id, start_serial, sealed FROM device_sessions WHERE session_id = ?',
        (session_id,),
    ).fetchone()
    if row is None:
        raise SessionNotFound(session_id)
    return DeviceSession(row[0], row[1], bool(row[2]))


def require_open_session(db: sqlite3.Connection, session_id: int) -> DeviceSession:
    session = load_session(db, session_id)
    if session.sealed:
        raise SessionClosed(f'device session {session_id} is sealed')
    return session


def seal_session(db: sqlite3.Connection, session_id: int) -> DeviceSession:
    db.execute('UPDATE device_sessions SET sealed = 1 WHERE session_id = ?', (session_id,))
    return load_session(db, session_id)

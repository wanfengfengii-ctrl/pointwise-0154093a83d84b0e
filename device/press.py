from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Protocol

from device import ledger
from device.ledger import PhysicalCut
from device.scale import measure
from device.sessions import (
    DeviceSession, create_session, initialize_sessions, load_session, require_open_session, seal_session,
)
from worklist.recipes import resolve_recipe


class SubmitUnknown(RuntimeError):
    pass


class DeviceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionSnapshot:
    session: DeviceSession
    cuts: tuple[PhysicalCut, ...]
    complete: bool


class DevicePort(Protocol):
    def open_session(self) -> DeviceSession: ...
    def submit_cut(self, session_id: int, recipe: str) -> PhysicalCut: ...
    def close_session(self, session_id: int) -> DeviceSession: ...
    def read_session(self, session_id: int) -> SessionSnapshot: ...


class Press:
    def __init__(
        self, path: str | Path, *, device_id: str | None = None, serial_floor: int | None = None,
    ) -> None:
        self.path = str(path)
        if not self.path or self.path == ':memory:':
            raise ValueError('use a persistent path for the device ledger')
        if device_id is not None and (not isinstance(device_id, str) or not device_id.strip() or
                                      '/' in device_id or '\x00' in device_id):
            raise ValueError('invalid device identifier')
        if serial_floor is not None and (type(serial_floor) is not int or
                                         not 0 <= serial_floor < 2**63 - 1):
            raise ValueError('invalid initial device serial')
        with self._transaction(write=True) as db:
            db.execute('''
                CREATE TABLE IF NOT EXISTS press_config (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    device_id TEXT NOT NULL,
                    serial_floor INTEGER NOT NULL CHECK (serial_floor >= 0)
                )
            ''')
            saved = db.execute(
                'SELECT device_id, serial_floor FROM press_config WHERE singleton = 1'
            ).fetchone()
            if saved is None:
                saved = (device_id if device_id is not None else 'P1',
                         serial_floor if serial_floor is not None else 0)
                db.execute('INSERT INTO press_config VALUES (1, ?, ?)', saved)
            if (device_id is not None and device_id != saved[0] or
                    serial_floor is not None and serial_floor != saved[1]):
                raise ValueError('device configuration is already fixed for this ledger')
            self.device_id = saved[0]
            initialize_sessions(db)
            ledger.initialize_ledger(db)

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys = ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def open_session(self) -> DeviceSession:
        with self._transaction(write=True) as db:
            return create_session(db, ledger.latest_serial(db))

    def submit_cut(
        self, session_id: int, recipe: str, read_scale: Callable[[], int | None],
    ) -> PhysicalCut:
        settings = resolve_recipe(recipe)
        with self._transaction(write=True) as db:
            session = require_open_session(db, session_id)
            reading = measure(read_scale())
            return ledger.append_cut(db, session, self.device_id, settings.die, reading)

    def close_session(self, session_id: int) -> DeviceSession:
        with self._transaction(write=True) as db:
            return seal_session(db, session_id)

    def read_session(self, session_id: int) -> SessionSnapshot:
        with self._transaction() as db:
            session = load_session(db, session_id)
            return SessionSnapshot(session, ledger.list_session_cuts(db, session_id), session.sealed)

    def read_cut(self, serial: int) -> PhysicalCut | None:
        with self._transaction() as db:
            return ledger.read_cut(db, serial)

    def cuts(self) -> tuple[PhysicalCut, ...]:
        with self._transaction() as db:
            return ledger.list_cuts(db)

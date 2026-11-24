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
from worklist.shared import solve_placement as _solve


class SubmitUnknown(RuntimeError):
    pass


class DeviceUnavailable(RuntimeError):
    pass


class PlacementConflict(ValueError):
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


def _cells(rect: tuple[int, int, int, int]) -> set[tuple[int, int]]:
    x, y, w, h = rect
    return {(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}


def _fits(rect: tuple[int, int, int, int], width: int, height: int) -> bool:
    x, y, w, h = rect
    return w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= width and y + h <= height


def _free(
    rect: tuple[int, int, int, int], damaged: frozenset, cuts: tuple,
) -> bool:
    occupied = set(damaged)
    for cut in cuts:
        occupied |= _cells(cut)
    return not (_cells(rect) & occupied)


def _reserve(
    ticket_id: str | None, remaining: tuple[str, ...], candidates: dict,
    occupied: set[tuple[int, int]],
) -> tuple[int, int, int, int]:
    """Choose this ticket's final cell so the whole rest of the shift still fits."""
    if ticket_id is None:
        raise PlacementConflict('a reserved shared cut needs its ticket')
    assignment = _solve((ticket_id, *remaining), candidates, occupied)
    if assignment is None or ticket_id not in assignment:
        raise PlacementConflict('no placement keeps every remaining ticket feasible')
    return assignment[ticket_id]


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

    def open_managed_session(self) -> DeviceSession:
        with self._transaction(write=True) as db:
            return create_session(db, ledger.latest_serial(db), managed=True)

    def submit_cut(
        self, session_id: int, recipe: str, read_scale: Callable[[], int | None],
        *, paper_id: str | None = None, rect: tuple[int, int, int, int] | None = None,
        ticket_id: str | None = None, remaining: tuple[str, ...] | None = None,
        candidates: dict | None = None,
    ) -> PhysicalCut:
        settings = resolve_recipe(recipe)
        with self._transaction(write=True) as db:
            session = require_open_session(db, session_id)
            if session.managed and paper_id is None:
                raise ValueError('a managed shared-paper session must use the shared cut path')
            if (paper_id is None) != (rect is None):
                raise ValueError('a paper cut needs both the paper id and the placement')
            if paper_id is not None:
                sheet = ledger.read_paper(db, paper_id)
                if sheet is None:
                    raise PlacementConflict(f'unknown paper {paper_id!r}')
                if not _fits(rect, sheet.width, sheet.height):
                    raise PlacementConflict('the placement lies off the paper')
                damaged, cuts = ledger.paper_occupancy(db, paper_id)
                if remaining is not None:
                    reserved = set(damaged)
                    for cut in cuts:
                        reserved |= _cells(cut)
                    rect = _reserve(ticket_id, remaining, candidates or {}, reserved)
                elif not _free(rect, damaged, cuts):
                    raise PlacementConflict('the placement overlaps damage or a prior cut')
            reading = measure(read_scale())
            return ledger.append_cut(
                db, session, self.device_id, settings.die, reading, paper_id=paper_id, rect=rect,
            )

    def load_paper(self, paper_id: str, width: int, height: int) -> ledger.Paper:
        if not isinstance(paper_id, str) or not paper_id.strip() or '\x00' in paper_id:
            raise ValueError('invalid paper identifier')
        if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            raise ValueError('paper dimensions must be positive integers')
        with self._transaction(write=True) as db:
            return ledger.load_paper(db, paper_id, width, height)

    def read_paper(self, paper_id: str) -> ledger.Paper | None:
        with self._transaction() as db:
            return ledger.read_paper(db, paper_id)

    def mark_damage(self, paper_id: str, cells) -> None:
        with self._transaction(write=True) as db:
            ledger.mark_damage(db, paper_id, cells)

    def paper_state(self, paper_id: str) -> tuple[ledger.Paper | None, frozenset, tuple]:
        with self._transaction() as db:
            sheet = ledger.read_paper(db, paper_id)
            damaged, cuts = ledger.paper_occupancy(db, paper_id)
            return sheet, damaged, cuts

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

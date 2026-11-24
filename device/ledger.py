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
    paper_id: str | None = None
    rect: tuple[int, int, int, int] | None = None


Rects = tuple[tuple[int, int, int, int], ...]


@dataclass(frozen=True)
class Paper:
    paper_id: str
    width: int
    height: int


def initialize_ledger(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_cuts (
            serial INTEGER PRIMARY KEY CHECK (serial > 0),
            session_id INTEGER NOT NULL REFERENCES device_sessions(session_id),
            source TEXT NOT NULL,
            grams TEXT,
            paper_id TEXT,
            rect_x INTEGER,
            rect_y INTEGER,
            rect_w INTEGER,
            rect_h INTEGER
        )
    ''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(device_cuts)')}
    for name, kind in (('paper_id', 'TEXT'), ('rect_x', 'INTEGER'), ('rect_y', 'INTEGER'),
                       ('rect_w', 'INTEGER'), ('rect_h', 'INTEGER')):
        if name not in columns:
            db.execute(f'ALTER TABLE device_cuts ADD COLUMN {name} {kind}')
    db.execute('CREATE INDEX IF NOT EXISTS device_cuts_by_session ON device_cuts(session_id, serial)')
    db.execute('''
        CREATE TABLE IF NOT EXISTS paper_sheets (
            paper_id TEXT PRIMARY KEY NOT NULL,
            width INTEGER NOT NULL CHECK (width > 0),
            height INTEGER NOT NULL CHECK (height > 0)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS paper_damage (
            paper_id TEXT NOT NULL REFERENCES paper_sheets(paper_id),
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            PRIMARY KEY (paper_id, x, y)
        )
    ''')


def load_paper(db: sqlite3.Connection, paper_id: str, width: int, height: int) -> Paper:
    row = db.execute(
        'SELECT paper_id, width, height FROM paper_sheets WHERE paper_id = ?', (paper_id,),
    ).fetchone()
    if row is None:
        db.execute(
            'INSERT INTO paper_sheets (paper_id, width, height) VALUES (?, ?, ?)',
            (paper_id, width, height),
        )
        return Paper(paper_id, width, height)
    if (row[1], row[2]) != (width, height):
        raise ValueError(f'paper {paper_id!r} is already registered with different dimensions')
    return Paper(*row)


def read_paper(db: sqlite3.Connection, paper_id: str) -> Paper | None:
    row = db.execute(
        'SELECT paper_id, width, height FROM paper_sheets WHERE paper_id = ?', (paper_id,),
    ).fetchone()
    return None if row is None else Paper(*row)


def damage_cells(db: sqlite3.Connection, paper_id: str) -> Rects:
    rows = db.execute(
        'SELECT x, y FROM paper_damage WHERE paper_id = ? ORDER BY y, x', (paper_id,),
    )
    return tuple((x, y) for x, y in rows)


def list_paper_cuts(db: sqlite3.Connection, paper_id: str) -> Rects:
    rows = db.execute(
        'SELECT rect_x, rect_y, rect_w, rect_h FROM device_cuts '
        'WHERE paper_id = ? ORDER BY serial', (paper_id,),
    )
    return tuple(tuple(row) for row in rows)


def mark_damage(db: sqlite3.Connection, paper_id: str, cells) -> None:
    sheet = read_paper(db, paper_id)
    if sheet is None:
        raise ValueError(f'unknown paper {paper_id!r}')
    points = []
    for cell in cells:
        if not isinstance(cell, (tuple, list)) or len(cell) != 2:
            raise ValueError('each damage cell must be (x, y)')
        x, y = cell
        if type(x) is not int or type(y) is not int:
            raise ValueError('damage coordinates must be integers')
        if not (0 <= x < sheet.width and 0 <= y < sheet.height):
            raise ValueError(f'damage cell ({x}, {y}) lies off paper {paper_id!r}')
        points.append((x, y))
    cuts = list_paper_cuts(db, paper_id)
    for x, y in points:
        if any(cx <= x < cx + cw and cy <= y < cy + ch for cx, cy, cw, ch in cuts):
            raise ValueError(f'damage cell ({x}, {y}) is already occupied by a cut')
    db.executemany(
        'INSERT OR IGNORE INTO paper_damage (paper_id, x, y) VALUES (?, ?, ?)',
        ((paper_id, x, y) for x, y in points),
    )


def paper_occupancy(
    db: sqlite3.Connection, paper_id: str,
) -> tuple[frozenset[tuple[int, int]], Rects]:
    damaged = frozenset(damage_cells(db, paper_id))
    return damaged, list_paper_cuts(db, paper_id)


def latest_serial(db: sqlite3.Connection) -> int:
    return db.execute('''
        SELECT COALESCE(MAX(serial), (SELECT serial_floor FROM press_config WHERE singleton = 1))
        FROM device_cuts
    ''').fetchone()[0]


def append_cut(
    db: sqlite3.Connection, session: DeviceSession, device_id: str, die: str,
    reading: ScaleReading, *, paper_id: str | None = None,
    rect: tuple[int, int, int, int] | None = None,
) -> PhysicalCut:
    serial = latest_serial(db) + 1
    source = f'{device_id}/{die}/cut/{serial}'
    box = (None, None, None, None) if rect is None else tuple(rect)
    db.execute(
        'INSERT INTO device_cuts (serial, session_id, source, grams, paper_id, rect_x, rect_y, rect_w, '
        'rect_h) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (serial, session.session_id, source, encode_grams(reading.grams), paper_id, *box),
    )
    return PhysicalCut(serial, session.session_id, source, reading.grams, paper_id, rect)


def _as_cut(row: tuple) -> PhysicalCut:
    serial, session_id, source, grams = row[:4]
    paper_id, x, y, w, h = row[4:]
    rect = None if x is None else (x, y, w, h)
    return PhysicalCut(serial, session_id, source, decode_grams(grams), paper_id, rect)


def read_cut(db: sqlite3.Connection, serial: int) -> PhysicalCut | None:
    row = db.execute(
        'SELECT serial, session_id, source, grams, paper_id, rect_x, rect_y, rect_w, rect_h '
        'FROM device_cuts WHERE serial = ?', (serial,),
    ).fetchone()
    return None if row is None else _as_cut(row)


def list_session_cuts(db: sqlite3.Connection, session_id: int) -> tuple[PhysicalCut, ...]:
    rows = db.execute(
        'SELECT serial, session_id, source, grams, paper_id, rect_x, rect_y, rect_w, rect_h '
        'FROM device_cuts WHERE session_id = ? ORDER BY serial', (session_id,),
    )
    return tuple(_as_cut(row) for row in rows)


def list_cuts(db: sqlite3.Connection) -> tuple[PhysicalCut, ...]:
    rows = db.execute(
        'SELECT serial, session_id, source, grams, paper_id, rect_x, rect_y, rect_w, rect_h '
        'FROM device_cuts ORDER BY serial',
    )
    return tuple(_as_cut(row) for row in rows)

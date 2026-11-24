from collections.abc import Iterable
from dataclasses import dataclass
import sqlite3

from archive.store import archive_transaction
from worklist.recipes import resolve_recipe
from worklist.shifts import RegistrationConflict
from worklist.tickets import Ticket, validate_identifier

Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class SharedShift:
    shift_id: str
    paper_id: str
    width: int
    height: int


@dataclass(frozen=True)
class SharedEntry:
    ticket_id: str
    recipe: str
    candidates: tuple[Rect, ...]


def initialize_shared(db: sqlite3.Connection) -> None:
    with archive_transaction(db):
        db.execute('''
            CREATE TABLE IF NOT EXISTS shared_shifts (
                shift_id TEXT PRIMARY KEY NOT NULL REFERENCES shifts(shift_id),
                paper_id TEXT NOT NULL UNIQUE,
                width INTEGER NOT NULL CHECK (width > 0),
                height INTEGER NOT NULL CHECK (height > 0)
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS shared_candidates (
                shift_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL CHECK (ordinal > 0),
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                w INTEGER NOT NULL CHECK (w > 0),
                h INTEGER NOT NULL CHECK (h > 0),
                PRIMARY KEY (shift_id, ticket_id, ordinal),
                FOREIGN KEY (shift_id, ticket_id) REFERENCES tickets(shift_id, ticket_id)
            )
        ''')


def is_shared_shift(db: sqlite3.Connection, shift_id: str) -> bool:
    return db.execute(
        'SELECT 1 FROM shared_shifts WHERE shift_id = ?', (shift_id,),
    ).fetchone() is not None


def load_shared_shift(db: sqlite3.Connection, shift_id: str) -> SharedShift | None:
    row = db.execute(
        'SELECT shift_id, paper_id, width, height FROM shared_shifts WHERE shift_id = ?',
        (shift_id,),
    ).fetchone()
    return None if row is None else SharedShift(*row)


def require_shared_shift(db: sqlite3.Connection, shift_id: str) -> SharedShift:
    shared = load_shared_shift(db, shift_id)
    if shared is None:
        raise ValueError(f'shift {shift_id!r} is not a shared-paper shift')
    return shared


def paper_owner(db: sqlite3.Connection, paper_id: str) -> str | None:
    row = db.execute(
        'SELECT shift_id FROM shared_shifts WHERE paper_id = ?', (paper_id,),
    ).fetchone()
    return None if row is None else row[0]


def _positive_dimension(value: int, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f'{label} must be a positive integer')
    return value


def _validate_rect(candidate, width: int, height: int) -> Rect:
    if not isinstance(candidate, (tuple, list)) or len(candidate) != 4:
        raise ValueError('each candidate must be (x, y, w, h)')
    x, y, w, h = candidate
    for value, label in ((x, 'x'), (y, 'y'), (w, 'w'), (h, 'h')):
        if type(value) is not int:
            raise ValueError(f'candidate {label} must be an integer')
    if w <= 0 or h <= 0:
        raise ValueError('candidate width and height must be positive')
    if x < 0 or y < 0:
        raise ValueError('candidate origin must not be negative')
    if x + w > width or y + h > height:
        raise ValueError('candidate must lie completely on the paper')
    return (x, y, w, h)


def validate_shared_entries(
    shift_id: str, width: int, height: int, entries: Iterable[tuple[str, str, Iterable]],
) -> tuple[SharedEntry, ...]:
    validate_identifier(shift_id, 'shift id')
    _positive_dimension(width, 'paper width')
    _positive_dimension(height, 'paper height')
    source = tuple(entries)
    if not source:
        raise ValueError('a shared shift needs at least one ticket')
    validated = []
    seen = set()
    for entry in source:
        if not isinstance(entry, (tuple, list)) or len(entry) != 3:
            raise ValueError('each shared ticket needs an id, a recipe and candidates')
        ticket_id, recipe, candidates = entry
        validate_identifier(ticket_id, 'ticket id')
        if ticket_id in seen:
            raise ValueError(f'duplicate ticket {ticket_id!r} in shift {shift_id!r}')
        seen.add(ticket_id)
        code = resolve_recipe(recipe).code
        raw = tuple(candidates)
        if not raw:
            raise ValueError(f'ticket {ticket_id!r} needs at least one candidate')
        rects = tuple(_validate_rect(item, width, height) for item in raw)
        validated.append(SharedEntry(ticket_id, code, rects))
    return tuple(validated)


def _stored_entries(db: sqlite3.Connection, shift_id: str) -> tuple[SharedEntry, ...]:
    rows = db.execute(
        'SELECT ticket_id, recipe, position FROM tickets WHERE shift_id = ? ORDER BY position',
        (shift_id,),
    ).fetchall()
    entries = []
    for ticket_id, recipe, _position in rows:
        rects = db.execute(
            'SELECT x, y, w, h FROM shared_candidates WHERE shift_id = ? AND ticket_id = ? '
            'ORDER BY ordinal', (shift_id, ticket_id),
        ).fetchall()
        entries.append(SharedEntry(ticket_id, recipe, tuple(tuple(rect) for rect in rects)))
    return tuple(entries)


def register_shared(
    db: sqlite3.Connection, shift_id: str, paper_id: str, width: int, height: int,
    entries: Iterable[tuple[str, str, Iterable]],
) -> str:
    validate_identifier(paper_id, 'paper id')
    requested = validate_shared_entries(shift_id, width, height, entries)
    with archive_transaction(db):
        shared = load_shared_shift(db, shift_id)
        if shared is not None:
            if (shared.paper_id, shared.width, shared.height) != (paper_id, width, height):
                raise RegistrationConflict(
                    f'shift {shift_id!r} is already registered on different paper parameters')
            if _stored_entries(db, shift_id) != requested:
                raise RegistrationConflict(
                    f'shift {shift_id!r} already has a different shared worklist')
            return shift_id
        existing = db.execute(
            'SELECT 1 FROM shifts WHERE shift_id = ?', (shift_id,),
        ).fetchone()
        if existing is not None:
            raise RegistrationConflict(
                f'shift {shift_id!r} is registered as a regular shift')
        owner = paper_owner(db, paper_id)
        if owner is not None and owner != shift_id:
            raise RegistrationConflict(f'paper {paper_id!r} already belongs to shift {owner!r}')
        db.execute('INSERT INTO shifts (shift_id) VALUES (?)', (shift_id,))
        db.execute(
            'INSERT INTO shared_shifts (shift_id, paper_id, width, height) VALUES (?, ?, ?, ?)',
            (shift_id, paper_id, width, height),
        )
        for position, entry in enumerate(requested, start=1):
            db.execute(
                'INSERT INTO tickets (shift_id, ticket_id, position, recipe) VALUES (?, ?, ?, ?)',
                (shift_id, entry.ticket_id, position, entry.recipe),
            )
            db.executemany(
                'INSERT INTO shared_candidates (shift_id, ticket_id, ordinal, x, y, w, h) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                ((shift_id, entry.ticket_id, ordinal, *rect)
                 for ordinal, rect in enumerate(entry.candidates, start=1)),
            )
    return shift_id


def candidates_for(db: sqlite3.Connection, shift_id: str) -> dict[str, tuple[Rect, ...]]:
    rows = db.execute(
        'SELECT ticket_id, x, y, w, h FROM shared_candidates WHERE shift_id = ? '
        'ORDER BY ticket_id, ordinal', (shift_id,),
    ).fetchall()
    grouped: dict[str, list[Rect]] = {}
    for ticket_id, x, y, w, h in rows:
        grouped.setdefault(ticket_id, []).append((x, y, w, h))
    return {ticket_id: tuple(rects) for ticket_id, rects in grouped.items()}


def rect_cells(rect: Rect) -> frozenset[tuple[int, int]]:
    x, y, w, h = rect
    return frozenset((cx, cy) for cx in range(x, x + w) for cy in range(y, y + h))


def solve_placement(
    order: tuple[str, ...], candidates: dict[str, tuple[Rect, ...]],
    occupied: frozenset[tuple[int, int]] | set[tuple[int, int]],
) -> dict[str, Rect] | None:
    """Assign one candidate per ticket so that no cells overlap damage or prior cuts."""
    choice: dict[str, Rect] = {}
    used = set(occupied)

    def recurse(index: int) -> bool:
        if index == len(order):
            return True
        ticket_id = order[index]
        for rect in candidates.get(ticket_id, ()):
            cells = rect_cells(rect)
            if cells & used:
                continue
            used.update(cells)
            choice[ticket_id] = rect
            if recurse(index + 1):
                return True
            del choice[ticket_id]
            used.difference_update(cells)
        return False

    return dict(choice) if recurse(0) else None


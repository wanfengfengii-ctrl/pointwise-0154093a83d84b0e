from dataclasses import dataclass
import sqlite3

from device.ledger import PhysicalCut
from device.scale import decode_grams, encode_grams


@dataclass(frozen=True)
class TicketTerminal:
    shift_id: str
    ticket_id: str
    cut: PhysicalCut


def insert_terminal(db: sqlite3.Connection, terminal: TicketTerminal) -> None:
    cut = terminal.cut
    db.execute('''
        INSERT INTO terminals (shift_id, ticket_id, serial, session_id, source, grams)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (terminal.shift_id, terminal.ticket_id, cut.serial, cut.session_id,
          cut.source, encode_grams(cut.grams)))


def find_terminal(db: sqlite3.Connection, shift_id: str, ticket_id: str) -> TicketTerminal | None:
    row = db.execute('''
        SELECT serial, session_id, source, grams FROM terminals
        WHERE shift_id = ? AND ticket_id = ?
    ''', (shift_id, ticket_id)).fetchone()
    if row is None:
        return None
    serial, session_id, source, grams = row
    return TicketTerminal(shift_id, ticket_id, PhysicalCut(serial, session_id, source, decode_grams(grams)))


def find_serial_owner(db: sqlite3.Connection, serial: int) -> tuple[str, str] | None:
    row = db.execute('SELECT shift_id, ticket_id FROM terminals WHERE serial = ?', (serial,)).fetchone()
    return None if row is None else (row[0], row[1])

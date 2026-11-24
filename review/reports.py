from dataclasses import dataclass
import json
import sqlite3

from archive.history import TicketStatus
from archive.store import archive_transaction
from device.ledger import PhysicalCut
from device.scale import decode_grams, encode_grams
from review.detail import DetailRow, ShiftDetail
from review.totals import ShiftTotals
from worklist.tickets import Ticket


@dataclass(frozen=True)
class ShiftReport:
    number: str
    detail: ShiftDetail
    totals: ShiftTotals


def initialize_reports(db: sqlite3.Connection) -> None:
    with archive_transaction(db):
        db.execute('''
            CREATE TABLE IF NOT EXISTS shift_reports (
                number TEXT PRIMARY KEY NOT NULL,
                shift_id TEXT NOT NULL UNIQUE REFERENCES shifts(shift_id),
                rows_json TEXT NOT NULL,
                cut_count INTEGER NOT NULL,
                valid_count INTEGER NOT NULL,
                grams_total TEXT NOT NULL
            )
        ''')


def save_report(db: sqlite3.Connection, report: ShiftReport) -> None:
    rows = []
    for row in report.detail.rows:
        if row.status != TicketStatus.CLOSED or row.cut is None:
            raise ValueError('fixed report rows must have closed tickets')
        cut = row.cut
        rows.append({
            'ticket_id': row.ticket.ticket_id, 'position': row.ticket.position,
            'recipe': row.ticket.recipe, 'serial': cut.serial, 'session_id': cut.session_id,
            'source': cut.source, 'grams': encode_grams(cut.grams),
        })
    db.execute('''
        INSERT INTO shift_reports (number, shift_id, rows_json, cut_count, valid_count, grams_total)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (report.number, report.detail.shift_id, json.dumps(rows, ensure_ascii=False),
          report.totals.cuts, report.totals.valid_readings, encode_grams(report.totals.grams)))


def _restore(row: tuple | None) -> ShiftReport | None:
    if row is None:
        return None
    number, shift_id, rows_json, cuts, valid, grams = row
    rows = []
    for item in json.loads(rows_json):
        ticket = Ticket(shift_id, item['ticket_id'], item['position'], item['recipe'])
        cut = PhysicalCut(item['serial'], item['session_id'], item['source'], decode_grams(item['grams']))
        rows.append(DetailRow(ticket, TicketStatus.CLOSED, cut))
    return ShiftReport(number, ShiftDetail(shift_id, tuple(rows)), ShiftTotals(cuts, valid, decode_grams(grams)))


def find_report_by_number(db: sqlite3.Connection, number: str) -> ShiftReport | None:
    return _restore(db.execute('''
        SELECT number, shift_id, rows_json, cut_count, valid_count, grams_total
        FROM shift_reports WHERE number = ?
    ''', (number,)).fetchone())


def find_report_for_shift(db: sqlite3.Connection, shift_id: str) -> ShiftReport | None:
    return _restore(db.execute('''
        SELECT number, shift_id, rows_json, cut_count, valid_count, grams_total
        FROM shift_reports WHERE shift_id = ?
    ''', (shift_id,)).fetchone())

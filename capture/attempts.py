from dataclasses import dataclass, replace
from enum import StrEnum
import sqlite3

from archive.store import archive_transaction
from capture.evidence import (
    CaptureEvidence, EvidenceStatus, evidence_from_receipt, no_execution_evidence, pending_evidence,
)
from device.ledger import PhysicalCut
from device.scale import decode_grams, encode_grams
from device.sessions import DeviceSession
from worklist.tickets import Ticket


class AttemptConflict(ValueError):
    pass


class AttemptState(StrEnum):
    PREPARED = 'prepared'
    PENDING = 'pending'
    EXECUTED = 'executed'
    NO_EXECUTION = 'no_execution'
    ARCHIVED = 'archived'
    SUPERSEDED = 'superseded'


@dataclass(frozen=True)
class CaptureAttempt:
    ticket: Ticket
    session_id: int
    start_serial: int
    state: AttemptState
    evidence: CaptureEvidence


def initialize_attempts(db: sqlite3.Connection) -> None:
    with archive_transaction(db):
        db.execute('''
            CREATE TABLE IF NOT EXISTS capture_attempts (
                session_id INTEGER PRIMARY KEY,
                shift_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                start_serial INTEGER NOT NULL CHECK (start_serial >= 0),
                state TEXT NOT NULL CHECK (state IN (
                    'prepared', 'pending', 'executed', 'no_execution', 'archived', 'superseded'
                )),
                serial INTEGER,
                source TEXT,
                grams TEXT,
                FOREIGN KEY (shift_id, ticket_id) REFERENCES tickets(shift_id, ticket_id),
                CHECK ((state IN ('executed', 'archived') AND serial IS NOT NULL
                        AND serial > start_serial AND source IS NOT NULL)
                    OR (state NOT IN ('executed', 'archived') AND serial IS NULL
                        AND source IS NULL AND grams IS NULL))
            )
        ''')
        db.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS one_live_attempt_per_shift
            ON capture_attempts(shift_id) WHERE state NOT IN ('archived', 'superseded')
        ''')


def begin_attempt(db: sqlite3.Connection, ticket: Ticket, session: DeviceSession) -> CaptureAttempt:
    if session.sealed:
        raise AttemptConflict('cannot prepare a submission to a sealed session')
    db.execute('''
        INSERT INTO capture_attempts (session_id, shift_id, ticket_id, start_serial, state)
        VALUES (?, ?, ?, ?, 'prepared')
    ''', (session.session_id, ticket.shift_id, ticket.ticket_id, session.start_serial))
    evidence = pending_evidence(session.session_id, session.start_serial)
    return CaptureAttempt(ticket, session.session_id, session.start_serial, AttemptState.PREPARED, evidence)


def load_pending_attempt(db: sqlite3.Connection, shift_id: str) -> CaptureAttempt | None:
    row = db.execute('''
        SELECT a.session_id, a.start_serial, a.state, a.serial, a.source, a.grams,
               t.shift_id, t.ticket_id, t.position, t.recipe
        FROM capture_attempts AS a JOIN tickets AS t
            ON t.shift_id = a.shift_id AND t.ticket_id = a.ticket_id
        WHERE a.shift_id = ? AND a.state NOT IN ('archived', 'superseded')
    ''', (shift_id,)).fetchone()
    if row is None:
        return None
    session_id, start, state, serial, source, grams, *ticket = row
    state = AttemptState(state)
    if state == AttemptState.EXECUTED:
        cut = PhysicalCut(serial, session_id, source, decode_grams(grams))
        evidence = evidence_from_receipt(session_id, start, cut)
    elif state == AttemptState.NO_EXECUTION:
        evidence = no_execution_evidence(session_id, start)
    else:
        evidence = pending_evidence(session_id, start)
    return CaptureAttempt(Ticket(*ticket), session_id, start, state, evidence)


def record_evidence(
    db: sqlite3.Connection, attempt: CaptureAttempt, evidence: CaptureEvidence,
) -> CaptureAttempt | None:
    if (evidence.session_id, evidence.start_serial) != (attempt.session_id, attempt.start_serial):
        raise AttemptConflict('evidence references a different attempt interval')
    current = load_pending_attempt(db, attempt.ticket.shift_id)
    if current is None or current.session_id != attempt.session_id or current.ticket != attempt.ticket:
        return None
    if current.evidence.status != EvidenceStatus.PENDING:
        if evidence.status != EvidenceStatus.PENDING and evidence != current.evidence:
            raise AttemptConflict('the attempt already has different definitive evidence')
        return current
    state = AttemptState(evidence.status.value)
    cut = evidence.cut
    columns = (None, None, None) if cut is None else (cut.serial, cut.source, encode_grams(cut.grams))
    db.execute('''
        UPDATE capture_attempts SET state = ?, serial = ?, source = ?, grams = ? WHERE session_id = ?
    ''', (state, *columns, attempt.session_id))
    return replace(current, state=state, evidence=evidence)


def authorize_retry(db: sqlite3.Connection, attempt: CaptureAttempt) -> None:
    change = db.execute('''
        UPDATE capture_attempts SET state = 'superseded'
        WHERE session_id = ? AND shift_id = ? AND ticket_id = ? AND state = 'no_execution'
    ''', (attempt.session_id, attempt.ticket.shift_id, attempt.ticket.ticket_id))
    if change.rowcount != 1:
        raise AttemptConflict('the old attempt does not have an unused retry authorization')


def mark_archived(db: sqlite3.Connection, attempt: CaptureAttempt) -> None:
    cut = attempt.evidence.cut
    if cut is None:
        raise AttemptConflict('archiving requires proof of execution')
    change = db.execute('''
        UPDATE capture_attempts SET state = 'archived'
        WHERE session_id = ? AND shift_id = ? AND ticket_id = ? AND state = 'executed' AND serial = ?
    ''', (attempt.session_id, attempt.ticket.shift_id, attempt.ticket.ticket_id, cut.serial))
    if change.rowcount != 1:
        raise AttemptConflict('the executed attempt could not be marked as archived')

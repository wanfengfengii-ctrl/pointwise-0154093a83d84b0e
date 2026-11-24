import sqlite3

from archive.history import TicketView, read_ticket
from archive.intake import accept_terminal
from archive.progress import claim_shift, current_ticket
from archive.store import archive_transaction, read_snapshot
from capture.attempts import (
    CaptureAttempt, authorize_retry, begin_attempt, load_pending_attempt, record_evidence,
)
from capture.evidence import CaptureEvidence, EvidenceStatus, evidence_from_receipt, pending_evidence
from device.press import DevicePort, DeviceUnavailable, SubmitUnknown
from device.sessions import SessionClosed
from worklist.tickets import Ticket


def advance(db: sqlite3.Connection, shift_id: str, device: DevicePort) -> TicketView | None:
    with read_snapshot(db):
        ticket = current_ticket(db, shift_id)
    if ticket is None:
        return None
    return dispatch_ticket(db, device, ticket)


def dispatch_ticket(
    db: sqlite3.Connection, device: DevicePort, ticket: Ticket, *, retry: CaptureAttempt | None = None,
) -> TicketView:
    with archive_transaction(db):
        if current_ticket(db, ticket.shift_id) != ticket:
            return read_ticket(db, ticket.shift_id, ticket.ticket_id)
        active = load_pending_attempt(db, ticket.shift_id)
        if active != retry:
            return read_ticket(db, ticket.shift_id, ticket.ticket_id)
        claim_shift(db, ticket)
        if retry is not None:
            authorize_retry(db, retry)
        session = device.open_session()
        attempt = begin_attempt(db, ticket, session)

    try:
        cut = device.submit_cut(attempt.session_id, ticket.recipe)
    except (SubmitUnknown, SessionClosed, DeviceUnavailable):
        evidence = pending_evidence(attempt.session_id, attempt.start_serial)
    else:
        try:
            evidence = evidence_from_receipt(attempt.session_id, attempt.start_serial, cut)
        except ValueError:
            evidence = pending_evidence(attempt.session_id, attempt.start_serial)
    return deliver_evidence(db, attempt, evidence)


def deliver_evidence(
    db: sqlite3.Connection, attempt: CaptureAttempt, evidence: CaptureEvidence,
) -> TicketView:
    with archive_transaction(db):
        saved = record_evidence(db, attempt, evidence)
    ticket = attempt.ticket
    if saved is not None and saved.evidence.status == EvidenceStatus.EXECUTED:
        accept_terminal(db, ticket, saved.evidence)
    return read_ticket(db, ticket.shift_id, ticket.ticket_id)

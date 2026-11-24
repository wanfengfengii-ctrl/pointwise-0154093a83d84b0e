import sqlite3

from archive.history import TicketView, read_ticket
from archive.progress import current_ticket
from archive.store import archive_transaction, read_snapshot
from capture.attempts import load_pending_attempt, record_evidence
from capture.dispatch import deliver_evidence, dispatch_ticket
from capture.evidence import EvidenceStatus
from capture.reconcile import reconcile_attempt
from capture.shared import resume_shared  # re-exported shared-paper entry point
from device.press import DevicePort
from worklist.shared import is_shared_shift


def resume(db: sqlite3.Connection, shift_id: str, device: DevicePort) -> TicketView | None:
    with read_snapshot(db):
        if is_shared_shift(db, shift_id):
            raise ValueError(f'shift {shift_id!r} is registered as shared paper; use resume_shared')
        ticket = current_ticket(db, shift_id)
        attempt = load_pending_attempt(db, shift_id)
    if ticket is None:
        return None
    if attempt is None:
        return dispatch_ticket(db, device, ticket)
    if attempt.ticket != ticket:
        raise ValueError('the pending attempt does not belong to the current ticket')

    evidence = reconcile_attempt(device, attempt)
    if evidence.status != EvidenceStatus.NO_EXECUTION:
        return deliver_evidence(db, attempt, evidence)
    with archive_transaction(db):
        saved = record_evidence(db, attempt, evidence)
    if saved is None:
        return read_ticket(db, shift_id, ticket.ticket_id)
    return dispatch_ticket(db, device, ticket, retry=saved)

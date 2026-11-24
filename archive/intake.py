import sqlite3

from archive.progress import TicketOutOfOrder, advance_progress, require_current_ticket
from archive.receipts import TicketTerminal, find_serial_owner, find_terminal, insert_terminal
from archive.store import archive_transaction
from capture.attempts import load_pending_attempt, mark_archived
from capture.evidence import CaptureEvidence, EvidenceStatus
from worklist.tickets import Ticket


class AttributionConflict(ValueError):
    pass


def accept_terminal(db: sqlite3.Connection, ticket: Ticket, evidence: CaptureEvidence) -> TicketTerminal:
    cut = evidence.cut
    if evidence.status != EvidenceStatus.EXECUTED or cut is None:
        raise AttributionConflict('a terminal requires a confirmed physical cut')
    terminal = TicketTerminal(ticket.shift_id, ticket.ticket_id, cut)
    with archive_transaction(db):
        existing = find_terminal(db, ticket.shift_id, ticket.ticket_id)
        if existing is not None:
            if existing == terminal:
                return existing
            raise AttributionConflict('the original ticket already owns different evidence')
        owner = find_serial_owner(db, cut.serial)
        if owner is not None:
            raise AttributionConflict(f'cut {cut.serial} already belongs to {owner!r}')
        try:
            require_current_ticket(db, ticket)
        except TicketOutOfOrder as exc:
            raise AttributionConflict(str(exc)) from exc
        attempt = load_pending_attempt(db, ticket.shift_id)
        if attempt is None or attempt.ticket != ticket or attempt.evidence != evidence:
            raise AttributionConflict('confirmed execution is outside the current ticket attempt')
        insert_terminal(db, terminal)
        advance_progress(db, ticket)
        mark_archived(db, attempt)
    return terminal

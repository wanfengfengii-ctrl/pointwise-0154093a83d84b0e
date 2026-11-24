import sqlite3

from archive.history import TicketStatus, TicketView, read_ticket
from archive.progress import claim_shift, current_ticket
from archive.store import archive_transaction, read_snapshot
from capture.attempts import (
    CaptureAttempt, authorize_retry, begin_attempt, load_pending_attempt, record_evidence,
)
from capture.dispatch import deliver_evidence
from capture.evidence import (
    EvidenceStatus, evidence_from_receipt, no_execution_evidence, pending_evidence,
)
from capture.reconcile import reconcile_attempt
from device.press import DevicePort, DeviceUnavailable, PlacementConflict, SubmitUnknown
from device.sessions import SessionClosed
from worklist.order import ordered_tickets
from worklist.shared import (
    Rect, SharedShift, candidates_for, load_shared_shift, rect_cells, require_shared_shift,
    solve_placement,
)
from worklist.tickets import Ticket


BLOCKED = 'blocked'
PENDING = 'pending'
CLOSED = 'closed'


class SharedOutcome:
    def __init__(self, ticket: Ticket, status: str, terminal=None) -> None:
        self.ticket = ticket
        self.status = status
        self.terminal = terminal

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SharedOutcome):
            return NotImplemented
        return (self.ticket, self.status, self.terminal) == (
            other.ticket, other.status, other.terminal)

    def __repr__(self) -> str:
        return (f'SharedOutcome(ticket={self.ticket!r}, status={self.status!r}, '
                f'terminal={self.terminal!r})')


def _outcome(view: TicketView) -> SharedOutcome:
    status = CLOSED if view.status == TicketStatus.CLOSED else PENDING
    return SharedOutcome(view.ticket, status, view.terminal)


def _blocked(db: sqlite3.Connection, shift_id: str, ticket: Ticket) -> SharedOutcome:
    view = read_ticket(db, shift_id, ticket.ticket_id)
    return SharedOutcome(view.ticket, BLOCKED, view.terminal)


def paper_occupancy(device: DevicePort, paper_id: str) -> set[tuple[int, int]]:
    press = getattr(device, 'press', None)
    _sheet, damaged, cuts = press.paper_state(paper_id)
    occupied = set(damaged)
    for rect in cuts:
        occupied |= set(rect_cells(rect))
    return occupied


def plan_placement(
    db: sqlite3.Connection, device: DevicePort, shift_id: str, ticket: Ticket,
) -> tuple[SharedShift, Rect] | None:
    shared = load_shared_shift(db, shift_id)
    if shared is None:
        raise ValueError(f'shift {shift_id!r} is not a shared-paper shift')
    remaining = tuple(
        item.ticket_id for item in ordered_tickets(db, shift_id)
        if item.position >= ticket.position
    )
    candidates = candidates_for(db, shift_id)
    occupied = paper_occupancy(device, shared.paper_id)
    assignment = solve_placement(remaining, candidates, occupied)
    if assignment is None:
        return None
    return shared, assignment[ticket.ticket_id]


def dispatch_shared_ticket(
    db: sqlite3.Connection, device: DevicePort, ticket: Ticket, shared: SharedShift, rect: Rect,
    *, retry: CaptureAttempt | None = None,
) -> tuple[TicketView, bool]:
    with archive_transaction(db):
        if current_ticket(db, ticket.shift_id) != ticket:
            return read_ticket(db, ticket.shift_id, ticket.ticket_id), False
        active = load_pending_attempt(db, ticket.shift_id)
        if active != retry:
            return read_ticket(db, ticket.shift_id, ticket.ticket_id), False
        claim_shift(db, ticket)
        if retry is not None:
            authorize_retry(db, retry)
        session = device.open_managed_session()
        attempt = begin_attempt(db, ticket, session, paper_id=shared.paper_id, rect=rect)
        remaining = tuple(
            item.ticket_id for item in ordered_tickets(db, ticket.shift_id)
            if item.position > ticket.position
        )
        candidates = candidates_for(db, ticket.shift_id)

    try:
        cut = device.submit_shared_cut(
            attempt.session_id, ticket.recipe, shared.paper_id, rect,
            ticket_id=ticket.ticket_id, remaining=remaining, candidates=candidates,
        )
    except (SubmitUnknown, SessionClosed, DeviceUnavailable):
        evidence = pending_evidence(attempt.session_id, attempt.start_serial)
    except PlacementConflict:
        evidence = no_execution_evidence(attempt.session_id, attempt.start_serial)
    else:
        try:
            evidence = evidence_from_receipt(attempt.session_id, attempt.start_serial, cut)
        except ValueError:
            evidence = pending_evidence(attempt.session_id, attempt.start_serial)
    view = deliver_evidence(db, attempt, evidence)
    if evidence.status == EvidenceStatus.NO_EXECUTION:
        with archive_transaction(db):
            current = load_pending_attempt(db, ticket.shift_id)
            if current is not None and current.session_id == attempt.session_id:
                authorize_retry(db, current)
        return read_ticket(db, ticket.shift_id, ticket.ticket_id), True
    return view, False


def resume_shared(db: sqlite3.Connection, shift_id: str, device: DevicePort) -> SharedOutcome | None:
    with read_snapshot(db):
        require_shared_shift(db, shift_id)
        ticket = current_ticket(db, shift_id)
        attempt = load_pending_attempt(db, shift_id)
    if ticket is None:
        return None
    retry: CaptureAttempt | None = None
    if attempt is not None:
        if attempt.ticket != ticket:
            raise ValueError('the pending attempt does not belong to the current ticket')
        evidence = reconcile_attempt(device, attempt)
        if evidence.status != EvidenceStatus.NO_EXECUTION:
            return _outcome(deliver_evidence(db, attempt, evidence))
        with archive_transaction(db):
            saved = record_evidence(db, attempt, evidence)
        if saved is None:
            return _outcome(read_ticket(db, shift_id, ticket.ticket_id))
        retry = saved

    planned = plan_placement(db, device, shift_id, ticket)
    if planned is None:
        return _blocked(db, shift_id, ticket)
    shared, rect = planned
    view, refused = dispatch_shared_ticket(db, device, ticket, shared, rect, retry=retry)
    if refused:
        return _blocked(db, shift_id, ticket)
    return _outcome(view)

from dataclasses import dataclass
from enum import StrEnum
import sqlite3

from archive.receipts import TicketTerminal, find_terminal
from archive.store import read_snapshot
from capture.attempts import load_pending_attempt
from worklist.tickets import Ticket, find_ticket


class TicketNotFound(LookupError):
    pass


class TicketStatus(StrEnum):
    TODO = 'todo'
    PENDING = 'pending'
    CLOSED = 'closed'


@dataclass(frozen=True)
class TicketView:
    ticket: Ticket
    status: TicketStatus
    terminal: TicketTerminal | None = None


def read_ticket(db: sqlite3.Connection, shift_id: str, ticket_id: str) -> TicketView:
    with read_snapshot(db):
        ticket = find_ticket(db, shift_id, ticket_id)
        if ticket is None:
            raise TicketNotFound((shift_id, ticket_id))
        terminal = find_terminal(db, shift_id, ticket_id)
        if terminal is not None:
            return TicketView(ticket, TicketStatus.CLOSED, terminal)
        attempt = load_pending_attempt(db, shift_id)
        if attempt is not None and attempt.ticket == ticket:
            return TicketView(ticket, TicketStatus.PENDING)
        return TicketView(ticket, TicketStatus.TODO)

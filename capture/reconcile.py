from capture.attempts import CaptureAttempt
from capture.evidence import (
    CaptureEvidence, EvidenceStatus, evidence_from_receipt, no_execution_evidence, pending_evidence,
)
from device.ledger import PhysicalCut
from device.press import DevicePort, DeviceUnavailable
from device.sessions import SessionNotFound


def read_closed_session_evidence(
    device: DevicePort, attempt: CaptureAttempt,
) -> tuple[PhysicalCut, ...] | None:
    try:
        closed = device.close_session(attempt.session_id)
        if (not closed.sealed or closed.session_id != attempt.session_id or
                closed.start_serial != attempt.start_serial):
            return None
        snapshot = device.read_session(attempt.session_id)
    except (DeviceUnavailable, SessionNotFound):
        return None
    if not snapshot.complete or snapshot.session != closed:
        return None
    if any(cut.session_id != attempt.session_id or cut.serial <= attempt.start_serial
           for cut in snapshot.cuts):
        return None
    return snapshot.cuts


def reconcile_attempt(device: DevicePort, attempt: CaptureAttempt) -> CaptureEvidence:
    if attempt.evidence.status != EvidenceStatus.PENDING:
        return attempt.evidence
    cuts = read_closed_session_evidence(device, attempt)
    if cuts is None or len(cuts) > 1:
        return pending_evidence(attempt.session_id, attempt.start_serial)
    if not cuts:
        return no_execution_evidence(attempt.session_id, attempt.start_serial)
    return evidence_from_receipt(attempt.session_id, attempt.start_serial, cuts[0])

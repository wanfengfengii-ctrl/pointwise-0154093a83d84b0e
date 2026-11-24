from dataclasses import dataclass
from enum import StrEnum

from device.ledger import PhysicalCut


class EvidenceStatus(StrEnum):
    PENDING = 'pending'
    NO_EXECUTION = 'no_execution'
    EXECUTED = 'executed'


@dataclass(frozen=True)
class CaptureEvidence:
    status: EvidenceStatus
    session_id: int
    start_serial: int
    cut: PhysicalCut | None = None

    def __post_init__(self) -> None:
        if self.status == EvidenceStatus.EXECUTED:
            if (self.cut is None or self.cut.session_id != self.session_id or
                    self.cut.serial <= self.start_serial):
                raise ValueError('receipt is outside the original session interval')
        elif self.cut is not None:
            raise ValueError('only confirmed execution carries a physical cut')


def evidence_from_receipt(session_id: int, start_serial: int, receipt: PhysicalCut) -> CaptureEvidence:
    return CaptureEvidence(EvidenceStatus.EXECUTED, session_id, start_serial, receipt)


def pending_evidence(session_id: int, start_serial: int) -> CaptureEvidence:
    return CaptureEvidence(EvidenceStatus.PENDING, session_id, start_serial)


def no_execution_evidence(session_id: int, start_serial: int) -> CaptureEvidence:
    return CaptureEvidence(EvidenceStatus.NO_EXECUTION, session_id, start_serial)

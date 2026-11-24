from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from device.ledger import PhysicalCut
from device.press import DeviceUnavailable, PlacementConflict, Press, SessionSnapshot, SubmitUnknown
from device.scale import validate_scale_reading
from device.sessions import DeviceSession, SessionClosed
from worklist.recipes import resolve_recipe


class DeviceStep(StrEnum):
    RETURN = 'return'
    LOSE_BEFORE_EXECUTION = 'lose_before_execution'
    LOSE_AFTER_EXECUTION = 'lose_after_execution'
    DEFER_EXECUTION = 'defer_execution'
    BLOCK_CLOSE = 'block_close'
    ALLOW_CLOSE = 'allow_close'
    BLOCK_READ = 'block_read'
    ALLOW_READ = 'allow_read'
    RELEASE_QUEUED = 'release_queued'


_SUBMISSION_STEPS = frozenset({
    DeviceStep.RETURN, DeviceStep.LOSE_BEFORE_EXECUTION,
    DeviceStep.LOSE_AFTER_EXECUTION, DeviceStep.DEFER_EXECUTION,
})


@dataclass(frozen=True)
class Submission:
    session_id: int
    recipe: str
    step: DeviceStep
    paper_id: str | None = None
    rect: tuple[int, int, int, int] | None = None


class ScriptedDevice:
    def __init__(
        self, press: Press, *, readings: Iterable[int | None],
        submissions: Iterable[DeviceStep] = (),
    ) -> None:
        readings = tuple(readings)
        for grams in readings:
            validate_scale_reading(grams)
        planned = tuple(DeviceStep(step) for step in submissions)
        if any(step not in _SUBMISSION_STEPS for step in planned):
            raise ValueError('the submission plan contains a control step')
        self.press = press
        self._readings = deque(readings)
        self._planned = deque(planned)
        self._submissions: list[Submission] = []
        self._queue: dict[int, str] = {}
        self._close_available = True
        self._read_complete = True
        self._lock = Lock()

    @property
    def submissions(self) -> tuple[Submission, ...]:
        with self._lock:
            return tuple(self._submissions)

    def open_session(self) -> DeviceSession:
        return self.press.open_session()

    def open_managed_session(self) -> DeviceSession:
        return self.press.open_managed_session()

    def _next_reading(self) -> int | None:
        if not self._readings:
            raise DeviceUnavailable('no scale reading has been scripted for the next cut')
        return self._readings[0]

    def _execute(self, session_id: int, recipe: str, **location) -> PhysicalCut:
        cut = self.press.submit_cut(session_id, recipe, self._next_reading, **location)
        self._readings.popleft()
        return cut

    def _queue_request(self, session_id: int, request: tuple) -> None:
        self._queue[session_id] = request

    def _release(self, session_id: int) -> PhysicalCut | None:
        request = self._queue[session_id]
        try:
            cut = self._execute(session_id, *request[:1], **request[1])
        except SessionClosed:
            del self._queue[session_id]
            return None
        except PlacementConflict:
            del self._queue[session_id]
            return None
        del self._queue[session_id]
        return cut

    def submit_cut(self, session_id: int, recipe: str) -> PhysicalCut:
        resolve_recipe(recipe)
        with self._lock:
            action = self._planned.popleft() if self._planned else DeviceStep.RETURN
            self._submissions.append(Submission(session_id, recipe, action))
            if action == DeviceStep.LOSE_BEFORE_EXECUTION:
                raise SubmitUnknown('submission response lost')
            if action == DeviceStep.DEFER_EXECUTION:
                self._queue_request(session_id, (recipe, {}))
                raise SubmitUnknown('submission response lost')
            cut = self._execute(session_id, recipe)
            if action == DeviceStep.LOSE_AFTER_EXECUTION:
                raise SubmitUnknown('submission response lost')
            return cut

    def submit_shared_cut(
        self, session_id: int, recipe: str, paper_id: str, rect: tuple[int, int, int, int],
        *, ticket_id: str | None = None, remaining: tuple[str, ...] | None = None,
        candidates: dict | None = None,
    ) -> PhysicalCut:
        resolve_recipe(recipe)
        location = {'paper_id': paper_id, 'rect': tuple(rect), 'ticket_id': ticket_id,
                    'remaining': remaining, 'candidates': candidates}
        with self._lock:
            action = self._planned.popleft() if self._planned else DeviceStep.RETURN
            self._submissions.append(
                Submission(session_id, recipe, action, paper_id, tuple(rect)))
            if action == DeviceStep.LOSE_BEFORE_EXECUTION:
                raise SubmitUnknown('submission response lost')
            if action == DeviceStep.DEFER_EXECUTION:
                self._queue_request(session_id, (recipe, location))
                raise SubmitUnknown('submission response lost')
            cut = self._execute(session_id, recipe, **location)
            if action == DeviceStep.LOSE_AFTER_EXECUTION:
                raise SubmitUnknown('submission response lost')
            return cut

    def close_session(self, session_id: int) -> DeviceSession:
        with self._lock:
            if not self._close_available:
                raise DeviceUnavailable('session closure is temporarily unavailable')
            return self.press.close_session(session_id)

    def read_session(self, session_id: int) -> SessionSnapshot:
        with self._lock:
            snapshot = self.press.read_session(session_id)
            if not self._read_complete:
                return SessionSnapshot(snapshot.session, (), False)
            return snapshot

    def step(self, action: DeviceStep, session_id: int | None = None) -> PhysicalCut | None:
        action = DeviceStep(action)
        with self._lock:
            if action == DeviceStep.RELEASE_QUEUED:
                if session_id is None:
                    raise ValueError('release requires the queued session id')
                return self._release(session_id)
            if session_id is not None:
                raise ValueError('only release takes a session id')
            if action in (DeviceStep.BLOCK_CLOSE, DeviceStep.ALLOW_CLOSE):
                self._close_available = action == DeviceStep.ALLOW_CLOSE
            elif action in (DeviceStep.BLOCK_READ, DeviceStep.ALLOW_READ):
                self._read_complete = action == DeviceStep.ALLOW_READ
            else:
                raise ValueError(f'{action} is a submission step')
            return None

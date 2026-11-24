from contextlib import closing, contextmanager
from dataclasses import asdict
import argparse
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from archive.history import read_ticket
from capture.dispatch import advance
from capture.resume import resume
from check_project import main as original_suite
from device.press import Press
from device.simulator import DeviceStep, ScriptedDevice
from lab import open_archive
from review.close import IncompleteShift, close_shift
from review.lookup import lookup_report
from tests.support import fail_terminal_commit
from worklist.shifts import RegistrationConflict, register


def check(actual, expected):
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def rejects(error, operation, *args):
    try:
        operation(*args)
    except error:
        return
    raise AssertionError(f"expected {error.__name__} from {operation.__name__}")


@contextmanager
def station(case):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        yield root / "archive.sqlite3", Press(root / "press.sqlite3", serial_floor=case["serial_floor"])


def basic(cut):
    return [cut.serial, cut.grams, cut.source]


def located(cut):
    return [*basic(cut), cut.paper_id, list(cut.rect)]


def selfcheck(case):
    shift = case["shift_id"]
    with station(case) as (path, press):
        device = ScriptedDevice(press, readings=case["readings"], submissions=[DeviceStep.DEFER_EXECUTION])
        with closing(open_archive(path)) as db:
            register(db, shift, [(ticket, recipe) for ticket, recipe, _ in case["entries"]])
            device.step(DeviceStep.BLOCK_CLOSE)
            check(advance(db, shift, device).status, "pending")
            sid = device.submissions[-1].session_id
            check(resume(db, shift, device).status, "pending")
            check(press.cuts(), ())
            late = device.step(DeviceStep.RELEASE_QUEUED, sid)
            check(basic(late), case["expected"]["cuts"][0])
            device.step(DeviceStep.ALLOW_CLOSE)
            fail_terminal_commit(db)
            rejects(sqlite3.IntegrityError, resume, db, shift, device)
            check(read_ticket(db, shift, "A").terminal, None)
        with closing(open_archive(path)) as db:
            check(resume(db, shift, device).terminal.cut, late)
            advance(db, shift, device)
            advance(db, shift, device)
            check([basic(cut) for cut in press.cuts()], case["expected"]["cuts"])
            check(asdict(close_shift(db, shift).totals), case["expected"]["totals"])
    print("SHARED_PAPER_FIXTURE_SELF_CHECK", json.dumps(case["expected"]["cuts"], ensure_ascii=False))


def register_case(db, press, case):
    from worklist.shifts import register_shared

    shift, paper, entries = case["shift_id"], case["paper_id"], case["entries"]
    dimensions = (case["width"], case["height"])
    press.load_paper(paper, *dimensions)
    check(register_shared(db, shift, paper, *dimensions, entries), shift)
    check(register_shared(db, shift, paper, *dimensions, entries), shift)
    rejects(RegistrationConflict, register_shared, db, shift, paper, *dimensions, entries[::-1])


def deferred_branch(case, branch):
    from capture.resume import resume_shared

    shift, paper = case["shift_id"], case["paper_id"]
    with station(case) as (path, press):
        device = ScriptedDevice(press, readings=case["readings"], submissions=[DeviceStep.DEFER_EXECUTION])
        with closing(open_archive(path)) as db:
            register_case(db, press, case)
            rejects(ValueError, advance, db, shift, device)
            rejects(ValueError, resume, db, shift, device)
            check(resume_shared(db, shift, device).status, "pending")
            check(press.cuts(), ())
            sid = device.submissions[-1].session_id
            rejects(ValueError, press.submit_cut, sid, "strip", lambda: 99)
            Press(press.path).mark_damage(paper, case["damage"][branch])
            device.step(DeviceStep.RELEASE_QUEUED, sid)
            first = resume_shared(db, shift, device)
            check(first.ticket.ticket_id, "A")
            if branch == "blocked":
                check(first.status, "blocked")
                check(press.cuts(), ())
                check(read_ticket(db, shift, "A").terminal, None)
                check(read_ticket(db, shift, "B").status, "todo")
                check(resume_shared(db, shift, device).status, "blocked")
                check(press.cuts(), ())
                rejects(IncompleteShift, close_shift, db, shift)
                return {"status": first.status, "cut_count": len(press.cuts())}
            check(first.status, "closed")
            for name in ("B", "C"):
                view = resume_shared(db, shift, device)
                check((view.ticket.ticket_id, view.status), (name, "closed"))
            check(resume_shared(db, shift, device), None)
            saved = close_shift(db, shift)
            device_cuts = [located(cut) for cut in press.cuts()]
        Path(press.path).unlink()
        with closing(open_archive(path)) as db:
            report = lookup_report(db, saved.number)
            check(report, saved)
            rows = report.detail.rows
            check([row.ticket.ticket_id for row in rows], ["A", "B", "C"])
            cuts = [located(row.cut) for row in rows]
            check(cuts, device_cuts)
            check([row[:3] for row in cuts], case["expected"]["cuts"])
            check([row[3] for row in cuts], [paper] * 3)
            rects = [row[4] for row in cuts]
            if rects not in case["expected"]["continue_rects"]:
                raise AssertionError(f"unexpected placements: {rects!r}")
            totals = asdict(report.totals)
            check(totals, case["expected"]["totals"])
            return {"report": report.number, "tickets": [[r.ticket.ticket_id, *c] for r, c in zip(rows, cuts)],
                    "totals": totals}


def recover_after_cut(case):
    from capture.resume import resume_shared

    shift, paper = case["shift_id"], case["paper_id"]
    with station(case) as (path, press):
        with closing(open_archive(path)) as db:
            register_case(db, press, case)
            press.mark_damage(paper, case["damage"]["continue"])
            device = ScriptedDevice(press, readings=case["readings"], submissions=[DeviceStep.LOSE_AFTER_EXECUTION])
            check(resume_shared(db, shift, device).status, "pending")
            check(len(press.cuts()), 1)
            physical = located(press.cuts()[0])
            check(physical[:3], case["expected"]["cuts"][0])
            press.mark_damage(paper, case["damage"]["after_first"])
        press = Press(press.path)
        device = ScriptedDevice(press, readings=case["readings"][1:])
        with closing(open_archive(path)) as db:
            fail_terminal_commit(db)
            rejects(sqlite3.IntegrityError, resume_shared, db, shift, device)
        with closing(open_archive(path)) as db:
            check(read_ticket(db, shift, "A").status, "pending")
            check(read_ticket(db, shift, "A").terminal, None)
            check(read_ticket(db, shift, "B").status, "todo")
            closed = resume_shared(db, shift, device)
            check((closed.ticket.ticket_id, closed.status), ("A", "closed"))
            check(located(closed.terminal.cut), physical)
            blocked = resume_shared(db, shift, device)
            check((blocked.ticket.ticket_id, blocked.status), ("B", "blocked"))
            check([located(cut) for cut in press.cuts()], [physical])
            rejects(IncompleteShift, close_shift, db, shift)
            return {"actual_A": physical, "next_ticket": blocked.ticket.ticket_id,
                    "next_status": blocked.status}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("selfcheck", "verify", "demo"))
    mode = parser.parse_args().mode
    path = Path(__file__).with_name("samples") / "shared_paper.json"
    case = json.loads(path.read_text(encoding="utf-8"))
    if mode == "selfcheck":
        selfcheck(case)
        return 0
    if mode == "verify":
        if original_suite():
            return 1
        print(json.dumps(deferred_branch(case, "blocked"), ensure_ascii=False))
        print(json.dumps(recover_after_cut(case), ensure_ascii=False))
    print(json.dumps(deferred_branch(case, "continue"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

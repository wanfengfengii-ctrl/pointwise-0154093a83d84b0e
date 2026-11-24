from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.history import read_ticket
from capture.dispatch import advance
from capture.resume import resume
from device.press import Press
from device.simulator import DeviceStep, ScriptedDevice
from lab import open_archive
from review.close import close_shift
from review.lookup import lookup_report
from worklist.shifts import register


def cut_data(cut):
    return [cut.serial, cut.grams, cut.source]


def main() -> None:
    path = Path(__file__).resolve().parent / 'samples' / 'day.json'
    with path.open(encoding='utf-8') as stream:
        plan = json.load(stream)
    shift_id = plan['shift_id']
    expected = plan['expected']
    with TemporaryDirectory() as directory:
        root = Path(directory)
        archive_path = root / 'archive.sqlite3'
        press_path = root / 'press.sqlite3'
        press = Press(press_path, device_id=plan['device_id'], serial_floor=plan['serial_floor'])
        device = ScriptedDevice(press, readings=plan['scale_readings'], submissions=plan['submissions'])
        with closing(open_archive(archive_path)) as db:
            register(db, shift_id, plan['tickets'])
            device.step(DeviceStep.BLOCK_CLOSE)
            first = advance(db, shift_id, device)
            session = device.submissions[0].session_id
            opening = device.read_session(session)
            if opening.complete or opening.cuts:
                raise AssertionError(f'expected an empty open observation, got {opening!r}')
            waiting = resume(db, shift_id, device)
            check_waiting = [first.status, waiting.status, read_ticket(db, shift_id, 'B').status,
                             len(device.submissions), len(press.cuts())]
            device.step(DeviceStep.RELEASE_QUEUED, session)
        with closing(open_archive(archive_path)) as db:
            device.step(DeviceStep.ALLOW_CLOSE)
            resume(db, shift_id, device)
            advance(db, shift_id, device)
            saved = close_shift(db, shift_id)
        with closing(open_archive(archive_path)) as db:
            ticket_rows = []
            for ticket_id, _ in plan['tickets']:
                view = read_ticket(db, shift_id, ticket_id)
                data = [] if view.terminal is None else cut_data(view.terminal.cut)
                ticket_rows.append([ticket_id, view.status, *data])
            report = lookup_report(db, saved.number)
            if saved != report:
                raise AssertionError(f'fixed report changed after reopen: {saved!r} != {report!r}')
        actual = {
            'waiting': check_waiting,
            'device': [cut_data(cut) for cut in Press(press_path).cuts()],
            'tickets': ticket_rows,
            'report': {'number': report.number, 'order': [r.ticket.ticket_id for r in report.detail.rows],
                       **asdict(report.totals)},
        }
        if actual != expected:
            raise AssertionError(f'SAMPLE_CUT_ATTRIBUTION expected={expected!r} actual={actual!r}')

    business_parts = ('waiting', 'device', 'tickets')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'totals',
        'input': {key: value for key, value in plan.items() if key != 'expected'},
        'expected': {key: expected[key] for key in business_parts},
        'actual': {key: actual[key] for key in business_parts},
        'downstream_expected': expected['report'],
        'downstream_actual': actual['report'],
        'code_paths': ['capture/dispatch.py', 'capture/reconcile.py', 'archive/intake.py',
                       'review/totals.py', 'review/lookup.py'],
        'oracle': '手算日剧本从流水 40 起步。A 消耗 41 且缺测，B 消耗 42 得 23 克。固定报告为两次冲切、一条有效读值、合计 23 克。',
    }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()

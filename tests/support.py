from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from device.press import Press
from device.simulator import ScriptedDevice
from lab import open_archive
from worklist.shifts import register


class LabCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.archive_path = root / 'archive.sqlite3'
        self.device_path = root / 'press.sqlite3'
        self.db = open_archive(self.archive_path)
        self.addCleanup(lambda: self.db.close())
        self.press = Press(self.device_path, device_id='P1', serial_floor=40)
        self.shift = 'day-1'

    def worklist(self, entries=(('A', 'round'), ('B', 'round'))) -> str:
        return register(self.db, self.shift, entries)

    def script(self, readings, submissions=()) -> ScriptedDevice:
        return ScriptedDevice(self.press, readings=readings, submissions=submissions)

    def reopen(self) -> None:
        self.db.close()
        self.db = open_archive(self.archive_path)
        self.press = Press(self.device_path)


def fail_terminal_commit(db: sqlite3.Connection) -> None:
    # Only this connection sees the trigger. Its deferred constraint fails at COMMIT.
    db.executescript('''
        CREATE TEMP TABLE failure_parent (id INTEGER PRIMARY KEY);
        CREATE TEMP TABLE failure_child (
            parent_id INTEGER REFERENCES failure_parent(id) DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TEMP TRIGGER fail_terminal_commit AFTER INSERT ON main.terminals
        BEGIN
            INSERT INTO failure_child VALUES (1);
        END;
    ''')


def cut_values(cut):
    if cut is None:
        return None
    return [cut.serial, cut.grams, cut.source]


def ticket_cut(view):
    return cut_values(None if view.terminal is None else view.terminal.cut)


def report_values(report):
    return {
        'number': report.number,
        'tickets': [[row.ticket.ticket_id, cut_values(row.cut)] for row in report.detail.rows],
        'totals': asdict(report.totals),
    }


def scenario(case_id, inputs, expected, actual, downstream_expected, downstream_actual, code_paths, oracle):
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case_id, 'input': inputs, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': code_paths, 'oracle': oracle,
    }, ensure_ascii=False, sort_keys=True), flush=True)

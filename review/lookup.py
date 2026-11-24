import sqlite3

from review.reports import ShiftReport, find_report_by_number


class ReportNotFound(LookupError):
    pass


def lookup_report(db: sqlite3.Connection, number: str) -> ShiftReport:
    saved = find_report_by_number(db, number)
    if saved is None:
        raise ReportNotFound(number)
    return saved

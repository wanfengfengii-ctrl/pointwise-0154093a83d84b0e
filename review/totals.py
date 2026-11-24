from dataclasses import dataclass

from review.detail import ShiftDetail


@dataclass(frozen=True)
class ShiftTotals:
    cuts: int
    valid_readings: int
    grams: int


def calculate_totals(detail: ShiftDetail) -> ShiftTotals:
    cuts = 0
    valid_readings = 0
    grams = 0
    for row in detail.rows:
        if row.cut is None:
            continue
        cuts += 1
        if row.cut.grams is not None:
            valid_readings += 1
            grams += row.cut.grams
    return ShiftTotals(cuts, valid_readings, grams)

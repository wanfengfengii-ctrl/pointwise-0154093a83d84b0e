from dataclasses import dataclass


def validate_scale_reading(grams: int | None) -> None:
    if grams is not None and type(grams) is not int:
        raise ValueError(f'expected integer grams or a missing reading, got {grams!r}')


@dataclass(frozen=True)
class ScaleReading:
    grams: int | None

    def __post_init__(self) -> None:
        validate_scale_reading(self.grams)


def measure(grams: int | None) -> ScaleReading:
    return ScaleReading(grams)


def encode_grams(grams: int | None) -> str | None:
    # Text preserves Python integer precision in SQLite and in report snapshots.
    return None if grams is None else format(grams, 'x')


def decode_grams(stored: str | None) -> int | None:
    return None if stored is None else int(stored, 16)

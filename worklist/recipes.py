from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Recipe:
    code: str
    die: str


RECIPES = MappingProxyType({
    'round': Recipe('round', 'round-25'),
    'strip': Recipe('strip', 'strip-10'),
})


def resolve_recipe(code: str) -> Recipe:
    if not isinstance(code, str) or code not in RECIPES:
        raise ValueError(f'unknown paper recipe {code!r}')
    return RECIPES[code]

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType


CLASS_KEY_ALIASES = MappingProxyType(
    {
        "unit": "other",
        "terminal_unit": "other",
        "card": "other",
        "device": "other",
        "equipment": "other",
        "breaker": "other",
    }
)


def _normalized_key(class_key: str) -> str:
    return class_key.strip().casefold().replace("-", "_").replace(" ", "_")


def canonical_symbol_class_key(class_key: str, known_keys: Iterable[str]) -> str:
    requested = class_key.strip()
    normalized = _normalized_key(requested)
    alias = CLASS_KEY_ALIASES.get(normalized)
    if alias is not None:
        return alias

    known = {_normalized_key(key): key for key in known_keys}
    orientation_bases = []
    if normalized.startswith("horizontal_"):
        orientation_bases.append(normalized.removeprefix("horizontal_"))
    if normalized.endswith("_horizontal"):
        orientation_bases.append(normalized.removesuffix("_horizontal"))
    for base in orientation_bases:
        alias = CLASS_KEY_ALIASES.get(base)
        if alias is not None:
            return alias
        if base in known:
            return known[base]

    return known.get(normalized, requested)

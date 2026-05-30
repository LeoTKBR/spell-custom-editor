"""Constantes compartilhadas (sem dependencias externas)."""

from __future__ import annotations

SPELL_SHEETS = (
    ("images/spells/spell-icons-32x32.png", 32),
    ("images/spells/spell-icons-20x20.png", 20),
)

CLIENT_SPELL_PATHS = {
    "embedded": (b":/spells/spells.json", b":/spells/spells-previews.json"),
    "external": (b"./spells/spells.json", b"./spells/spells-previews.json"),
    "bundled": (b":/custom/spells.json", b":/custom/spells-previews.json"),
}

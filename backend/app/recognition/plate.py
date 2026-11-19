"""Mexican licence-plate normalization.

The prototype accepted any OCR string of 5-10 characters as a plate. That
is the single largest source of false readings: bus liveries, stickers and
bumper text all pass that test.

This module does three things instead:

1. Canonicalises the raw OCR text (case, separators, look-alike glyphs).
2. Matches it against the plate layouts actually issued in Mexico.
3. Uses the matched layout to *repair* character confusions, because the
   layout tells us which positions must be letters and which must be digits
   (an "O" in a digit slot is a zero, a "0" in a letter slot is an O).

The output is a `PlateReading`, never a bare string, so callers always have
the confidence and the layout that justified the result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Glyph pairs OCR engines confuse. Mapping is direction-aware: when a slot
# must hold a digit we fold letters into digits, and vice versa.
_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                           "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7", "A": "4"})
_TO_LETTER = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S",
                            "8": "B", "6": "G", "4": "A"})

_STRIP = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class PlateLayout:
    """A plate layout: `pattern` uses A for letter slots and 9 for digits."""

    name: str
    pattern: str
    display: str  # how to group it for humans, using '-' separators

    @property
    def length(self) -> int:
        return len(self.pattern)

    def matches(self, text: str) -> bool:
        if len(text) != self.length:
            return False
        return all(
            (c.isalpha() if slot == "A" else c.isdigit())
            for c, slot in zip(text, self.pattern)
        )

    def coerce(self, text: str) -> Optional[str]:
        """Force `text` into this layout using look-alike substitutions."""
        if len(text) != self.length:
            return None
        out = []
        for c, slot in zip(text, self.pattern):
            if slot == "A":
                c = c.translate(_TO_LETTER)
                if not c.isalpha():
                    return None
            else:
                c = c.translate(_TO_DIGIT)
                if not c.isdigit():
                    return None
            out.append(c)
        return "".join(out)

    def format(self, text: str) -> str:
        """Insert the conventional separators for display."""
        out, i = [], 0
        for ch in self.display:
            if ch == "-":
                out.append("-")
            else:
                out.append(text[i])
                i += 1
        return "".join(out)


# Ordered by how commonly they appear on Mexican roads. The first match wins,
# so more specific / more common layouts come first.
LAYOUTS: tuple[PlateLayout, ...] = (
    # Private vehicles, the dominant layout: GPT-45-67
    PlateLayout("particular", "AAA9999", "XXX-XX-XX"),
    # Newer private series issued by several states: ABC-123-D
    PlateLayout("particular_nueva", "AAA999A", "XXX-XXX-X"),
    # Older private series still in circulation: 123-ABC
    PlateLayout("particular_antigua", "999AAA", "XXX-XXX"),
    # Public transport / taxi: 12-AB-34
    PlateLayout("publico", "99AA99", "XX-XX-XX"),
    # Motorcycles: 123-AB or 1234-A
    PlateLayout("motocicleta", "999AA", "XXX-XX"),
    PlateLayout("motocicleta_alt", "9999A", "XXXX-X"),
    # Federal cargo transport: 123-AB-4
    PlateLayout("carga_federal", "999AA9", "XXX-XX-X"),
)


@dataclass
class PlateReading:
    """The result of interpreting one OCR string."""

    raw: str
    normalized: Optional[str]
    display: Optional[str]
    layout: Optional[str]
    confidence: float
    is_valid: bool
    # Every interpretation considered, best first. Manual review shows these.
    candidates: list[dict] = field(default_factory=list)

    @property
    def plate(self) -> Optional[str]:
        return self.normalized


def canonicalize(raw: str) -> str:
    """Upper-case and drop everything that cannot be part of a plate."""
    return _STRIP.sub("", (raw or "").upper())


def interpret(raw: str, ocr_confidence: float = 0.0) -> PlateReading:
    """Interpret a single OCR string as a plate.

    Returns a reading even when nothing matches, so the caller can record
    the failed attempt rather than silently dropping it.
    """
    cleaned = canonicalize(raw)
    if not cleaned:
        return PlateReading(raw=raw, normalized=None, display=None, layout=None,
                            confidence=0.0, is_valid=False)

    candidates: list[dict] = []

    # Pass 1 — exact layout match. Highest trust: no repair needed.
    for layout in LAYOUTS:
        if layout.matches(cleaned):
            candidates.append({
                "plate": cleaned,
                "display": layout.format(cleaned),
                "layout": layout.name,
                "repaired": False,
                "score": round(min(1.0, ocr_confidence * 1.0 + 0.15), 4),
            })

    # Pass 2 — layout-guided repair of look-alike glyphs.
    if not candidates:
        for layout in LAYOUTS:
            coerced = layout.coerce(cleaned)
            if coerced and layout.matches(coerced):
                edits = sum(1 for a, b in zip(cleaned, coerced) if a != b)
                candidates.append({
                    "plate": coerced,
                    "display": layout.format(coerced),
                    "layout": layout.name,
                    "repaired": True,
                    "edits": edits,
                    # Each repaired glyph costs confidence: a plate we had to
                    # rewrite twice is not as trustworthy as a clean read.
                    "score": round(max(0.0, ocr_confidence - 0.12 * edits), 4),
                })

    candidates.sort(key=lambda c: c["score"], reverse=True)

    if not candidates:
        return PlateReading(raw=raw, normalized=None, display=None, layout=None,
                            confidence=0.0, is_valid=False, candidates=[])

    best = candidates[0]
    return PlateReading(
        raw=raw,
        normalized=best["plate"],
        display=best["display"],
        layout=best["layout"],
        confidence=best["score"],
        is_valid=True,
        candidates=candidates[:5],
    )


def best_reading(ocr_results: list[tuple[str, float]]) -> PlateReading:
    """Pick the most plausible plate among several OCR fragments.

    A frame usually yields several text boxes (plate, dealer sticker, model
    badge). We interpret each one and keep the best-scoring valid plate,
    falling back to the highest-confidence raw fragment so the guard still
    sees what the camera read.
    """
    readings = [interpret(text, conf) for text, conf in ocr_results]
    valid = [r for r in readings if r.is_valid]
    if valid:
        best = max(valid, key=lambda r: r.confidence)
        # Merge the rejected interpretations in for review context.
        others = [c for r in valid if r is not best for c in r.candidates]
        best.candidates = (best.candidates + others)[:8]
        return best
    if readings:
        return max(readings, key=lambda r: r.confidence)
    return PlateReading(raw="", normalized=None, display=None, layout=None,
                        confidence=0.0, is_valid=False)


def normalize_for_storage(value: str) -> str:
    """Normalize a human-entered plate for lookup/storage.

    Used when a guard types a plate or an admin registers a vehicle, so the
    manual path and the OCR path agree on the key.
    """
    reading = interpret(value, ocr_confidence=1.0)
    return reading.normalized or canonicalize(value)


def format_for_display(plate: str) -> str:
    for layout in LAYOUTS:
        if layout.matches(plate):
            return layout.format(plate)
    return plate

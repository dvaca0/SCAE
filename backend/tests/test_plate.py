"""Plate normalization decides what the whole system matches on."""
import pytest

from app.recognition.plate import (
    best_reading, canonicalize, format_for_display, interpret,
    normalize_for_storage,
)


@pytest.mark.parametrize("raw,expected", [
    ("GPT-45-67", "GPT4567"),
    ("gpt4567", "GPT4567"),
    ("  GPT 45 67  ", "GPT4567"),
    ("ABC123D", "ABC123D"),
    ("123-ABC", "123ABC"),
    ("12-AB-34", "12AB34"),
])
def test_valid_layouts_are_recognized(raw, expected):
    r = interpret(raw, 0.9)
    assert r.is_valid
    assert r.normalized == expected


@pytest.mark.parametrize("junk", [
    "{MABRIS'",   # bus livery text the prototype accepted as a plate
    "MERCEDES",
    "TURBO",
    "",
    "XX",
    "1",
    "THISISWAYTOOLONG",
])
def test_non_plate_text_is_rejected(junk):
    assert not interpret(junk, 0.9).is_valid


def test_layout_guided_repair_of_ocr_confusions():
    """G->6 and S->5 land in slots the layout says must be letters."""
    r = interpret("6PT4S67", 0.7)
    assert r.is_valid
    assert r.normalized == "GPT4567"
    assert r.candidates[0]["repaired"] is True


def test_repair_costs_confidence():
    clean = interpret("GPT4567", 0.7)
    repaired = interpret("6PT4S67", 0.7)
    assert repaired.confidence < clean.confidence


def test_display_formatting_groups_the_plate():
    assert interpret("GPT4567", 0.9).display == "GPT-45-67"
    assert format_for_display("12AB34") == "12-AB-34"


def test_best_reading_picks_the_plate_out_of_frame_noise():
    r = best_reading([("MERCEDES", 0.95), ("GPT4567", 0.62), ("TURBO", 0.88)])
    assert r.normalized == "GPT4567"


def test_best_reading_survives_a_frame_with_no_plate():
    r = best_reading([("MERCEDES", 0.95), ("TURBO", 0.88)])
    assert not r.is_valid
    assert r.normalized is None


def test_manual_entry_and_ocr_agree_on_the_key():
    assert normalize_for_storage("gpt-45-67") == interpret("GPT4567", 1.0).normalized


def test_canonicalize_strips_separators_and_case():
    assert canonicalize(" gpt-45-67 ") == "GPT4567"

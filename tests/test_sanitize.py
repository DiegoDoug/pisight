"""Sanitization of attacker-controlled text: SSIDs, device names and alert bodies."""

from __future__ import annotations

import pytest

from pisight.sanitize import ELLIPSIS, mask_mac, sanitize_for_log, sanitize_text, truncate


def test_plain_text_passes_through() -> None:
    assert sanitize_text("harbor-guest") == "harbor-guest"


@pytest.mark.parametrize("whitespace", ["\n", "\r", "\t", "\v", "\f", "\r\n"])
def test_newlines_and_tabs_collapse_to_spaces(whitespace: str) -> None:
    """A crafted SSID must not be able to forge extra lines in a log file."""
    result = sanitize_text(f"before{whitespace}after")
    assert result == "before after"
    assert "\n" not in result
    assert "\r" not in result


def test_control_characters_are_replaced() -> None:
    result = sanitize_text("net\x00\x07\x1bwork")
    assert "\x00" not in result
    assert "\x1b" not in result
    assert result.startswith("net")
    assert result.endswith("work")


def test_ansi_escape_sequence_is_defused() -> None:
    """An SSID carrying an ANSI sequence must not colour or clear a terminal."""
    result = sanitize_text("\x1b[31mALERT\x1b[0m")
    assert "\x1b" not in result
    assert "ALERT" in result


def test_bidi_overrides_are_replaced() -> None:
    """Right-to-left overrides can visually reverse a name; they are stripped."""
    result = sanitize_text("safe‮reversed")
    assert "‮" not in result


def test_runs_of_whitespace_collapse() -> None:
    assert sanitize_text("  a   b  ") == "a b"


@pytest.mark.parametrize("value", [None, True, False, object(), [1, 2]])
def test_non_strings_become_empty(value: object) -> None:
    assert sanitize_text(value) == ""


def test_numbers_are_rendered() -> None:
    assert sanitize_text(42) == "42"
    assert sanitize_text(4.5) == "4.5"


def test_max_length_truncates_with_an_ellipsis() -> None:
    result = sanitize_text("abcdefghij", max_length=5)
    assert len(result) == 5
    assert result.endswith(ELLIPSIS)


def test_truncate_edge_cases() -> None:
    assert truncate("abc", 0) == ""
    assert truncate("abc", 1) == ELLIPSIS
    assert truncate("abc", 3) == "abc"
    assert truncate("abc", 10) == "abc"


def test_sanitize_for_log_is_single_line_and_bounded() -> None:
    hostile = "name\nlevel=CRITICAL fake log line\n" + "x" * 500
    result = sanitize_for_log(hostile)
    assert "\n" not in result
    assert len(result) <= 200


# --- MAC masking -----------------------------------------------------------------------


def test_mac_is_masked_to_the_last_four_hex_characters() -> None:
    assert mask_mac("02:00:5E:11:22:33") == "··2233"


def test_masked_mac_never_contains_the_leading_octets() -> None:
    masked = mask_mac("AA:BB:CC:DD:EE:FF")
    for octet in ("AA", "BB", "CC", "DD"):
        assert octet not in masked


def test_full_mac_shown_only_on_explicit_opt_in() -> None:
    assert mask_mac("02:00:5e:11:22:33", show_full=True) == "02:00:5E:11:22:33"


@pytest.mark.parametrize("value", [None, "", "xyz", "1:2"])
def test_unusable_macs_mask_to_a_placeholder(value: str | None) -> None:
    assert mask_mac(value) == "??"


def test_mask_mac_handles_a_dashed_format() -> None:
    assert mask_mac("02-00-5E-11-22-33") == "··2233"

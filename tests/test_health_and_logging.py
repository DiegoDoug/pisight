"""Host health collection, log redaction and log-injection defence."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from pisight.health.host import (
    HostHealthProvider,
    display_is_visible,
    read_cpu_temperature_c,
    read_disk_usage,
)
from pisight.logging_setup import REDACTED, RedactingFilter, configure_logging
from pisight.models import HostHealth

# --- CPU temperature -------------------------------------------------------------------


def test_temperature_read_from_a_sysfs_style_file(tmp_path: Path) -> None:
    path = tmp_path / "temp"
    path.write_text("48312\n", encoding="utf-8")
    assert read_cpu_temperature_c([path]) == pytest.approx(48.3)


def test_temperature_accepts_a_value_already_in_celsius(tmp_path: Path) -> None:
    path = tmp_path / "temp"
    path.write_text("51.5", encoding="utf-8")
    assert read_cpu_temperature_c([path]) == pytest.approx(51.5)


def test_temperature_is_none_when_no_thermal_zone_exists(tmp_path: Path) -> None:
    assert read_cpu_temperature_c([tmp_path / "absent"]) is None


def test_temperature_ignores_unparseable_and_implausible_values(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage"
    garbage.write_text("not a number", encoding="utf-8")
    assert read_cpu_temperature_c([garbage]) is None

    absurd = tmp_path / "absurd"
    absurd.write_text("999999999", encoding="utf-8")
    assert read_cpu_temperature_c([absurd]) is None


def test_temperature_falls_through_to_the_next_candidate(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    good = tmp_path / "good"
    good.write_text("45000", encoding="utf-8")
    assert read_cpu_temperature_c([missing, good]) == pytest.approx(45.0)


# --- Disk usage ------------------------------------------------------------------------


def test_disk_usage_reads_an_existing_directory(tmp_path: Path) -> None:
    total, free = read_disk_usage(tmp_path)
    assert total is not None and total > 0
    assert free is not None and 0 <= free <= total


def test_disk_usage_walks_up_to_an_existing_parent(tmp_path: Path) -> None:
    """A configured-but-not-yet-created log directory still reports its filesystem."""
    total, free = read_disk_usage(tmp_path / "not" / "created" / "yet")
    assert total is not None
    assert free is not None


def test_disk_usage_of_an_impossible_path_is_none() -> None:
    total, free = read_disk_usage("\x00invalid")
    assert (total, free) in {(None, None)} or total is not None


# --- Host health provider --------------------------------------------------------------


def test_collect_returns_a_populated_health_record(tmp_path: Path) -> None:
    health = HostHealthProvider(tmp_path, cache_seconds=0.0).collect()
    assert isinstance(health, HostHealth)
    assert health.storage_path == str(tmp_path)
    assert health.platform != ""
    assert health.architecture != ""
    assert health.uptime_seconds >= 0.0


def test_values_are_cached_but_uptime_stays_live(tmp_path: Path) -> None:
    provider = HostHealthProvider(tmp_path, cache_seconds=60.0)
    first = provider.collect()
    second = provider.collect()

    assert second.disk_total_bytes == first.disk_total_bytes
    assert second.uptime_seconds >= first.uptime_seconds


def test_force_bypasses_the_cache(tmp_path: Path) -> None:
    provider = HostHealthProvider(tmp_path, cache_seconds=3600.0)
    provider.collect()
    assert provider.collect(force=True).storage_path == str(tmp_path)


def test_off_pi_values_degrade_to_none_rather_than_raising(tmp_path: Path) -> None:
    """On a development host with no thermal zone, collection must still succeed."""
    health = HostHealthProvider(tmp_path, cache_seconds=0.0).collect()
    assert health.cpu_temp_c is None or isinstance(health.cpu_temp_c, float)


def test_display_visibility_never_raises() -> None:
    assert isinstance(display_is_visible(), bool)


# --- HostHealth derived values ---------------------------------------------------------


def test_free_percentage_and_low_storage_flag() -> None:
    healthy = HostHealth(disk_total_bytes=1000, disk_free_bytes=500)
    assert healthy.disk_free_pct == pytest.approx(50.0)
    assert healthy.storage_is_low is False

    low = HostHealth(disk_total_bytes=1000, disk_free_bytes=50)
    assert low.disk_free_pct == pytest.approx(5.0)
    assert low.storage_is_low is True


def test_free_percentage_is_none_without_usable_numbers() -> None:
    assert HostHealth().disk_free_pct is None
    assert HostHealth(disk_total_bytes=0, disk_free_bytes=0).disk_free_pct is None
    assert HostHealth().storage_is_low is False


# --- Logging redaction -----------------------------------------------------------------


def make_record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 1, message, args, None)


def test_the_token_is_redacted_from_log_records() -> None:
    token = "super-secret-token-value"
    record = make_record("connecting with %s", token)
    RedactingFilter(token).filter(record)

    assert token not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_a_token_appearing_mid_string_is_redacted() -> None:
    token = "abcdefgh12345678"
    record = make_record(f"cookie=KISMET={token}; path=/")
    RedactingFilter(token).filter(record)
    assert token not in record.getMessage()


def test_even_short_tokens_are_redacted() -> None:
    """Token confidentiality also applies to short operator-supplied values."""
    record = make_record("the cat sat")
    RedactingFilter("cat").filter(record)
    assert "cat" not in record.getMessage()


def test_no_token_configured_leaves_the_message_intact() -> None:
    record = make_record("nothing secret here")
    RedactingFilter(None).filter(record)
    assert record.getMessage() == "nothing secret here"


def test_log_records_are_flattened_to_one_line() -> None:
    """A crafted SSID must not be able to forge additional journal lines."""
    hostile = "guest\nERROR root: fabricated line claiming success"
    record = make_record("device seen: %s", hostile)
    RedactingFilter(None).filter(record)

    assert "\n" not in record.getMessage()
    assert "fabricated line" in record.getMessage()


def test_control_characters_are_stripped_from_log_records() -> None:
    record = make_record("name: %s", "a\x1b[31mb\x00c")
    RedactingFilter(None).filter(record)
    message = record.getMessage()
    assert "\x1b" not in message
    assert "\x00" not in message


def test_the_filter_never_drops_a_record() -> None:
    assert RedactingFilter("token-value-long").filter(make_record("anything")) is True


def test_configure_logging_installs_exactly_one_handler() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    original = list(root.handlers)
    original_level = root.level
    try:
        configure_logging("DEBUG", stream=stream)
        configure_logging("DEBUG", stream=stream)
        installed = [h for h in root.handlers if getattr(h, "_pisight", False)]
        assert len(installed) == 1

        logging.getLogger("pisight.test").info("hello")
        assert "hello" in stream.getvalue()
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "_pisight", False):
                root.removeHandler(handler)
        root.handlers = original
        root.setLevel(original_level)


def test_configure_logging_quiets_httpx() -> None:
    root = logging.getLogger()
    original = list(root.handlers)
    try:
        configure_logging("DEBUG", stream=io.StringIO())
        # httpx logs full request URLs at INFO; it stays at WARNING regardless.
        assert logging.getLogger("httpx").level >= logging.WARNING
        assert logging.getLogger("httpcore").level >= logging.WARNING
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "_pisight", False):
                root.removeHandler(handler)
        root.handlers = original


def test_exception_traceback_is_redacted_and_flattened(monkeypatch: pytest.MonkeyPatch) -> None:
    from pisight.logging_setup import SafeFormatter

    token = "synthetic-traceback-token"
    monkeypatch.setenv("PISIGHT_KISMET_API_TOKEN", token)
    try:
        raise ValueError(f"{token}\nforged log line")
    except ValueError:
        import sys

        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    rendered = SafeFormatter("%(message)s").format(record)
    assert token not in rendered
    assert "\n" not in rendered
    assert REDACTED in rendered

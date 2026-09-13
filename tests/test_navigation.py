"""Navigation, layout geometry and touch debounce -- all testable without a video driver."""

from __future__ import annotations

from itertools import pairwise

import pytest

from pisight.ui.layout import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CONTENT_HEIGHT,
    CONTENT_RECT,
    CONTENT_Y,
    MIN_TOUCH_SIZE,
    NAV_BUTTON_COUNT,
    NAV_HEIGHT,
    NAV_RECT,
    NAV_Y,
    STATUS_HEIGHT,
    STATUS_RECT,
    Rect,
    nav_button_index_at,
    nav_button_rect,
    split_columns,
    split_rows,
)
from pisight.ui.navigation import InputRouter, Navigator, PointerEvent, ScreenId

# --- Layout geometry -------------------------------------------------------------------


def test_canvas_is_exactly_320x240() -> None:
    assert (CANVAS_WIDTH, CANVAS_HEIGHT) == (320, 240)


def test_the_three_bands_match_the_specification_and_tile_the_canvas() -> None:
    assert STATUS_HEIGHT == 24
    assert CONTENT_HEIGHT == 176
    assert NAV_HEIGHT == 40
    assert STATUS_HEIGHT + CONTENT_HEIGHT + NAV_HEIGHT == CANVAS_HEIGHT

    assert STATUS_RECT.bottom == CONTENT_RECT.y == CONTENT_Y
    assert CONTENT_RECT.bottom == NAV_RECT.y == NAV_Y
    assert NAV_RECT.bottom == CANVAS_HEIGHT


def test_every_band_spans_the_full_width() -> None:
    for rect in (STATUS_RECT, CONTENT_RECT, NAV_RECT):
        assert rect.x == 0
        assert rect.width == CANVAS_WIDTH


def test_nav_buttons_tile_the_width_with_no_gaps() -> None:
    buttons = [nav_button_rect(index) for index in range(NAV_BUTTON_COUNT)]
    assert buttons[0].x == 0
    assert buttons[-1].right == CANVAS_WIDTH
    for left, right in pairwise(buttons):
        assert left.right == right.x


def test_every_nav_button_meets_the_minimum_touch_target() -> None:
    for index in range(NAV_BUTTON_COUNT):
        button = nav_button_rect(index)
        assert button.width >= MIN_TOUCH_SIZE
        assert button.height >= MIN_TOUCH_SIZE


def test_nav_button_index_out_of_range_raises() -> None:
    with pytest.raises(IndexError):
        nav_button_rect(NAV_BUTTON_COUNT)
    with pytest.raises(IndexError):
        nav_button_rect(-1)


@pytest.mark.parametrize("index", range(NAV_BUTTON_COUNT))
def test_hit_testing_finds_each_button_centre(index: int) -> None:
    button = nav_button_rect(index)
    assert nav_button_index_at(button.center_x, button.center_y) == index


def test_hit_testing_ignores_the_status_and_content_bands() -> None:
    assert nav_button_index_at(160, 5) is None  # status bar
    assert nav_button_index_at(160, 100) is None  # content
    assert nav_button_index_at(160, NAV_Y - 1) is None  # one pixel above the nav bar


def test_hit_testing_ignores_points_outside_the_canvas() -> None:
    assert nav_button_index_at(-5, NAV_Y + 5) is None
    assert nav_button_index_at(CANVAS_WIDTH + 5, NAV_Y + 5) is None
    assert nav_button_index_at(160, CANVAS_HEIGHT + 5) is None


def test_rect_helpers() -> None:
    rect = Rect(10, 20, 100, 50)
    assert rect.right == 110
    assert rect.bottom == 70
    assert rect.center_x == 60
    assert rect.center_y == 45
    assert rect.contains(10, 20) is True
    assert rect.contains(110, 70) is False
    assert rect.as_tuple() == (10, 20, 100, 50)


def test_rect_inset_never_goes_negative() -> None:
    inset = Rect(0, 0, 4, 4).inset(10)
    assert inset.width == 0
    assert inset.height == 0


def test_split_columns_fills_the_rectangle_exactly() -> None:
    rect = Rect(0, 0, 313, 40)
    columns = split_columns(rect, 4, gap=3)
    assert len(columns) == 4
    assert columns[0].x == rect.x
    assert columns[-1].right == rect.right
    for left, right in pairwise(columns):
        assert right.x == left.right + 3


def test_split_rows_fills_the_rectangle_exactly() -> None:
    rect = Rect(0, 0, 100, 177)
    rows = split_rows(rect, 5, gap=2)
    assert rows[0].y == rect.y
    assert rows[-1].bottom == rect.bottom


@pytest.mark.parametrize("count", [0, -1])
def test_splitting_rejects_non_positive_counts(count: int) -> None:
    with pytest.raises(ValueError):
        split_columns(Rect(0, 0, 10, 10), count)
    with pytest.raises(ValueError):
        split_rows(Rect(0, 0, 10, 10), count)


# --- Navigator -------------------------------------------------------------------------


def test_there_are_exactly_four_screens() -> None:
    assert len(ScreenId) == NAV_BUTTON_COUNT == 4


def test_every_screen_has_a_label_and_a_title() -> None:
    for screen in ScreenId:
        assert screen.label
        assert screen.title
        assert len(screen.label) <= 5


def test_navigator_starts_on_overview() -> None:
    assert Navigator().current is ScreenId.OVERVIEW


def test_select_changes_the_screen_and_reports_the_change() -> None:
    navigator = Navigator()
    assert navigator.select(ScreenId.DEVICES) is True
    assert navigator.current is ScreenId.DEVICES
    # Selecting the active screen is not a change.
    assert navigator.select(ScreenId.DEVICES) is False


def test_select_index_ignores_out_of_range_values() -> None:
    navigator = Navigator()
    assert navigator.select_index(99) is False
    assert navigator.select_index(-1) is False
    assert navigator.current is ScreenId.OVERVIEW


def test_next_and_previous_wrap_around() -> None:
    navigator = Navigator()
    for expected in (ScreenId.CHANNELS, ScreenId.DEVICES, ScreenId.ALERTS, ScreenId.OVERVIEW):
        navigator.next()
        assert navigator.current is expected

    for expected in (ScreenId.ALERTS, ScreenId.DEVICES, ScreenId.CHANNELS, ScreenId.OVERVIEW):
        navigator.previous()
        assert navigator.current is expected


# --- Input routing and debounce --------------------------------------------------------


def centre_of(screen: ScreenId) -> PointerEvent:
    button = nav_button_rect(int(screen))
    return PointerEvent(button.center_x, button.center_y)


def test_a_tap_on_a_nav_button_selects_that_screen() -> None:
    navigator = Navigator()
    router = InputRouter(navigator)

    assert router.handle_pointer(centre_of(ScreenId.ALERTS), now_ms=1000) is True
    assert navigator.current is ScreenId.ALERTS


def test_a_tap_in_the_content_area_changes_nothing() -> None:
    navigator = Navigator()
    router = InputRouter(navigator)

    assert router.handle_pointer(PointerEvent(160, 100), now_ms=1000) is False
    assert navigator.current is ScreenId.OVERVIEW


def test_bounced_presses_within_the_debounce_window_are_ignored() -> None:
    """One physical press on a resistive panel must not skip two screens."""
    navigator = Navigator()
    router = InputRouter(navigator, debounce_ms=220)

    router.handle_pointer(centre_of(ScreenId.CHANNELS), now_ms=1000)
    # Contact bounce a few milliseconds later.
    router.handle_pointer(centre_of(ScreenId.DEVICES), now_ms=1005)
    router.handle_pointer(centre_of(ScreenId.ALERTS), now_ms=1050)

    assert navigator.current is ScreenId.CHANNELS


def test_the_synthesised_mouse_event_that_follows_a_touch_is_swallowed() -> None:
    """SDL emits a mouse event for every touch; handling both would double every tap."""
    navigator = Navigator()
    router = InputRouter(navigator, debounce_ms=220)

    router.handle_pointer(PointerEvent(*_centre(ScreenId.DEVICES), source="touch"), now_ms=500)
    router.handle_pointer(PointerEvent(*_centre(ScreenId.DEVICES), source="mouse"), now_ms=502)

    assert navigator.current is ScreenId.DEVICES


def test_a_press_after_the_debounce_window_is_accepted() -> None:
    navigator = Navigator()
    router = InputRouter(navigator, debounce_ms=220)

    router.handle_pointer(centre_of(ScreenId.CHANNELS), now_ms=1000)
    router.handle_pointer(centre_of(ScreenId.ALERTS), now_ms=1000 + 220)

    assert navigator.current is ScreenId.ALERTS


def test_a_swallowed_press_does_not_extend_the_lockout() -> None:
    """Holding a finger down must not postpone the next legitimate press."""
    navigator = Navigator()
    router = InputRouter(navigator, debounce_ms=200)

    router.handle_pointer(centre_of(ScreenId.CHANNELS), now_ms=0)
    for bounce in range(10, 190, 10):
        router.handle_pointer(centre_of(ScreenId.DEVICES), now_ms=bounce)

    assert router.accepts(now_ms=200) is True
    router.handle_pointer(centre_of(ScreenId.ALERTS), now_ms=200)
    assert navigator.current is ScreenId.ALERTS


def test_reset_clears_the_debounce_timer() -> None:
    router = InputRouter(Navigator(), debounce_ms=500)
    router.handle_pointer(centre_of(ScreenId.CHANNELS), now_ms=1000)
    assert router.accepts(now_ms=1100) is False
    router.reset()
    assert router.accepts(now_ms=1100) is True


def test_debounce_must_not_be_negative() -> None:
    with pytest.raises(ValueError):
        InputRouter(Navigator(), debounce_ms=-1)


def _centre(screen: ScreenId) -> tuple[int, int]:
    button = nav_button_rect(int(screen))
    return (button.center_x, button.center_y)

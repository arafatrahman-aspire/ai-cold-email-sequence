from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.scheduling import (
    advance_business_days,
    build_schedule,
    clamp_into_window,
    next_business_day_start,
    within_business_hours,
)
from app.settings_store import BusinessHours

HOURS = BusinessHours(start_hour=9, end_hour=17, weekdays=(0, 1, 2, 3, 4))


def test_advance_skips_weekends():
    friday = datetime(2026, 9, 25).date()  # a Friday
    assert advance_business_days(friday, 1, HOURS).isoformat() == "2026-09-28"  # Monday
    assert advance_business_days(friday, 3, HOURS).isoformat() == "2026-09-30"


def test_advance_zero_rolls_weekend_forward():
    saturday = datetime(2026, 9, 26).date()
    assert advance_business_days(saturday, 0, HOURS).isoformat() == "2026-09-28"


def test_clamp_before_window():
    tz = ZoneInfo("Europe/London")
    dt = datetime(2026, 9, 22, 6, 30, tzinfo=tz)  # Tuesday 06:30
    assert clamp_into_window(dt, HOURS).hour == 9


def test_clamp_after_window_rolls_to_next_day():
    tz = ZoneInfo("Europe/London")
    dt = datetime(2026, 9, 22, 19, 0, tzinfo=tz)  # Tuesday 19:00
    out = clamp_into_window(dt, HOURS)
    assert out.day == 23 and out.hour == 9


def test_clamp_inside_window_is_unchanged():
    tz = ZoneInfo("Europe/London")
    dt = datetime(2026, 9, 22, 11, 15, tzinfo=tz)
    assert clamp_into_window(dt, HOURS) == dt


def test_schedule_is_strictly_increasing_and_in_hours():
    anchor = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
    tz_name = "America/New_York"
    schedule = build_schedule(anchor, tz_name, [0, 3, 7, 12], HOURS)

    assert len(schedule) == 4
    assert all(b > a for a, b in zip(schedule, schedule[1:]))
    for due in schedule:
        local = due.astimezone(ZoneInfo(tz_name))
        assert local.weekday() in HOURS.weekdays
        assert 9 <= local.hour < 17


def test_within_business_hours_respects_lead_timezone():
    # 14:00 UTC is 10:00 in New York (inside) and 20:00 in Dhaka (outside).
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    assert within_business_hours(now, "America/New_York", HOURS) is True
    assert within_business_hours(now, "Asia/Dhaka", HOURS) is False


def test_unknown_timezone_falls_back_to_utc():
    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    assert within_business_hours(now, "Mars/Olympus_Mons", HOURS) is True


def test_next_business_day_start_from_friday_evening():
    now = datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc)  # Friday night UTC
    nxt = next_business_day_start(now, "UTC", HOURS)
    assert nxt.weekday() == 0  # Monday
    assert nxt.hour == 9

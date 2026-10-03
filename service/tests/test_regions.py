from datetime import datetime, timezone

import pytest

from app.regions import (
    country_code,
    country_for_timezone,
    resolve_lead_timezone,
    timezone_for_country,
    timezone_for_state,
)
from app.scheduling import within_business_hours
from app.settings_store import hours_for_timezone

DEFAULT = {"start_hour": 9, "end_hour": 17, "weekdays": [0, 1, 2, 3, 4]}
SUN_THU = [6, 0, 1, 2, 3]


# --- country / state lookups ------------------------------------------------

@pytest.mark.parametrize("value,code", [
    ("US", "US"), ("usa", "US"), ("United States", "US"),
    ("Bangladesh", "BD"), ("bd", "BD"), ("UK", "GB"), ("England", "GB"),
    ("UAE", "AE"), ("South Korea", "KR"), ("Germany", "DE"),
    ("Atlantis", None), ("", None), (None, None),
])
def test_country_code(value, code):
    assert country_code(value) == code


def test_multi_zone_countries_use_their_main_business_zone():
    assert timezone_for_country("US") == "America/New_York"
    assert timezone_for_country("CA") == "America/Toronto"
    assert timezone_for_country("AU") == "Australia/Sydney"
    assert timezone_for_country("BD") == "Asia/Dhaka"
    assert timezone_for_country("GB") == "Europe/London"


def test_state_lookup_accepts_codes_and_names_per_country():
    assert timezone_for_state("CA", "US") == "America/Los_Angeles"
    assert timezone_for_state("california", "US") == "America/Los_Angeles"
    assert timezone_for_state("TX", "US") == "America/Chicago"
    assert timezone_for_state("AZ", "US") == "America/Phoenix"
    # The same code means a different place in another country.
    assert timezone_for_state("WA", "AU") == "Australia/Perth"
    assert timezone_for_state("BC", "CA") == "America/Vancouver"
    assert timezone_for_state("Dhaka Division", "BD") is None


def test_country_for_timezone():
    assert country_for_timezone("Asia/Dhaka") == "BD"
    assert country_for_timezone("America/Chicago") == "US"
    assert country_for_timezone("UTC") is None


# --- per-lead resolution ----------------------------------------------------

def test_override_beats_everything():
    lead = {"timezone_override": "Asia/Tokyo", "lead_timezone": "Europe/Paris",
            "prospect_country": "US", "prospect_state": "TX"}
    assert resolve_lead_timezone(lead) == ("Asia/Tokyo", "enrollment override")


def test_explicit_shared_timezone_is_trusted():
    lead = {"lead_timezone": "Europe/Paris", "prospect_country": "US"}
    assert resolve_lead_timezone(lead)[0] == "Europe/Paris"


def test_shared_default_does_not_hide_a_lead_in_another_region():
    # leads.timezone defaults to America/New_York; the prospect is in Dhaka.
    lead = {"lead_timezone": "America/New_York", "prospect_country": "Bangladesh"}
    assert resolve_lead_timezone(lead) == ("Asia/Dhaka", "prospects country")


def test_us_state_narrows_the_zone():
    lead = {"lead_timezone": "America/New_York",
            "prospect_country": "United States", "prospect_state": "California"}
    assert resolve_lead_timezone(lead) == ("America/Los_Angeles", "prospects state")


def test_leads_state_is_read_as_a_us_state():
    lead = {"lead_timezone": "America/New_York", "lead_state": "TX"}
    assert resolve_lead_timezone(lead) == ("America/Chicago", "leads.state state")


def test_company_location_is_used_when_prospect_data_is_missing():
    lead = {"company_location": {"country": "Germany", "city": "Berlin"}}
    assert resolve_lead_timezone(lead) == ("Europe/Berlin", "company_profiles.location country")


def test_falls_back_to_shared_default_then_utc():
    assert resolve_lead_timezone({"lead_timezone": "America/New_York"}) == (
        "America/New_York", "leads.timezone (default)")
    assert resolve_lead_timezone({"lead_timezone": "Not/AZone"}) == ("UTC", "fallback")
    assert resolve_lead_timezone({}) == ("UTC", "fallback")


# --- regional business hours ------------------------------------------------

def test_default_hours_without_region_entry():
    hours = hours_for_timezone(DEFAULT, {"BD": {"weekdays": SUN_THU}}, "America/Chicago")
    assert hours.weekdays == (0, 1, 2, 3, 4) and hours.start_hour == 9


def test_country_entry_changes_only_the_fields_it_sets():
    hours = hours_for_timezone(DEFAULT, {"BD": {"weekdays": SUN_THU}}, "Asia/Dhaka")
    assert hours.weekdays == tuple(SUN_THU)
    assert (hours.start_hour, hours.end_hour) == (9, 17)


def test_timezone_entry_beats_country_entry():
    by_region = {"US": {"start_hour": 8}, "America/Phoenix": {"start_hour": 10}}
    assert hours_for_timezone(DEFAULT, by_region, "America/Phoenix").start_hour == 10
    assert hours_for_timezone(DEFAULT, by_region, "America/Denver").start_hour == 8


def test_sunday_is_a_working_day_in_dhaka_but_not_in_new_york():
    by_region = {"BD": {"weekdays": SUN_THU}}
    sunday_0500_utc = datetime(2026, 9, 27, 5, 0, tzinfo=timezone.utc)  # 11:00 Dhaka
    dhaka = hours_for_timezone(DEFAULT, by_region, "Asia/Dhaka")
    new_york = hours_for_timezone(DEFAULT, by_region, "America/New_York")
    assert within_business_hours(sunday_0500_utc, "Asia/Dhaka", dhaka) is True
    assert within_business_hours(sunday_0500_utc, "America/New_York", new_york) is False

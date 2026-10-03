"""Work out which timezone a lead lives in, from whatever the shared tables have.

The shared ``public.leads.timezone`` column defaults to ``America/New_York``,
so for many leads it says nothing about where they really are. Region data
(US state, country, company location) is used to do better. Resolution order:

  1. ``cold_email.enrollments.timezone`` — an explicit per-lead override
  2. ``leads.timezone`` when it is set to something other than the default
  3. region: state/province (US, Canada, Australia), else country
  4. ``leads.timezone`` even if it is the default
  5. UTC

Country → timezone uses the IANA tables shipped in the ``tzdata`` package, so
every country is covered. Countries spanning several zones fall back to the
zone most of their business population uses unless a state says otherwise.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib import resources
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

# public.leads.timezone's column default; indistinguishable from "not set".
SHARED_DEFAULT_TIMEZONE = "America/New_York"

# Multi-zone countries: the zone to use when no state/province is known.
# zone.tab lists zones geographically, so its first entry is often a poor pick
# (Canada would get St. John's, Australia Lord Howe Island).
COUNTRY_PRIMARY_ZONE = {
    "US": "America/New_York",
    "CA": "America/Toronto",
    "AU": "Australia/Sydney",
    "BR": "America/Sao_Paulo",
    "RU": "Europe/Moscow",
    "MX": "America/Mexico_City",
    "ID": "Asia/Jakarta",
    "KZ": "Asia/Almaty",
    "CD": "Africa/Kinshasa",
    "MN": "Asia/Ulaanbaatar",
    "UA": "Europe/Kyiv",
    "ES": "Europe/Madrid",
    "PT": "Europe/Lisbon",
    "DE": "Europe/Berlin",
    "CN": "Asia/Shanghai",
    "AR": "America/Argentina/Buenos_Aires",
    "CL": "America/Santiago",
    "EC": "America/Guayaquil",
    "NZ": "Pacific/Auckland",
    "MY": "Asia/Kuala_Lumpur",
}

# Where a state splits across zones, the zone of its main population centres.
US_STATES = {
    "AL": ("Alabama", "America/Chicago"),
    "AK": ("Alaska", "America/Anchorage"),
    "AZ": ("Arizona", "America/Phoenix"),
    "AR": ("Arkansas", "America/Chicago"),
    "CA": ("California", "America/Los_Angeles"),
    "CO": ("Colorado", "America/Denver"),
    "CT": ("Connecticut", "America/New_York"),
    "DE": ("Delaware", "America/New_York"),
    "DC": ("District of Columbia", "America/New_York"),
    "FL": ("Florida", "America/New_York"),
    "GA": ("Georgia", "America/New_York"),
    "HI": ("Hawaii", "Pacific/Honolulu"),
    "ID": ("Idaho", "America/Boise"),
    "IL": ("Illinois", "America/Chicago"),
    "IN": ("Indiana", "America/Indiana/Indianapolis"),
    "IA": ("Iowa", "America/Chicago"),
    "KS": ("Kansas", "America/Chicago"),
    "KY": ("Kentucky", "America/New_York"),
    "LA": ("Louisiana", "America/Chicago"),
    "ME": ("Maine", "America/New_York"),
    "MD": ("Maryland", "America/New_York"),
    "MA": ("Massachusetts", "America/New_York"),
    "MI": ("Michigan", "America/Detroit"),
    "MN": ("Minnesota", "America/Chicago"),
    "MS": ("Mississippi", "America/Chicago"),
    "MO": ("Missouri", "America/Chicago"),
    "MT": ("Montana", "America/Denver"),
    "NE": ("Nebraska", "America/Chicago"),
    "NV": ("Nevada", "America/Los_Angeles"),
    "NH": ("New Hampshire", "America/New_York"),
    "NJ": ("New Jersey", "America/New_York"),
    "NM": ("New Mexico", "America/Denver"),
    "NY": ("New York", "America/New_York"),
    "NC": ("North Carolina", "America/New_York"),
    "ND": ("North Dakota", "America/Chicago"),
    "OH": ("Ohio", "America/New_York"),
    "OK": ("Oklahoma", "America/Chicago"),
    "OR": ("Oregon", "America/Los_Angeles"),
    "PA": ("Pennsylvania", "America/New_York"),
    "RI": ("Rhode Island", "America/New_York"),
    "SC": ("South Carolina", "America/New_York"),
    "SD": ("South Dakota", "America/Chicago"),
    "TN": ("Tennessee", "America/Chicago"),
    "TX": ("Texas", "America/Chicago"),
    "UT": ("Utah", "America/Denver"),
    "VT": ("Vermont", "America/New_York"),
    "VA": ("Virginia", "America/New_York"),
    "WA": ("Washington", "America/Los_Angeles"),
    "WV": ("West Virginia", "America/New_York"),
    "WI": ("Wisconsin", "America/Chicago"),
    "WY": ("Wyoming", "America/Denver"),
    "PR": ("Puerto Rico", "America/Puerto_Rico"),
}

CA_PROVINCES = {
    "AB": ("Alberta", "America/Edmonton"),
    "BC": ("British Columbia", "America/Vancouver"),
    "MB": ("Manitoba", "America/Winnipeg"),
    "NB": ("New Brunswick", "America/Moncton"),
    "NL": ("Newfoundland and Labrador", "America/St_Johns"),
    "NS": ("Nova Scotia", "America/Halifax"),
    "NT": ("Northwest Territories", "America/Yellowknife"),
    "NU": ("Nunavut", "America/Iqaluit"),
    "ON": ("Ontario", "America/Toronto"),
    "PE": ("Prince Edward Island", "America/Halifax"),
    "QC": ("Quebec", "America/Toronto"),
    "SK": ("Saskatchewan", "America/Regina"),
    "YT": ("Yukon", "America/Whitehorse"),
}

AU_STATES = {
    "ACT": ("Australian Capital Territory", "Australia/Sydney"),
    "NSW": ("New South Wales", "Australia/Sydney"),
    "NT": ("Northern Territory", "Australia/Darwin"),
    "QLD": ("Queensland", "Australia/Brisbane"),
    "SA": ("South Australia", "Australia/Adelaide"),
    "TAS": ("Tasmania", "Australia/Hobart"),
    "VIC": ("Victoria", "Australia/Melbourne"),
    "WA": ("Western Australia", "Australia/Perth"),
}

STATES_BY_COUNTRY = {"US": US_STATES, "CA": CA_PROVINCES, "AU": AU_STATES}

# Common spellings that differ from the names in iso3166.tab.
COUNTRY_ALIASES = {
    "USA": "US", "U.S.": "US", "U.S.A.": "US", "UNITED STATES OF AMERICA": "US",
    "AMERICA": "US",
    "UK": "GB", "U.K.": "GB", "UNITED KINGDOM": "GB", "GREAT BRITAIN": "GB",
    "ENGLAND": "GB", "SCOTLAND": "GB", "WALES": "GB", "NORTHERN IRELAND": "GB",
    "UAE": "AE", "U.A.E.": "AE",
    "SOUTH KOREA": "KR", "KOREA": "KR", "REPUBLIC OF KOREA": "KR",
    "NORTH KOREA": "KP",
    "RUSSIAN FEDERATION": "RU", "VIETNAM": "VN", "VIET NAM": "VN",
    "CZECHIA": "CZ", "TURKIYE": "TR", "TÜRKIYE": "TR",
    "HOLLAND": "NL", "THE NETHERLANDS": "NL",
    "IVORY COAST": "CI", "CÔTE D'IVOIRE": "CI", "COTE D'IVOIRE": "CI",
    "DR CONGO": "CD", "DRC": "CD", "HONG KONG SAR": "HK",
    "KSA": "SA",
}


# ---------------------------------------------------------------------------
# IANA tables
# ---------------------------------------------------------------------------

def _tab_lines(name: str) -> list[list[str]]:
    text = (resources.files("tzdata.zoneinfo") / name).read_text(encoding="utf-8")
    return [
        line.split("\t")
        for line in text.splitlines()
        if line and not line.startswith("#")
    ]


@lru_cache(maxsize=1)
def _country_names() -> dict[str, str]:
    """Upper-cased country name -> ISO code, from iso3166.tab plus aliases."""
    names = {name.strip().upper(): code for code, name in _tab_lines("iso3166.tab")}
    names.update(COUNTRY_ALIASES)
    return names


@lru_cache(maxsize=1)
def _zones_by_country() -> dict[str, list[str]]:
    zones: dict[str, list[str]] = {}
    for fields in _tab_lines("zone.tab"):
        zones.setdefault(fields[0], []).append(fields[2])
    return zones


@lru_cache(maxsize=1)
def _country_by_zone() -> dict[str, str]:
    by_zone = {}
    for code, zones in _zones_by_country().items():
        for zone in zones:
            by_zone.setdefault(zone, code)
    return by_zone


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def is_valid_timezone(name: Optional[str]) -> bool:
    if not name or not isinstance(name, str):
        return False
    try:
        ZoneInfo(name.strip())
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def country_code(value: Optional[str]) -> Optional[str]:
    """ISO 3166 alpha-2 code for a country name or code, or None."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip().upper()
    if not v:
        return None
    if len(v) == 2 and v in _zones_by_country():
        return v
    return _country_names().get(v)


def country_for_timezone(name: Optional[str]) -> Optional[str]:
    """The country an IANA zone belongs to (``Asia/Dhaka`` -> ``BD``)."""
    if not name:
        return None
    return _country_by_zone().get(name.strip())


def timezone_for_country(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    if code in COUNTRY_PRIMARY_ZONE:
        return COUNTRY_PRIMARY_ZONE[code]
    zones = _zones_by_country().get(code)
    return zones[0] if zones else None


def timezone_for_state(state: Optional[str], country: Optional[str]) -> Optional[str]:
    """Zone for a state/province (code or full name) within a country."""
    if not state or not isinstance(state, str):
        return None
    table = STATES_BY_COUNTRY.get(country or "")
    if table is None:
        return None
    s = state.strip().upper()
    if s in table:
        return table[s][1]
    for name, zone in table.values():
        if name.upper() == s:
            return zone
    return None


def _location_value(location: Any, *keys: str) -> Optional[str]:
    """Pull a string out of company_profiles.location, whatever its shape."""
    if not isinstance(location, dict):
        return None
    for key in keys:
        value = location.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def resolve_lead_timezone(lead: dict[str, Any]) -> tuple[str, str]:
    """Return ``(timezone, source)`` for a lead-details row.

    ``source`` says which piece of data decided it, for logs and debugging.
    """
    override = lead.get("timezone_override")
    if is_valid_timezone(override):
        return override.strip(), "enrollment override"

    shared = lead.get("lead_timezone")
    shared_valid = is_valid_timezone(shared)
    if shared_valid and shared.strip() != SHARED_DEFAULT_TIMEZONE:
        return shared.strip(), "leads.timezone"

    location = lead.get("company_location")
    candidates = [
        # (country, state, source)
        (lead.get("prospect_country"), lead.get("prospect_state"), "prospects"),
        (_location_value(location, "country", "country_code"),
         _location_value(location, "state", "region", "province"),
         "company_profiles.location"),
        # public.leads has a state but no country; its data is US-centric.
        (None, lead.get("lead_state"), "leads.state"),
    ]
    for raw_country, state, source in candidates:
        country = country_code(raw_country)
        zone = timezone_for_state(state, country or "US")
        if zone:
            return zone, f"{source} state"
        zone = timezone_for_country(country)
        if zone:
            return zone, f"{source} country"

    if shared_valid:
        return shared.strip(), "leads.timezone (default)"
    return "UTC", "fallback"

"""Offline tests for decoding Google's price-insights block.

The fixture is slot ``[5]`` of the ``ds:1`` payload from a live round-trip
YYZ-NRT search (2026-11-07). The page showed "Prices are currently typical"
and "usually cost between CA$1,350-2,200", which pins the slot layout the
decoder reads.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from fli.models import (
    Airport,
    FlightSearchFilters,
    FlightSegment,
    PassengerInfo,
    PriceInsights,
)
from fli.search._decoders import parse_price_insights
from fli.search.flights import SearchFlights

FIXTURE = Path(__file__).parent / "fixtures" / "price_insights_yyz_nrt_block5.json"


def _inner(block):
    """Build a payload whose slot 5 is ``block``, as in the live page."""
    return [None, None, None, None, None, block]


@pytest.fixture
def block():
    return json.loads(FIXTURE.read_text())


def test_decodes_range_lowest_and_history(block):
    insights = parse_price_insights(_inner(block))

    assert insights is not None
    assert insights.lowest_price == 1802
    assert insights.typical_low == 1350
    assert insights.typical_high == 2200
    assert len(insights.history) == 61
    assert insights.level == "typical"


def test_history_is_ordered_daily_and_ends_on_the_current_low(block):
    history = parse_price_insights(_inner(block)).history

    assert history[0].timestamp == datetime(2026, 9, 8, 4, 0, tzinfo=timezone.utc)
    assert history[0].price == 1844
    assert history[-1].timestamp.date().isoformat() == "2026-11-07"
    assert history[-1].price == 1802
    assert [p.timestamp for p in history] == sorted(p.timestamp for p in history)
    assert min(p.price for p in history) == 1515
    assert max(p.price for p in history) == 2021


@pytest.mark.parametrize(
    ("lowest", "expected"),
    [(1200, "low"), (1350, "typical"), (1802, "typical"), (2200, "typical"), (2500, "high")],
)
def test_level_is_derived_from_the_typical_range(lowest, expected):
    insights = PriceInsights(lowest_price=lowest, typical_low=1350, typical_high=2200)

    assert insights.level == expected


def test_level_is_none_when_a_figure_is_missing():
    assert PriceInsights(lowest_price=1802, typical_low=1350).level is None
    assert PriceInsights().level is None


@pytest.mark.parametrize("inner", [None, [], [None] * 5, _inner(None), _inner([]), _inner("x")])
def test_absent_or_malformed_block_returns_none(inner):
    assert parse_price_insights(inner) is None


def test_missing_history_keeps_the_range(block):
    block[10] = []

    insights = parse_price_insights(_inner(block))

    assert insights is not None
    assert insights.history == []
    assert (insights.typical_low, insights.typical_high) == (1350, 2200)


def test_bad_history_pairs_are_skipped(block):
    good = [1788840000000, 1844]
    block[10] = [[good, [123, 999], ["x", 1], [1788926400000, -5], None, [1789012800000, True]]]

    insights = parse_price_insights(_inner(block))

    # Only the first pair is well formed: the rest are out of range, mistyped,
    # negative or a bool.
    assert [p.price for p in insights.history] == [1844]


def test_reversed_range_is_dropped(block):
    block[4], block[5] = [None, 2200], [None, 1350]

    insights = parse_price_insights(_inner(block))

    assert insights.typical_low is None
    assert insights.typical_high is None
    assert insights.lowest_price == 1802
    assert insights.level is None


def test_search_flights_exposes_and_resets_insights(block):
    """The first page's block lands on ``SearchFlights.price_insights``."""
    # An empty flights array is enough: an empty result is a normal outcome,
    # and the insights are captured before the rows are decoded.
    payload = [[None, None, None, None, "session"], None, [[]], [[]], None, block]
    filters = FlightSearchFilters(
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.YYZ, 0]],
                arrival_airport=[[Airport.NRT, 0]],
                travel_date="2027-03-10",
            )
        ],
    )
    search = SearchFlights()
    assert search.price_insights is None

    with patch("fli.search.flights.fetch_payload", return_value=payload):
        search.search(filters)
    assert search.price_insights is not None
    assert search.price_insights.typical_high == 2200
    assert len(search.price_insights.history) == 61

    # A later search whose page carries no block must not keep the old figures.
    payload[5] = None
    with patch("fli.search.flights.fetch_payload", return_value=payload):
        search.search(filters)
    assert search.price_insights is None

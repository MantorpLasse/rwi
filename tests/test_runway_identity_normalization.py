import pytest

from app.services.runway_identity import (
    AmbiguousRunwayDesignationError,
    normalize_end,
    normalize_pair,
    pair_ends,
)


def test_leading_zero_normalizes_the_same_as_no_zero():
    assert normalize_end("06") == normalize_end("6") == "6"
    assert normalize_end("04R") == normalize_end("4R") == "4R"


def test_reciprocal_pair_order_normalizes_identically():
    assert normalize_pair("04R/22L") == normalize_pair("22L/04R") == "4R/22L"
    assert normalize_pair("24/06") == normalize_pair("06/24") == "6/24"


def test_normalize_pair_matches_existing_seeded_designations_unchanged():
    # MDW and CGF's real, already-seeded legacy Runway rows - the canonical
    # form must not require reordering data that's already correct.
    assert normalize_pair("13L/31R") == "13L/31R"
    assert normalize_pair("6/24") == "6/24"


def test_pair_ends_splits_a_normalized_pair():
    assert pair_ends(normalize_pair("22L/04R")) == ("4R", "22L")


@pytest.mark.parametrize("bad", ["04R", "04R/22L/13L", "", "04R/04R"])
def test_normalize_pair_fails_closed_on_anything_not_two_distinct_ends(bad):
    with pytest.raises(AmbiguousRunwayDesignationError):
        normalize_pair(bad)


def test_normalize_end_fails_closed_on_no_numeric_heading():
    with pytest.raises(AmbiguousRunwayDesignationError):
        normalize_end("N")
    with pytest.raises(AmbiguousRunwayDesignationError):
        normalize_end("")

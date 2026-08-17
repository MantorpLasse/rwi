import pytest

from app.services.runway_identity import (
    AmbiguousRunwayDesignationError,
    is_two_ended_pair_shape,
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


# ---------------------------------------------------------------------------
# is_two_ended_pair_shape() - the structural NASR-input classification rule
# (docs/domain/nasr-special-record-classification-investigation.md). Pure
# shape check: exactly two non-empty, whitespace-trimmed tokens split on a
# single "/". Deliberately NOT a prefix/suffix heuristic - none of these
# tests reference "H"/"B"/"X" as special characters, only pair structure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("accepted", ["04R/22L", "13L/31R", "6/24"])
def test_is_two_ended_pair_shape_accepts_real_runway_pairs(accepted):
    assert is_two_ended_pair_shape(accepted) is True


@pytest.mark.parametrize(
    "rejected",
    [
        "H1",  # helicopter pad - rejected for lacking pair shape, not for starting with "H"
        "H-A",  # helicopter pad, hyphenated multi-pad naming
        "B1",  # balloonport pad - rejected for lacking pair shape, not for starting with "B"
        "00X",  # empty placeholder record
        "10X",
        "19X",
        "",  # empty string
        "   ",  # whitespace-only
        "/22L",  # leading slash - first token empty
        "04R/",  # trailing slash - second token empty
        "04R//22L",  # double slash - three parts, one empty
        "04R/22L/XX",  # three-part malformed value
        "04R",  # single token, no slash at all
    ],
)
def test_is_two_ended_pair_shape_rejects_non_pair_shapes(rejected):
    assert is_two_ended_pair_shape(rejected) is False


def test_is_two_ended_pair_shape_rejects_none():
    assert is_two_ended_pair_shape(None) is False


def test_is_two_ended_pair_shape_trims_whitespace_around_tokens():
    """Matches normalize_pair()'s own whitespace convention: tokens are
    stripped before the non-empty check, so incidental whitespace around
    a real pair doesn't cause a false rejection."""
    assert is_two_ended_pair_shape(" 04R / 22L ") is True
    assert is_two_ended_pair_shape(" / ") is False  # both tokens empty after strip


def test_is_two_ended_pair_shape_does_not_depend_on_special_prefix_characters():
    """Classification is purely structural: a two-token pair whose tokens
    happen to start with H/B, or a non-pair value that happens not to
    start with H/B/X, must classify the same way a naive prefix/suffix
    heuristic would get wrong."""
    assert is_two_ended_pair_shape("H1/H2") is True  # two tokens, pair-shaped - not excluded merely for containing "H"
    assert is_two_ended_pair_shape("ZZ") is False  # single token, no special character at all - still rejected


def test_normalize_pair_uses_the_same_shape_check_normalize_pair_itself_requires():
    """normalize_pair() must still reject exactly what is_two_ended_pair_shape()
    rejects - the refactor extracting the shared check must not change
    normalize_pair()'s own behavior."""
    for value in ("H1", "B1", "00X", "", "04R", "04R/22L/XX"):
        assert is_two_ended_pair_shape(value) is False
        with pytest.raises(AmbiguousRunwayDesignationError):
            normalize_pair(value)

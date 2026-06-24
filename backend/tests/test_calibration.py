from app.calibration import (
    SectorProfileRecord,
    ordered_profile,
    recommend_sector_profiles,
    score_sector_profile_records,
)
from app.calculator import preferred_sector_profile_for_slot, sector_profile_options_for_count


def test_ordered_profile_uses_display_order():
    assert ordered_profile(["TOP", "LOWER", "UPPER"]) == ("LOWER", "UPPER", "TOP")


def test_recommend_sector_profiles_uses_manual_majority():
    records = [
        SectorProfileRecord("a", 0, "07:00-08:00", 2, ("LOWER", "UPPER")),
        SectorProfileRecord("a", 1, "08:00-09:00", 2, ("LOWER", "UPPER")),
        SectorProfileRecord("b", 0, "07:00-08:00", 2, ("LOWER", "TOP")),
    ]

    recommended = recommend_sector_profiles(records, {2: ("LOWER", "TOP")})

    assert recommended[2] == ("LOWER", "UPPER")


def test_score_sector_profile_records_counts_sector_distance():
    records = [
        SectorProfileRecord("a", 0, "07:00-08:00", 2, ("LOWER", "UPPER")),
        SectorProfileRecord("a", 1, "08:00-09:00", 2, ("LOWER", "TOP")),
    ]

    score = score_sector_profile_records(records, {2: ("LOWER", "TOP")})

    assert score["total_hours"] == 2
    assert score["exact_matches"] == 1
    assert score["profile_mismatch_hours"] == 1
    assert score["sector_distance"] == 2


def test_sector_profile_options_allow_two_sector_variants():
    assert sector_profile_options_for_count(2) == [("LOWER", "UPPER"), ("LOWER", "TOP")]


def test_preferred_sector_profile_uses_daily_context():
    assert preferred_sector_profile_for_slot(16, 2, 64) == ("LOWER", "UPPER")
    assert preferred_sector_profile_for_slot(16, 2, 67) == ("LOWER", "TOP")
    assert preferred_sector_profile_for_slot(23, 2, 59) == ("LOWER", "UPPER")
    assert preferred_sector_profile_for_slot(23, 2, 64) == ("LOWER", "TOP")

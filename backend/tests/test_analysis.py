import zipfile
from datetime import date
from io import BytesIO

from app.analysis import (
    AnalysisMapping,
    AnalysisParams,
    SectorRecord,
    XlsxReader,
    build_analysis_workbook,
    predict_record,
    summarize_operational_blocks,
)


def test_operational_blocks_use_representative_day_not_hourly_envelope():
    high_morning = [5, *([1] * 23)]
    high_midday = [1, 5, *([1] * 22)]
    lower_day = [1] * 20 + [0] * 4

    blocks = summarize_operational_blocks([
        {
            "date": "2026-10-05",
            "weekday": "PO",
            "special_day": False,
            "hourly_for_calculator": high_morning,
        },
        {
            "date": "2026-10-06",
            "weekday": "TO",
            "special_day": False,
            "hourly_for_calculator": high_midday,
        },
        {
            "date": "2026-10-07",
            "weekday": "SR",
            "special_day": False,
            "hourly_for_calculator": lower_day,
        },
    ])

    assert len(blocks) == 2
    assert blocks[0]["sector_hours"] == sum(high_morning)
    assert blocks[0]["hourly_for_calculator"] in [high_morning, high_midday]
    assert blocks[0]["hourly_for_calculator"] != [5, 5, *([1] * 22)]
    assert blocks[0]["representative_date"] in {"2026-10-05", "2026-10-06"}
    assert blocks[1]["sector_hours"] == sum(lower_day)


def test_operational_blocks_break_equal_sector_hour_ties_by_traffic():
    saturday = [4] * 12 + [2] * 12
    sunday = [2] * 12 + [4] * 12

    blocks = summarize_operational_blocks([
        {
            "date": "2026-10-10",
            "weekday": "SO",
            "special_day": False,
            "flights": 1930,
            "hourly_for_calculator": saturday,
        },
        {
            "date": "2026-10-11",
            "weekday": "NE",
            "special_day": False,
            "flights": 1845,
            "hourly_for_calculator": sunday,
        },
    ])

    assert len(blocks) == 1
    assert blocks[0]["representative_date"] == "2026-10-10"
    assert blocks[0]["representative_flights"] == 1930
    assert blocks[0]["max_flights"] == 1930


def test_prediction_caps_threshold_rounding_to_daily_target():
    params = AnalysisParams()
    record = SectorRecord(
        block_year=2026,
        day_date=date(2026, 10, 12),
        slot_index=1,
        weekday="PO",
        iso_week=42,
        iso_weekday=1,
        flights=0,
        hourly=[],
        actual_total=0,
        has_actual=False,
    )
    coefficients = {
        "intercept": 64.6,
        "coefficient_per_flight": 0,
        "weekday_adjustments": {"PO": 0},
    }
    base_profile = [3.5] * 7 + [2.925] * 12
    profiles = {
        (record.slot_index, hour_index): value
        for hour_index, value in enumerate(base_profile)
    }

    prediction = predict_record(
        record,
        coefficients,
        profiles,
        {},
        {"weekday": None, "weekend": None},
        params,
        params.thresholds,
    )

    assert prediction["target"] == 64.6
    assert sum(prediction["hourly_for_calculator"]) == 65
    assert prediction["hybrid_total"] == 65


def test_analysis_workbook_export_builds_readable_xlsx_with_mapping():
    params = AnalysisParams()
    mapping = AnalysisMapping()
    metric_set = {
        "count": 0,
        "mae": None,
        "bias": None,
        "rmse": None,
        "within_3": None,
        "within_5": None,
        "within_10": None,
        "r2": None,
    }
    result = {
        "traffic_forecast": {"mode": "previous_year_growth"},
        "data_counts": {
            "forecast_days": 0,
            "checked_days": 0,
            "known_target_days": 0,
            "fit_days": 0,
        },
        "operational_fit_metrics": metric_set,
        "traffic_fit_metrics": metric_set,
        "analog_fit_metrics": metric_set,
        "hourly_metrics": {
            "exact_percent": None,
            "within_one_percent": None,
        },
        "used_coefficients": {
            "intercept": 0,
            "coefficient_per_flight": 0,
            "weekday_adjustments": {},
            "thresholds": {},
        },
        "reference_density": {"weekday": None, "weekend": None},
        "formula": {"template": "T(d)", "example": None},
        "forecast_days": [],
        "weekday_summary": [],
        "monthly_summary": [],
        "hourly_summary": [],
        "top_misses": [],
        "pattern_suggestions": [],
    }

    content = build_analysis_workbook(result, "vir.xlsx", params, mapping)

    with zipfile.ZipFile(BytesIO(content)) as archive:
        assert "xl/workbook.xml" in archive.namelist()

    assert "TEORIJA_MODELA" in XlsxReader(content).sheet_names()

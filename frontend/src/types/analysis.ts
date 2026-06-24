export type WeekdayCode = 'PO' | 'TO' | 'SR' | 'ČE' | 'PE' | 'SO' | 'NE';

export type AnalysisMapping = {
  sector_sheet: string;
  adjusted_sector_sheet: string;
  traffic_sheet: string;
  forecast_traffic_sheet: string;
  first_col: number;
  last_col: number;
  year_rows: Record<string, number>;
  traffic_header_row: number;
  traffic_first_row: number;
  traffic_date_col: number;
  traffic_weekday_col: number;
  traffic_flights_col: number;
};

export type AnalysisParams = {
  fit_years: number[];
  test_year: number;
  night_add: number;
  min_daily_sector_hours: number;
  max_sectors: number;
  year_weights: Record<string, number>;
  thresholds: Record<string, number>;
  intercept_override: number | null;
  coefficient_override: number | null;
  weekday_adjustment_overrides: Record<string, number>;
  weekday_buffers: Record<string, number>;
  optimize_with_cp_sat: boolean;
  optimize_thresholds: boolean;
  threshold_search_step: number;
  threshold_search_radius: number;
  lock_manual_coefficients: boolean;
  lock_intercept: boolean;
  lock_coefficient: boolean;
  lock_weekday_adjustments: boolean;
  lock_thresholds: boolean;
  cp_sat_time_limit_seconds: number;
  under_prediction_weight: number;
  over_prediction_weight: number;
  traffic_forecast_mode: string;
  traffic_source_year: number | null;
  use_actual_target_traffic: boolean;
  annual_traffic_growth_rates: Record<string, number>;
  default_traffic_growth: number;
  planning_safety_margin: number;
  analog_backtest_enabled: boolean;
  forecast_start_date: string | null;
  forecast_end_date: string | null;
  planning_start_date: string | null;
  planning_end_date: string | null;
  fatigue_enabled: boolean;
  fatigue_lambda: number;
  fatigue_apply_max: boolean;
  reference_year: number;
  target_weekday_staff: number;
  target_weekend_staff: number;
  reference_weekday_staff: number;
  reference_weekend_staff: number;
  allowed_density_increase: number;
  season_start_month: number;
  season_end_month: number;
  special_days: string[];
  special_day_buffer: number;
  special_day_exclude_from_fit: boolean;
};

export type WorkbookPayload = {
  file_name: string;
  file_base64: string;
  mapping?: AnalysisMapping;
  params?: AnalysisParams;
};

export type WorkbookSheetProfile = {
  name: string;
  max_row: number;
  max_col: number;
};

export type WorkbookProfile = {
  file_name: string;
  sheets: WorkbookSheetProfile[];
  suggested_mapping: AnalysisMapping;
  detected_year_rows: Record<string, number>;
  suggested_params: AnalysisParams;
};

export type AnalysisMetricSet = {
  count: number;
  mae: number | null;
  bias: number | null;
  rmse: number | null;
  within_3: number | null;
  within_5: number | null;
  within_10: number | null;
  r2: number | null;
};

export type HourlyMetrics = {
  count: number;
  exact_percent: number | null;
  within_one_percent: number | null;
  mae: number | null;
  bias: number | null;
  error_distribution: Record<string, number>;
};

export type AnalysisResult = {
  mapping: AnalysisMapping;
  data_counts: {
    traffic_days: number;
    sector_days: number;
    profile_days: number;
    fit_days: number;
    test_days: number;
    forecast_days: number;
    checked_days: number;
    analog_days: number;
    known_target_days: number;
  };
  formula: {
    template: string;
    example: string | null;
  };
  optimization: {
    method: string;
    solver_status: string | null;
    objective_value: number | null;
    best_objective_bound: number | null;
    cp_sat_time_limit_seconds: number;
    under_prediction_weight: number;
    over_prediction_weight: number;
    lock_manual_coefficients: boolean;
    lock_intercept: boolean;
    lock_coefficient: boolean;
    lock_weekday_adjustments: boolean;
    lock_thresholds: boolean;
  };
  traffic_forecast: {
    mode: string;
    generated_days: number;
    actual_days_kept: number;
    missing_days: number;
    source_year?: number;
    annual_growth_rates?: Record<string, number>;
    default_growth?: number;
    sample: Array<{
      date: string | null;
      weekday: WeekdayCode;
      flights: number | null;
      source: string;
    }>;
  };
  threshold_optimization: {
    method: string;
    score: number | null;
    mae?: number | null;
    passes: number;
    improvements?: number;
    records: number;
    step?: number;
    radius?: number;
  };
  reference_density: {
    weekday: number | null;
    weekend: number | null;
  };
  raw_coefficients: {
    intercept: number;
    coefficient_per_flight: number;
    weekday_adjustments: Record<string, number>;
    sample_size: number;
  };
  used_coefficients: {
    intercept: number;
    coefficient_per_flight: number;
    weekday_adjustments: Record<string, number>;
    thresholds: Record<string, number>;
  };
  traffic_fit_metrics: AnalysisMetricSet;
  operational_fit_metrics: AnalysisMetricSet;
  checked_fit_metrics?: AnalysisMetricSet;
  analog_fit_metrics: AnalysisMetricSet;
  hourly_metrics: HourlyMetrics;
  weekday_summary: Array<{
    weekday: WeekdayCode;
    count: number;
    avg_flights: number | null;
    avg_actual: number | null;
    avg_prediction: number | null;
    bias: number | null;
    actual_density: number | null;
  }>;
  monthly_summary: Array<{
    month: number;
    count: number;
    actual_total: number | null;
    prediction_total: number | null;
    mae: number | null;
    bias: number | null;
  }>;
  hourly_summary: Array<{
    hour: string;
    count: number;
    exact_percent: number | null;
    within_one_percent: number | null;
    mae: number | null;
    bias: number | null;
  }>;
  top_misses: Array<{
    date: string;
    weekday: WeekdayCode;
    flights: number | null;
    actual: number | null;
    prediction: number | null;
    error: number | null;
    traffic_target: number | null;
  }>;
  pattern_suggestions: Array<{
    rank: number;
    label: string;
    count: number;
    sector_hours: number;
    dates: string[];
    weekdays: Record<string, number>;
    hourly_for_calculator: number[];
  }>;
  operational_blocks: Array<{
    rank: number;
    label: string;
    period: string;
    day_type: string;
    count: number;
    date_start: string | null;
    date_end: string | null;
    dates: string[];
    weekday_counts: Record<string, number>;
    sector_hours: number;
    avg_day_sector_hours: number | null;
    min_day_sector_hours?: number | null;
    max_day_sector_hours: number | null;
    representative_date?: string | null;
    representative_flights?: number | null;
    max_flights?: number | null;
    sector_hour_tolerance?: number;
    hourly_for_calculator: number[];
    config_candidates: Array<{
      name: string;
      total_people: number;
      max_sector_hours: number;
      reserve_sector_hours: number;
      status: string;
    }>;
  }>;
  forecast_days: Array<{
    date: string;
    weekday: WeekdayCode;
    special_day: boolean;
    special_day_buffer: number;
    flights: number | null;
    traffic_target: number | null;
    target_daytime_sector_hours: number | null;
    base_profile_sum: number | null;
    calibration_factor: number | null;
    predicted_sector_hours: number;
    actual_sector_hours: number | null;
    has_actual: boolean;
    daytime_hours: number[];
    actual_daytime_hours: number[];
    hourly_for_calculator: number[];
    formula: string;
    hybrid_sector_hours: number;
    fatigue_required_sector_hours: number | null;
    fatigue_adjusted_target: number | null;
    fatigue_calibration_factor: number | null;
    base_profile: number[];
    z_values: number[];
    explain_hours: Array<{
      hour: string;
      profile: number | null;
      z: number | null;
      hybrid_sector: number;
      final_sector: number;
      actual_sector: number | null;
    }>;
    thresholds: Record<string, number>;
  }>;
  test_rows_sample: Array<{
    date: string;
    weekday: WeekdayCode;
    flights: number | null;
    actual: number | null;
    prediction: number | null;
    error: number | null;
  }>;
};

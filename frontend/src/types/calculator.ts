export interface ShiftRule {
  code: string;
  start_hour: number;
  duration_hours: number;
  enabled?: boolean;
}

export interface CalculatorSettings {
  max_sectors_per_hour: number;
  max_consecutive_work_hours: number;
  rest_after_max_consecutive_hours: number;
  cp_sat_time_limit_seconds: number;
  cp_sat_no_improvement_seconds: number;
  cp_sat_acceptable_sector_gap: number;
  cp_sat_min_auto_stop_coverage_percent: number;
  include_required_shift_leaders: boolean;
  include_night_fl_requirement: boolean;
  required_night_fl_count: number;
  v1_sector_limit: number;
  v2_sector_limit: number;
  v3_sector_limit: number;
  fmp_sector_limit: number;
  shifts: ShiftRule[];
  officer_shifts: ShiftRule[];
}

export interface FixedStaffRule {
  count: number;
  license: 'FL' | 'APS' | 'ACS';
  shift: string;
  role: string | null;
}

export interface LockedStaffRule {
  count: number;
  license: 'FL' | 'APS' | 'ACS';
  shift: string;
  role: string | null;
  label: string | null;
}

export interface OfficerStaffRule {
  count: number;
  license: 'FL' | 'APS' | 'ACS';
  shift: string;
}

export interface OfficePoolRule {
  count: number;
  license: 'FL' | 'APS' | 'ACS';
}

export interface LicenseMixPercent {
  fl: number;
  aps: number;
  acs: number;
}

export interface CalculatorRequest {
  calculation_mode: 'staff_to_coverage' | 'demand_to_staff';
  total_people: number;
  fl_count: number;
  aps_count: number;
  acs_count: number;
  include_fmp: boolean;
  fmp_shift_mode: 'auto' | 'fixed';
  fmp_shift: string;
  settings: CalculatorSettings;
  requested_sector_counts: number[];
  fixed_staff: FixedStaffRule[];
  locked_staff: LockedStaffRule[];
  officer_staff: OfficerStaffRule[];
  office_pool: OfficePoolRule[];
  license_mix_percent: LicenseMixPercent | null;
  include_pareto: boolean;
  prefer_minimal_fl: boolean;
  office_fallback_mode: 'auto' | 'disabled' | 'force';
  leader_exception_mode: 'forbid' | 'allow';
  max_leader_exception_hours: number;
  continuation_min_sector_hours: number | null;
  solver_random_seed: number;
  preferred_manual_configuration_id?: string | null;
  warm_start?: {
    people: VirtualPerson[];
    hourly_coverage: HourlyCoverage[];
  } | null;
  warm_start_snapshot_id?: string | null;
}

export interface VirtualPerson {
  id: string;
  license: 'FL' | 'APS' | 'ACS';
  shift: string;
  role: string | null;
  sector_hours: number;
  max_sector_hours: number;
  utilization_percent: number;
  used_as_sector_controller: boolean;
  source: 'regular' | 'fixed' | 'officer' | 'office-pool' | string;
}

export interface ShiftSummary {
  shift: string;
  fl: number;
  aps: number;
  acs: number;
  total: number;
}

export interface ParetoPoint {
  people_limit: number;
  planned_people: number;
  active_people: number;
  max_sector_hours: number;
  requested_sector_hours: number;
  coverage_percent: number;
  missing_sector_hours: number;
  scheduled_person_hours: number;
  total_person_capacity_hours: number;
  utilization_percent: number;
  used_officers: number;
  feasible: boolean;
  solver_status: string | null;
  solver_solution_count: number;
  solver_optimality_gap_percent: number | null;
  solver_stop_reason: string | null;
}

export interface SectorAssignment {
  sector_name: string;
  lower_worker: string;
  upper_worker: string;
}

export interface HourlyCoverage {
  hour: string;
  open_sectors: number;
  workers: string[];
  sector_workers: (SectorAssignment | null)[];
}

export interface CalculatorResponse {
  feasible: boolean;
  max_sector_hours: number;
  requested_sector_hours: number;
  solver_upper_bound_sector_hours: number | null;
  solver_gap_to_upper_bound: number | null;
  solver_status: string | null;
  solver_solution_count: number;
  solver_optimality_gap_percent: number | null;
  solver_stop_reason: string | null;
  leader_edge_exception_hours: number;
  fmp_vi_overlap_hours: number;
  crisis_exception_hours: number;
  missing_sector_hours: number;
  baseline_min_people: number;
  baseline_min_people_formula: string | null;
  minimum_required_fl: number;
  planned_people: number;
  active_people: number;
  unused_people: number;
  scheduled_person_hours: number;
  total_person_capacity_hours: number;
  utilization_percent: number;
  people: VirtualPerson[];
  shift_summary: ShiftSummary[];
  hourly_coverage: HourlyCoverage[];
  pareto_points: ParetoPoint[];
  notes: string[];
  warnings: string[];
}

export interface ParetoResponse {
  requested_sector_hours: number;
  points: ParetoPoint[];
  notes: string[];
  warnings: string[];
}

export interface PatternLibraryProfile {
  rule_signature: string;
  pattern_count: number;
  cache_status: string;
  cache_path: string | null;
  generated_at_seconds: number;
  required_group_counts: Record<string, number>;
  patterns_by_shift: Record<string, number>;
  patterns_by_license: Record<'FL' | 'APS' | 'ACS', number>;
  patterns_by_role: Record<string, number>;
}

export interface ManualConfigurationStaffRow {
  source: 'regular' | 'officer' | string;
  shift: string;
  role: string | null;
  fl: number;
  aps: number;
  acs: number;
  total: number;
  start_hour: number | null;
  duration_hours: number | null;
  hour_slots: number[];
}

export interface ManualConfigurationSchedulePerson {
  label: string;
  shift: string | null;
  sector_hours: number | null;
  role: string | null;
  source: string | null;
}

export interface ManualConfigurationSchedule {
  source_path: string;
  people: ManualConfigurationSchedulePerson[];
  hourly_coverage: HourlyCoverage[];
  max_sector_hours: number;
  scheduled_person_hours: number;
}

export interface ManualConfigurationSummary {
  id: string;
  name: string;
  column_index: number;
  parsed_total: number;
  total_without_waiting: number;
  waiting_count: number;
  license_counts: Record<'FL' | 'APS' | 'ACS', number>;
  unsupported_rows: string[];
  status: string;
  model_max_sector_hours: number | string | null;
  model_reported_sector_hours?: number | string | null;
  excel_sector_hours?: number | null;
  model_seconds: number | string | null;
  has_manual_schedule: boolean;
  source_type?: 'excel' | 'user' | string;
  source_label?: string | null;
  created_at?: string | null;
  note?: string | null;
}

export interface ManualConfigurationDetail extends ManualConfigurationSummary {
  source_path: string;
  workbook_path: string | null;
  fixed_staff: FixedStaffRule[];
  officer_staff: OfficerStaffRule[];
  staff_rows: ManualConfigurationStaffRow[];
  manual_schedule: ManualConfigurationSchedule | null;
}

export interface ManualConfigurationLibrary {
  source_path: string | null;
  workbook_path: string | null;
  user_source_path?: string | null;
  configurations: ManualConfigurationSummary[];
}

export interface SaveUserConfigurationRequest {
  name: string | null;
  result: CalculatorResponse;
  note?: string | null;
}

export interface DeleteManualConfigurationResponse {
  deleted: boolean;
  id: string;
  remaining: number;
}

export interface ConfigurationSimilarityMatch {
  id: string;
  name: string;
  source_type: 'excel' | 'user' | string;
  source_label: string | null;
  similarity: number;
  sh_diff: number;
  people_diff: number;
  license_diff: Record<'FL' | 'APS' | 'ACS', number>;
  role_hours_diff: Record<'V1' | 'V2' | 'V3' | 'FMP', number>;
  sector_profile_diff: number;
  workload_diff: number;
  candidate_sector_hours: number;
  candidate_people: number;
}

export interface ConfigurationComparisonResult {
  result: {
    sector_hours: number;
    people: number;
    license_counts: Record<'FL' | 'APS' | 'ACS', number>;
    role_hours: Record<'V1' | 'V2' | 'V3' | 'FMP', number>;
    sector_counts: number[];
  };
  duplicate_warning: string | null;
  matches: ConfigurationSimilarityMatch[];
}

export interface CompleteConfigurationRequest {
  request: CalculatorRequest;
  current_result: CalculatorResponse | null;
  time_limit_seconds?: number;
}

export interface ManualConfigurationOneDownRequest {
  time_limit_seconds?: number;
  settings?: CalculatorSettings | null;
}

export interface CompleteConfigurationCalculatorResult {
  feasible: boolean;
  status: string;
  planned_people: number;
  active_people: number;
  model_sector_hours: number;
  missing_sector_hours: number;
  solver_status: string | null;
  elapsed_seconds: number;
  result: CalculatorResponse;
}

export interface CompletionRoleLimits {
  v1: number;
  v2: number;
  v3: number;
  fmp: number;
}

export interface CompleteConfigurationResult {
  target_people: number;
  requested_sector_hours: number;
  selected_variant: string;
  selected_variant_label: string;
  license_ratio: Record<'FL' | 'APS' | 'ACS', number> | null;
  include_fmp: boolean;
  role_limits: CompletionRoleLimits;
  calculator: CompleteConfigurationCalculatorResult;
  variants: Array<{
    variant: string;
    variant_label: string;
    license_ratio: Record<'FL' | 'APS' | 'ACS', number> | null;
    include_fmp: boolean;
    calculation_mode: 'staff_to_coverage' | 'demand_to_staff';
    role_limits: CompletionRoleLimits;
    calculator: CompleteConfigurationCalculatorResult;
  }>;
}

export interface ManualConfigurationOneDownResult {
  id: string;
  name: string;
  source_type: string;
  manual_people: number;
  target_people: number;
  requested_sector_hours: number;
  license_ratio: Record<'FL' | 'APS' | 'ACS', number>;
  include_fmp: boolean;
  selected_variant: string;
  selected_variant_label: string;
  pattern: {
    checked: boolean;
    feasible: boolean | null;
    status: string | null;
    pattern_count: number | null;
    cache_status: string | null;
    message: string | null;
  };
  calculator: {
    feasible: boolean;
    status: string;
    planned_people: number;
    active_people: number;
    model_sector_hours: number;
    missing_sector_hours: number;
    solver_status: string | null;
    elapsed_seconds: number;
    result: CalculatorResponse;
  };
  variants: Array<{
    variant: string;
    variant_label: string;
    license_ratio: Record<'FL' | 'APS' | 'ACS', number>;
    include_fmp: boolean;
    role_limits: {
      v1: number;
      v2: number;
      v3: number;
      fmp: number;
    };
    pattern: ManualConfigurationOneDownResult['pattern'];
    calculator: ManualConfigurationOneDownResult['calculator'];
  }>;
}

export interface ManualConfigurationAuditHour {
  hour: string;
  manual: number;
  model: number;
  diff: number;
  manual_sectors?: string[];
  model_sectors?: string[];
  matching_sectors?: string[];
  missing_sectors?: string[];
  extra_sectors?: string[];
  manual_workers?: number;
  model_workers?: number;
  worker_diff?: number;
  sectors?: Array<{
    sector_name: string;
    manual: string | null;
    model: string | null;
    status: 'same_workers' | 'same_sector' | 'manual_only' | 'model_only' | 'closed' | string;
  }>;
}

export interface ManualConfigurationAuditRow {
  id?: string;
  name: string;
  status: string;
  exists: boolean;
  parsed_total?: number;
  total_without_waiting?: number;
  waiting_count?: number;
  license_counts?: Record<'FL' | 'APS' | 'ACS', number>;
  unsupported_rows?: string[];
  has_manual_schedule?: boolean;
  manual_sector_hours?: number | null;
  manual_scheduled_person_hours?: number | null;
  model_sector_hours?: number | null;
  model_missing_sector_hours?: number | null;
  model_vs_manual_diff?: number | null;
  model_coverage_percent?: number | null;
  model_planned_people?: number | null;
  model_active_people?: number | null;
  model_utilization_percent?: number | null;
  solver_status?: string | null;
  solver_upper_bound_sector_hours?: number | null;
  solver_gap_to_upper_bound?: number | null;
  manual_similarity_percent?: number | null;
  manual_similarity_sh_diff?: number | null;
  manual_similarity_people_diff?: number | null;
  manual_similarity_license_diff?: Record<'FL' | 'APS' | 'ACS', number> | null;
  manual_similarity_role_hours_diff?: Record<'V1' | 'V2' | 'V3' | 'FMP', number> | null;
  manual_similarity_sector_profile_diff?: number | null;
  manual_similarity_workload_diff?: number | null;
  hourly_comparison?: ManualConfigurationAuditHour[];
  message?: string | null;
  elapsed_seconds?: number | null;
}

export interface ManualConfigurationAudit {
  source_path: string | null;
  workbook_path: string | null;
  time_limit_seconds: number;
  focus_names: string[];
  elapsed_seconds: number;
  rows: ManualConfigurationAuditRow[];
}

export interface ManualAuditSummary {
  elapsed_seconds?: number | null;
  status_counts: Record<string, number>;
  configuration_count: number;
  total_manual_sector_hours: number;
  total_model_sector_hours: number;
  total_missing_sector_hours: number;
  sector_mismatch_hours: number;
  sector_distance: number;
  average_manual_similarity_percent?: number | null;
  per_configuration?: Array<{
    name: string;
    status: string;
    manual_sector_hours: number;
    model_sector_hours: number;
    missing_sector_hours: number;
    manual_similarity_percent?: number | null;
    coverage_percent?: number | null;
    solver_status?: string | null;
  }>;
}

export interface ManualFocusComposition {
  configuration_count: number;
  requested_configuration_count: number;
  total_people: number;
  average_people: number;
  total_control_hours: number;
  average_control_hours: number;
  total_office_people: number;
  total_office_hours: number;
  license_counts: Record<'FL' | 'APS' | 'ACS', number>;
  license_ratio_percent: Record<'FL' | 'APS' | 'ACS', number>;
  shift_mix: Array<{ name: string; count: number; share_percent: number }>;
  role_mix: Array<{ name: string; count: number; share_percent: number }>;
  office_shift_mix: Array<{ name: string; count: number; share_percent: number }>;
  missing?: string[];
  unsupported?: Record<string, string[]>;
}

export interface ManualFocusCalibration {
  focus_names: string[];
  status: 'accepted' | 'rejected' | string;
  success: boolean;
  applied: boolean;
  applied_profiles?: Record<string, string[]> | null;
  hard_constraints_changed: boolean;
  acceptance_rule: string;
  message: string;
  current_missing_sector_hours?: number | null;
  recommended_missing_sector_hours?: number | null;
  composition: ManualFocusComposition;
  sector_profile_calibration: {
    manual_record_count: number;
    current_profiles: Record<string, string[]>;
    recommended_profiles: Record<string, string[]>;
    profile_score_current: {
      exact_match_percent: number;
      profile_mismatch_hours: number;
      sector_distance: number;
    };
    profile_score_recommended: {
      exact_match_percent: number;
      profile_mismatch_hours: number;
      sector_distance: number;
    };
    solver_audit_current?: ManualAuditSummary;
    solver_audit_recommended_profiles?: ManualAuditSummary;
  };
}

export type CalculationJobState = 'queued' | 'running' | 'finished' | 'failed';
export type CalculationJobKind = 'calculation' | 'complete' | 'one_down' | 'pareto' | string;

export interface CalculationJobStart {
  job_id: string;
  status: 'queued';
}

export interface CalculationJobStatus {
  job_id: string;
  kind: CalculationJobKind;
  status: CalculationJobState;
  progress: number;
  message: string;
  elapsed_seconds: number;
  error: string | null;
  cancel_requested: boolean;
  best_result_available: boolean;
  best_result_version: number;
  best_max_sector_hours: number | null;
  best_requested_sector_hours: number | null;
  best_missing_sector_hours: number | null;
  best_planned_people: number | null;
  best_utilization_percent: number | null;
  solver_status: string | null;
  solver_solution_count: number;
  solver_objective_value: number | null;
  solver_best_objective_bound: number | null;
  solver_optimality_gap_percent: number | null;
  solver_stop_reason: string | null;
  solver_best_bound_sector_hours: number | null;
  solver_sector_gap_to_best_bound: number | null;
  calculation_phase: string | null;
  calculation_phase_label: string | null;
  calculation_phase_detail: string | null;
  calculation_next_step: string | null;
  pattern_phase: string | null;
  pattern_current_people_limit: number | null;
  pattern_lower_bound: number | null;
  pattern_upper_bound: number | null;
  pattern_limit_index: number | null;
  pattern_limit_count: number | null;
  pattern_checked_limits: Array<{
    people_limit: number;
    status: string;
    elapsed_seconds: number | null;
  }>;
  pattern_pattern_count: number | null;
  pattern_cache_status: string | null;
  pattern_cache_path: string | null;
  pattern_estimate_low_seconds: number | null;
  pattern_estimate_high_seconds: number | null;
  pattern_proven_minimum: boolean | null;
  warm_start_snapshot_id: string | null;
}

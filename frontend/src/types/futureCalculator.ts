import type { ShiftRule } from './calculator';

export interface FutureCalculatorRequest {
  calculation_mode: 'staff_to_coverage' | 'demand_to_staff';
  total_people: number;
  fl_count: number;
  aps_count: number;
  acs_count: number;
  requested_sector_counts: number[];
  shifts: ShiftRule[];
  min_continuous_work_minutes: number;
  max_continuous_work_minutes: number;
  rest_ratio_percent: number;
  allow_quarter_hour_shift_starts: boolean;
  time_limit_seconds: number;
}

export interface FutureWorkBlock {
  start_slot: number;
  end_slot: number;
  start: string;
  end: string;
  duration_minutes: number;
  required_rest_minutes: number;
}

export interface FuturePersonResult {
  id: string;
  license: 'FL' | 'APS' | 'ACS';
  shift: string;
  worked_minutes: number;
  work_slots: number[];
  blocks: FutureWorkBlock[];
}

export interface FutureSectorAssignment {
  sector_name: string;
  lower_worker: string;
  upper_worker: string;
}

export interface FutureSlotCoverage {
  slot: number;
  time: string;
  requested_sectors: number;
  open_sectors: number;
  workers: string[];
  resting_workers: string[];
  sectors: FutureSectorAssignment[];
}

export interface FutureCalculatorResponse {
  calculation_mode: 'staff_to_coverage' | 'demand_to_staff';
  feasible: boolean;
  solver_status: string;
  solver_stop_reason: string | null;
  elapsed_seconds: number;
  requested_quarter_slots: number;
  covered_quarter_slots: number;
  missing_quarter_slots: number;
  requested_sector_hours: number;
  covered_sector_hours: number;
  missing_sector_hours: number;
  solver_upper_bound_quarter_slots: number | null;
  solver_gap_quarter_slots: number | null;
  planned_people: number;
  available_people: number;
  active_people: number;
  controller_hours: number;
  people: FuturePersonResult[];
  coverage: FutureSlotCoverage[];
  notes: string[];
  warnings: string[];
}

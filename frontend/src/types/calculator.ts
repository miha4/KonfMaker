export interface ShiftRule {
  code: string;
  start_hour: number;
  duration_hours: number;
}

export interface CalculatorSettings {
  max_sectors_per_hour: number;
  max_consecutive_work_hours: number;
  rest_after_max_consecutive_hours: number;
  include_required_shift_leaders: boolean;
  required_night_fl_count: number;
  shifts: ShiftRule[];
}

export interface CalculatorRequest {
  total_people: number;
  fl_count: number;
  aps_count: number;
  acs_count: number;
  include_fmp: boolean;
  settings: CalculatorSettings;
  requested_sector_counts: number[];
}

export interface VirtualPerson {
  id: string;
  license: 'FL' | 'APS' | 'ACS';
  shift: string;
  role: string | null;
  sector_hours: number;
  used_as_sector_controller: boolean;
}

export interface ShiftSummary {
  shift: string;
  fl: number;
  aps: number;
  acs: number;
  total: number;
}

export interface SectorAssignment {
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
  minimum_required_fl: number;
  unused_people: number;
  people: VirtualPerson[];
  shift_summary: ShiftSummary[];
  hourly_coverage: HourlyCoverage[];
  notes: string[];
  warnings: string[];
}

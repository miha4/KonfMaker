export type AirportCode = 'BRN' | 'MBX' | 'POW' | 'CEK';

export interface AirportShiftDefinition {
  code: string;
  start: string;
  end: string;
  break_start: string | null;
  break_end: string | null;
}

export interface AirportDefinition {
  code: AirportCode;
  name: string;
  shifts: AirportShiftDefinition[];
}

export interface AirportCalculatorRequest {
  airport: AirportCode;
  total_people: number;
  opening_start: string;
  opening_end: string;
  calculation_mode: 'opening' | 'selected_shifts';
  fixed_shift_counts: Record<string, number>;
  continuous_24_hours: boolean;
  require_assistant_presence: boolean;
  avoid_split_shifts: boolean;
  explore_opening_extension: boolean;
  time_limit_seconds: number;
}

export interface AirportTimeBlock {
  start_slot: number;
  end_slot: number;
  start: string;
  end: string;
  duration_minutes: number;
}

export interface AirportPersonResult {
  id: string;
  shift: string;
  shift_start: string;
  shift_end: string;
  shift_segments: AirportTimeBlock[];
  presence_minutes: number;
  duty_minutes: number;
  controller_minutes: number;
  presence_slots: number[];
  preparation_slots: number[];
  duty_slots: number[];
  controller_slots: number[];
  assistant_slots: number[];
  duty_blocks: AirportTimeBlock[];
  controller_blocks: AirportTimeBlock[];
  break_blocks: AirportTimeBlock[];
}

export interface AirportSlotResult {
  slot: number;
  start: string;
  end: string;
  is_open: boolean;
  is_covered: boolean;
  controller_id: string | null;
  assistant_id: string | null;
  present_workers: string[];
  duty_workers: string[];
  break_workers: string[];
}

export interface AirportOpeningExtension {
  suggested_start: string;
  suggested_end: string;
  before_minutes: number;
  after_minutes: number;
  total_minutes: number;
}

export interface AirportScheduleVariant {
  opening_start: string;
  opening_end: string;
  opening_minutes: number;
  active_people: number;
  handovers: number;
  solver_status: string;
  elapsed_seconds: number;
  people: AirportPersonResult[];
  coverage: AirportSlotResult[];
}

export interface AirportCalculatorResponse {
  airport: AirportCode;
  airport_name: string;
  feasible: boolean;
  solver_status: string;
  elapsed_seconds: number;
  opening_start: string;
  opening_end: string;
  calculation_mode: 'opening' | 'selected_shifts';
  continuous_24_hours: boolean;
  require_assistant_presence: boolean;
  avoid_split_shifts: boolean;
  explore_opening_extension: boolean;
  opening_extension: AirportOpeningExtension | null;
  extended_variant: AirportScheduleVariant | null;
  requested_minutes: number;
  covered_minutes: number;
  missing_minutes: number;
  available_people: number;
  active_people: number;
  handovers: number;
  people: AirportPersonResult[];
  coverage: AirportSlotResult[];
  notes: string[];
  warnings: string[];
}

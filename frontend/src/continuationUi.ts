import type {
  CalculationJobStatus,
  CalculatorRequest,
  CalculatorResponse,
  CalculatorSettings,
  ShiftRule,
  VirtualPerson,
} from './types/calculator';

export type ContinuationDelta = {
  sectorHours: number;
  plannedPeople: number;
  crisisHours: number;
  utilizationPercent: number;
};

export type ShiftWhatIfChange = {
  person: VirtualPerson;
  shift: string;
};

export const FULL_WHAT_IF_TIME_LIMIT_SECONDS = 600;

export function fullWhatIfSolverSettings(settings: CalculatorSettings): CalculatorSettings {
  return {
    ...settings,
    cp_sat_time_limit_seconds: FULL_WHAT_IF_TIME_LIMIT_SECONDS,
    cp_sat_no_improvement_seconds: 0,
  };
}

export function applyCurrentFmpSelection(
  request: CalculatorRequest,
  includeFmp: boolean,
): CalculatorRequest {
  if (includeFmp) {
    return { ...request, include_fmp: true };
  }
  const isFmpRole = (role: string | null | undefined) => (role ?? '').trim().toUpperCase() === 'FMP';
  return {
    ...request,
    include_fmp: false,
    fixed_staff: request.fixed_staff.filter((person) => !isFmpRole(person.role)),
    locked_staff: request.locked_staff.filter((person) => !isFmpRole(person.role)),
  };
}

export function increasedLeaderSectorLimits(
  v1SectorLimit: number,
  v2SectorLimit: number,
): { v1SectorLimit: number; v2SectorLimit: number } {
  return {
    v1SectorLimit: Math.min(24, Math.max(0, v1SectorLimit) + 1),
    v2SectorLimit: Math.min(24, Math.max(0, v2SectorLimit) + 1),
  };
}

export function selectedShiftWhatIfChanges(
  people: VirtualPerson[],
  selectedShiftByPerson: Record<string, string>,
): ShiftWhatIfChange[] {
  return people.flatMap((person) => {
    const shift = selectedShiftByPerson[person.id];
    return shift && shift !== person.shift ? [{ person, shift }] : [];
  });
}

export function coverageIsProven(status: CalculationJobStatus): boolean {
  return status.solver_status === 'OPTIMAL' || status.solver_sector_gap_to_best_bound === 0;
}

export function stoppedOnTimeLimit(status: CalculationJobStatus): boolean {
  return (status.solver_stop_reason ?? '').includes('časovni limit');
}

export function continuationDelta(
  baseline: CalculatorResponse,
  candidate: CalculatorResponse,
): ContinuationDelta {
  return {
    sectorHours: candidate.max_sector_hours - baseline.max_sector_hours,
    plannedPeople: candidate.planned_people - baseline.planned_people,
    crisisHours: candidate.crisis_exception_hours - baseline.crisis_exception_hours,
    utilizationPercent: candidate.utilization_percent - baseline.utilization_percent,
  };
}

export function resultUsesOffice(result: CalculatorResponse): boolean {
  return result.people.some(
    (person: VirtualPerson) => ['officer', 'office-pool'].includes(person.source) && person.sector_hours > 0,
  );
}

export function nextPeopleLimit(
  request: CalculatorRequest,
  result: CalculatorResponse,
): number | null {
  if (request.calculation_mode !== 'demand_to_staff') {
    return null;
  }
  const currentLimit = Math.max(request.total_people, result.planned_people);
  return currentLimit >= 80 ? null : Math.max(1, currentLimit + 1);
}

export function enablesOnlyAdditionalRegularShifts(
  previous: CalculatorRequest,
  next: CalculatorRequest,
): boolean {
  if (previous.settings.shifts.length !== next.settings.shifts.length) {
    return false;
  }

  let enabledAnotherShift = false;
  for (const previousShift of previous.settings.shifts) {
    const nextShift = next.settings.shifts.find((shift) => shift.code === previousShift.code);
    if (
      !nextShift
      || nextShift.start_hour !== previousShift.start_hour
      || nextShift.duration_hours !== previousShift.duration_hours
    ) {
      return false;
    }
    const wasEnabled = previousShift.enabled !== false;
    const isEnabled = nextShift.enabled !== false;
    if (wasEnabled && !isEnabled) {
      return false;
    }
    if (!wasEnabled && isEnabled) {
      enabledAnotherShift = true;
    }
  }
  if (!enabledAnotherShift) {
    return false;
  }

  const comparable = (request: CalculatorRequest) => ({
    ...request,
    continuation_min_sector_hours: null,
    solver_random_seed: 1,
    warm_start: null,
    warm_start_snapshot_id: null,
    settings: {
      ...request.settings,
      shifts: request.settings.shifts.map((shift) => ({ ...shift, enabled: true })),
    },
  });
  return JSON.stringify(comparable(previous)) === JSON.stringify(comparable(next));
}

function slotHour(slot: number): number {
  return (7 + slot) % 24;
}

function isNightHour(hour: number): boolean {
  return hour >= 21 || hour < 7;
}

export function suggestedOfficeLicense(
  request: CalculatorRequest,
  result: CalculatorResponse,
): { license: 'FL' | 'APS' | 'ACS'; reason: string } {
  const missingSlots = request.requested_sector_counts
    .map((requested, slot) => ({
      missing: Math.max(0, requested - (result.hourly_coverage[slot]?.open_sectors ?? 0)),
      slot,
    }))
    .filter((item) => item.missing > 0);

  if (missingSlots.some((item) => isNightHour(slotHour(item.slot)))) {
    return {
      license: 'FL',
      reason: 'Med manjkajočimi urami je nočna ura, zato je FL najvarnejši prvi preizkus.',
    };
  }
  return {
    license: 'FL',
    reason: 'FL lahko zapolni obe sektorski poziciji, zato je najbolj prilagodljiv prvi preizkus.',
  };
}

function shiftSlots(shift: ShiftRule): number[] {
  return Array.from(
    { length: shift.duration_hours },
    (_, offset) => (shift.start_hour + offset - 7 + 24) % 24,
  );
}

export function suggestedEmergencyShift(
  request: CalculatorRequest,
  result: CalculatorResponse,
): ShiftRule | null {
  const disabledShifts = request.settings.shifts.filter((shift) => shift.enabled === false);
  if (disabledShifts.length === 0) {
    return null;
  }

  const missingBySlot = request.requested_sector_counts.map(
    (requested, slot) => Math.max(0, requested - (result.hourly_coverage[slot]?.open_sectors ?? 0)),
  );
  const ranked = disabledShifts
    .map((shift) => ({
      shift,
      score: shiftSlots(shift).reduce((sum, slot) => sum + missingBySlot[slot], 0),
    }))
    .sort((first, second) => second.score - first.score || first.shift.start_hour - second.shift.start_hour);
  return ranked[0]?.score > 0 ? ranked[0].shift : null;
}

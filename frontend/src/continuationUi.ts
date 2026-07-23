import type {
  CalculationJobStatus,
  CalculatorRequest,
  CalculatorResponse,
  ShiftRule,
  VirtualPerson,
} from './types/calculator';

export type ContinuationDelta = {
  sectorHours: number;
  plannedPeople: number;
  crisisHours: number;
  utilizationPercent: number;
};

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

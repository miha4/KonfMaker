import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyCurrentFmpSelection,
  continuationDelta,
  coverageIsProven,
  enablesOnlyAdditionalRegularShifts,
  fullWhatIfSolverSettings,
  increasedLeaderSectorLimits,
  nextPeopleLimit,
  selectedShiftWhatIfChanges,
  stoppedOnTimeLimit,
  suggestedEmergencyShift,
  suggestedOfficeLicense,
} from '../src/continuationUi.ts';

const baseResult = {
  max_sector_hours: 121,
  planned_people: 23,
  crisis_exception_hours: 0,
  utilization_percent: 80,
  hourly_coverage: Array.from({ length: 24 }, (_, slot) => ({ open_sectors: slot === 5 || slot === 14 ? 2 : 3 })),
  people: [],
};

const baseRequest = {
  requested_sector_counts: Array.from({ length: 24 }, () => 3),
  settings: {
    shifts: [
      { code: 'A12', start_hour: 12, duration_hours: 8, enabled: false },
      { code: 'A21', start_hour: 21, duration_hours: 10, enabled: true },
    ],
  },
};

test('recognizes proven coverage and time-limit stops', () => {
  assert.equal(coverageIsProven({ solver_status: 'OPTIMAL', solver_sector_gap_to_best_bound: 2 }), true);
  assert.equal(coverageIsProven({ solver_status: 'FEASIBLE', solver_sector_gap_to_best_bound: 0 }), true);
  assert.equal(coverageIsProven({ solver_status: 'FEASIBLE', solver_sector_gap_to_best_bound: 1 }), false);
  assert.equal(stoppedOnTimeLimit({ solver_stop_reason: 'skupni časovni limit izračuna se je iztekel' }), true);
});

test('compares a continuation against the preserved result', () => {
  const delta = continuationDelta(
    baseResult,
    { ...baseResult, max_sector_hours: 123, planned_people: 24, crisis_exception_hours: 1, utilization_percent: 82 },
  );
  assert.deepEqual(delta, {
    sectorHours: 2,
    plannedPeople: 1,
    crisisHours: 1,
    utilizationPercent: 2,
  });
});

test('recommends FL for a missing night hour and a disabled shift that overlaps a gap', () => {
  assert.equal(suggestedOfficeLicense(baseRequest, baseResult).license, 'FL');
  assert.equal(suggestedEmergencyShift(baseRequest, baseResult)?.code, 'A12');
});

test('offers one more person above the active demand-mode limit', () => {
  assert.equal(
    nextPeopleLimit(
      { ...baseRequest, calculation_mode: 'demand_to_staff', total_people: 28 },
      { ...baseResult, planned_people: 28 },
    ),
    29,
  );
  assert.equal(
    nextPeopleLimit(
      { ...baseRequest, calculation_mode: 'staff_to_coverage', total_people: 28 },
      { ...baseResult, planned_people: 28 },
    ),
    null,
  );
});

test('increases both VI sector limits by one without exceeding 24', () => {
  assert.deepEqual(increasedLeaderSectorLimits(2, 0), { v1SectorLimit: 3, v2SectorLimit: 1 });
  assert.deepEqual(increasedLeaderSectorLimits(24, 24), { v1SectorLimit: 24, v2SectorLimit: 24 });
});

test('starts a what-if as a full 600-second solve without early no-improvement stop', () => {
  const settings = {
    cp_sat_time_limit_seconds: 120,
    cp_sat_no_improvement_seconds: 30,
  };

  assert.deepEqual(fullWhatIfSolverSettings(settings), {
    cp_sat_time_limit_seconds: 600,
    cp_sat_no_improvement_seconds: 0,
  });
});

test('current unchecked FMP removes stale fixed and locked FMP rows', () => {
  const request = {
    ...baseRequest,
    include_fmp: true,
    fixed_staff: [
      { count: 1, license: 'FL', shift: 'A9', role: 'FMP' },
      { count: 1, license: 'FL', shift: 'A7', role: 'V1' },
    ],
    locked_staff: [
      { count: 1, license: 'FL', shift: 'A9', role: 'FMP', label: 'FMP' },
      { count: 1, license: 'ACS', shift: 'A14', role: null, label: 'A' },
    ],
  };

  const withoutFmp = applyCurrentFmpSelection(request, false);

  assert.equal(withoutFmp.include_fmp, false);
  assert.deepEqual(withoutFmp.fixed_staff, [request.fixed_staff[1]]);
  assert.deepEqual(withoutFmp.locked_staff, [request.locked_staff[1]]);
});

test('collects several staged shift what-if changes', () => {
  const people = [
    { id: 'A', shift: 'A7' },
    { id: 'B', shift: 'A14' },
    { id: 'C', shift: 'A21' },
  ];

  assert.deepEqual(
    selectedShiftWhatIfChanges(people, { A: 'A8', B: 'A15', C: 'A21' }),
    [
      { person: people[0], shift: 'A8' },
      { person: people[1], shift: 'A15' },
    ],
  );
});

test('recognizes a pure relaxation that only enables more regular shifts', () => {
  const previous = {
    ...baseRequest,
    calculation_mode: 'demand_to_staff',
    total_people: 28,
    settings: {
      ...baseRequest.settings,
      shifts: [
        { code: 'A7', start_hour: 7, duration_hours: 7, enabled: false },
        { code: 'A14', start_hour: 14, duration_hours: 7, enabled: false },
      ],
    },
  };
  const next = {
    ...previous,
    settings: {
      ...previous.settings,
      shifts: previous.settings.shifts.map((shift) => ({ ...shift, enabled: true })),
    },
  };

  assert.equal(enablesOnlyAdditionalRegularShifts(previous, next), true);
  assert.equal(
    enablesOnlyAdditionalRegularShifts(previous, { ...next, total_people: 29 }),
    false,
  );
});

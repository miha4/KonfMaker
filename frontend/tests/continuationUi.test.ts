import assert from 'node:assert/strict';
import test from 'node:test';

import {
  continuationDelta,
  coverageIsProven,
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

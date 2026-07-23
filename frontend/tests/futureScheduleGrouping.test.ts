import assert from 'node:assert/strict';
import test from 'node:test';

import {
  groupFutureCoverage,
  scheduleRowsForGroups,
} from '../src/futureScheduleGrouping.ts';
import type { FutureSlotCoverage } from '../src/types/futureCalculator.ts';


function coverageSlot(
  slot: number,
  lowerWorker = 'FL1',
  upperWorker = 'FL2',
  restingWorkers: string[] = ['FL3'],
): FutureSlotCoverage {
  return {
    slot,
    time: 'unused',
    requested_sectors: 1,
    open_sectors: 1,
    workers: [lowerWorker, upperWorker],
    resting_workers: restingWorkers,
    sectors: [{
      sector_name: 'ALL',
      lower_worker: lowerWorker,
      upper_worker: upperWorker,
    }],
  };
}

test('merges consecutive quarters with the same seats and break list', () => {
  const groups = groupFutureCoverage([
    coverageSlot(0),
    coverageSlot(1),
    coverageSlot(2),
    coverageSlot(3),
  ]);

  assert.equal(groups.length, 1);
  const rows = scheduleRowsForGroups(groups, new Set());
  assert.equal(rows.length, 1);
  assert.equal(rows[0].label, '07:00-08:00');
  assert.equal(rows[0].groupSize, 4);
  assert.equal(rows[0].showToggle, true);
});

test('starts a new group when a seat or break assignment changes', () => {
  const groups = groupFutureCoverage([
    coverageSlot(0),
    coverageSlot(1, 'FL1', 'FL4'),
    coverageSlot(2, 'FL1', 'FL4', ['FL5']),
  ]);

  assert.equal(groups.length, 3);
});

test('expands a merged interval back into individual quarters', () => {
  const groups = groupFutureCoverage([coverageSlot(0), coverageSlot(1), coverageSlot(2)]);
  const rows = scheduleRowsForGroups(groups, new Set([groups[0].key]));

  assert.deepEqual(rows.map((row) => row.label), [
    '07:00-07:15',
    '07:15-07:30',
    '07:30-07:45',
  ]);
  assert.equal(rows[0].showToggle, true);
  assert.equal(rows[1].showToggle, false);
  assert.equal(rows.every((row) => row.expanded), true);
});

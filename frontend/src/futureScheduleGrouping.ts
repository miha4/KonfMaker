import type { FutureSlotCoverage } from './types/futureCalculator';


const DAY_START_HOUR = 7;

export interface FutureScheduleGroup {
  key: string;
  signature: string;
  slots: FutureSlotCoverage[];
}

export interface FutureScheduleRow {
  key: string;
  groupKey: string;
  slot: FutureSlotCoverage;
  label: string;
  groupLabel: string;
  groupSize: number;
  showToggle: boolean;
  expanded: boolean;
}

export function quarterBoundaryLabel(slot: number): string {
  const startMinutes = (DAY_START_HOUR * 60 + slot * 15) % (24 * 60);
  return `${String(Math.floor(startMinutes / 60)).padStart(2, '0')}:${String(startMinutes % 60).padStart(2, '0')}`;
}

export function quarterIntervalLabel(slot: number): string {
  return `${quarterBoundaryLabel(slot)}-${quarterBoundaryLabel(slot + 1)}`;
}

export function futureScheduleSignature(slot: FutureSlotCoverage): string {
  return JSON.stringify([
    slot.sectors.map((sector) => [sector.sector_name, sector.lower_worker, sector.upper_worker]),
    [...slot.resting_workers].sort(),
  ]);
}

export function groupFutureCoverage(coverage: FutureSlotCoverage[]): FutureScheduleGroup[] {
  const groups: FutureScheduleGroup[] = [];
  coverage.forEach((slot) => {
    const signature = futureScheduleSignature(slot);
    const previousGroup = groups.at(-1);
    const previousSlot = previousGroup?.slots.at(-1);
    if (previousGroup && previousSlot && previousGroup.signature === signature && slot.slot === previousSlot.slot + 1) {
      previousGroup.slots.push(slot);
      return;
    }
    groups.push({ key: `slots-${slot.slot}`, signature, slots: [slot] });
  });
  return groups;
}

export function scheduleRowsForGroups(
  groups: FutureScheduleGroup[],
  expandedGroups: Set<string>,
): FutureScheduleRow[] {
  return groups.flatMap<FutureScheduleRow>((group) => {
    const expanded = group.slots.length > 1 && expandedGroups.has(group.key);
    if (!expanded) {
      const firstSlot = group.slots[0];
      const lastSlot = group.slots[group.slots.length - 1];
      const groupLabel = group.slots.length > 1
        ? `${quarterBoundaryLabel(firstSlot.slot)}-${quarterBoundaryLabel(lastSlot.slot + 1)}`
        : quarterIntervalLabel(firstSlot.slot);
      return [{
        key: group.key,
        groupKey: group.key,
        slot: firstSlot,
        label: groupLabel,
        groupLabel,
        groupSize: group.slots.length,
        showToggle: group.slots.length > 1,
        expanded: false,
      }];
    }
    const groupLabel = `${quarterBoundaryLabel(group.slots[0].slot)}-${quarterBoundaryLabel(group.slots.at(-1)!.slot + 1)}`;
    return group.slots.map((slot, index) => ({
      key: `${group.key}-${slot.slot}`,
      groupKey: group.key,
      slot,
      label: quarterIntervalLabel(slot.slot),
      groupLabel,
      groupSize: group.slots.length,
      showToggle: index === 0,
      expanded: true,
    }));
  });
}

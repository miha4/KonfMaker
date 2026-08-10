import { Fragment, type ChangeEvent, type DragEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  calibrateManualConfigurationFocus,
  cancelCalculationJob,
  compareResultToConfigurations,
  deleteManualConfiguration,
  exportCalculatorWorkbook,
  getCalculationJob,
  getCalculationJobs,
  getCalculationJobResult,
  getParetoJobResult,
  getDefaultSettings,
  getManualConfiguration,
  getManualConfigurationFocusAudit,
  getManualConfigurations,
  inspectPatternLibrary,
  regeneratePatternLibrary,
  saveUserConfiguration,
  startCalculationJob,
  startCompleteConfigurationJob,
  startManualConfigurationOneDownJob,
  startParetoJob,
  updateUserConfiguration,
} from './api/calculator';
import ModelAnalysis from './ModelAnalysis';
import FutureCalculator from './FutureCalculator';
import AirportCalculator from './AirportCalculator';
import {
  cancellationDetail as cancellationDetailFor,
  isCancellationInProgress,
  type CalculationCancelAction,
} from './calculationUi';
import {
  applyCurrentFmpSelection,
  continuationDelta,
  coverageIsProven,
  enablesOnlyAdditionalRegularShifts,
  FULL_WHAT_IF_TIME_LIMIT_SECONDS,
  fullWhatIfSolverSettings,
  increasedLeaderSectorLimits,
  nextPeopleLimit,
  resultUsesOffice,
  selectedShiftWhatIfChanges,
  stoppedOnTimeLimit,
  suggestedEmergencyShift,
  suggestedOfficeLicense,
  type ShiftWhatIfChange,
} from './continuationUi';
import { preventNumberInputArrowStep } from './numberInput';
import type {
  CalculationJobStart,
  CalculationJobStatus,
  CalculatorRequest,
  CalculatorResponse,
  CalculatorSettings,
  ConfigurationComparisonResult,
  ConfigurationSimilarityMatch,
  FixedStaffRule,
  HourlyCoverage,
  LockedStaffRule,
  ManualConfigurationAudit,
  ManualConfigurationAuditRow,
  ManualConfigurationDetail,
  ManualConfigurationLibrary,
  ManualConfigurationSchedule,
  ManualConfigurationStaffRow,
  ManualConfigurationSummary,
  ManualFocusCalibration,
  OfficePoolRule,
  OfficerStaffRule,
  ParetoPoint,
  ParetoResponse,
  PatternLibraryProfile,
  SectorAssignment,
  ShiftRule,
  VirtualPerson,
} from './types/calculator';

const DEFAULT_FOCUS_CONFIGURATION_NAMES = [
  '21z4',
  '20z4',
  '22z4',
  '23z4',
  '23n4',
  '24n4',
  '25n5x',
  '26f4',
  '26f4x',
  '27n5',
  '27s5',
  '28s5',
];

const FOCUS_STORAGE_KEY = 'konfmaker.focusConfigurationNames';
const COMPLETE_CONFIGURATION_TIME_LIMIT_SECONDS = 180;

const fallbackSettings: CalculatorSettings = {
  max_sectors_per_hour: 5,
  max_consecutive_work_hours: 2,
  rest_after_max_consecutive_hours: 1,
  cp_sat_time_limit_seconds: 600,
  cp_sat_no_improvement_seconds: 180,
  cp_sat_acceptable_sector_gap: 0,
  cp_sat_min_auto_stop_coverage_percent: 95,
  coverage_priority: 100,
  license_mix_priority: 25,
  short_shift_priority: 100,
  include_required_shift_leaders: true,
  include_night_fl_requirement: true,
  required_night_fl_count: 4,
  v1_sector_limit: 1,
  v2_sector_limit: 1,
  v3_sector_limit: 4,
  fmp_sector_limit: 6,
  shifts: [
    { code: 'A6', start_hour: 6, duration_hours: 8, enabled: true },
    { code: 'A7', start_hour: 7, duration_hours: 7, enabled: true },
    { code: 'A8', start_hour: 8, duration_hours: 8, enabled: true },
    { code: 'A9', start_hour: 9, duration_hours: 8, enabled: true },
    { code: 'A10', start_hour: 10, duration_hours: 8, enabled: true },
    { code: 'A11', start_hour: 11, duration_hours: 8, enabled: true },
    { code: 'A12', start_hour: 12, duration_hours: 8, enabled: true },
    { code: 'A13', start_hour: 13, duration_hours: 8, enabled: true },
    { code: 'A14', start_hour: 14, duration_hours: 7, enabled: true },
    { code: 'A15', start_hour: 15, duration_hours: 8, enabled: true },
    { code: 'A16', start_hour: 16, duration_hours: 8, enabled: true },
    { code: 'A17', start_hour: 17, duration_hours: 8, enabled: true },
    { code: 'A21', start_hour: 21, duration_hours: 10, enabled: true },
  ],
  officer_shifts: [
    { code: 'A6o', start_hour: 6, duration_hours: 8, enabled: true },
    { code: 'A7o', start_hour: 7, duration_hours: 8, enabled: true },
    { code: 'A8o', start_hour: 8, duration_hours: 8, enabled: true },
    { code: 'A9o', start_hour: 9, duration_hours: 8, enabled: true },
    { code: 'A10o', start_hour: 10, duration_hours: 8, enabled: true },
    { code: 'A11o', start_hour: 11, duration_hours: 8, enabled: true },
    { code: 'A14o', start_hour: 14, duration_hours: 7, enabled: true },
  ],
};

const savedSettingsStorageKey = 'konfmaker.calculatorSettings.v1';
const savedCalculatorInputsStorageKey = 'konfmaker.calculatorInputs.v1';
const onboardingCompletedStorageKey = 'konfmaker.onboardingCompleted.v1';

type Tab = 'calculator' | 'future' | 'airport' | 'settings' | 'manual-configs' | 'comparison' | 'analysis' | 'theory';
type CalculationMode = 'staff_to_coverage' | 'demand_to_staff';
type GuidedTourKind = 'new-configuration' | 'manual-configuration';
type GuidedTourStep = {
  tab: Tab;
  target: string;
  title: string;
  description: string;
  advanceOnTargetClick?: boolean;
  finishOnTargetClick?: boolean;
};
type PollingTimer = ReturnType<typeof window.setInterval>;
type SectorDemandInterval = {
  id: number;
  sectorCount: number;
  startHour: number;
  endHour: number;
};
type SectorDemandQueueItem = {
  id: number;
  label: string;
  values: number[];
};
type FixedStaffRow = FixedStaffRule & { id: number; role: string };
type OfficerStaffRow = {
  shift: string;
  fl: number;
  aps: number;
  acs: number;
};
type OfficePool = {
  fl: number;
  aps: number;
  acs: number;
};
type OfficeFallbackMode = 'auto' | 'fixed';
type OfficeFallbackSelection = {
  mode: OfficeFallbackMode;
  pool: OfficePool;
  shift?: string;
};
type FmpShiftMode = 'auto' | 'fixed';
type SavedCalculatorInputs = Partial<{
  calculationMode: CalculationMode;
  totalPeople: number;
  flCount: number;
  apsCount: number;
  preferMinimalFl: boolean;
  usePeopleLimit: boolean;
  minimumLicenseRatio: { fl: number; aps: number; acs: number };
  sectorDemand: number[];
  staffSectorLimits: Array<number | null>;
  baseSectors: number;
  sectorIntervals: SectorDemandInterval[];
  fixedStaff: FixedStaffRow[];
  officerStaff: OfficerStaffRow[];
  officePool: OfficePool;
  includeFmp: boolean;
  fmpShiftMode: FmpShiftMode;
  fmpShift: string;
}>;
type JobRestartPlan =
  | {
      kind: 'calculator';
      payload: CalculatorRequest;
    }
  | {
      kind: 'complete';
      payload: CalculatorRequest;
      currentResult: CalculatorResponse;
      timeLimitSeconds: number;
    }
  | {
      kind: 'one-down';
      configurationId: string;
      configurationName: string;
      timeLimitSeconds: number;
      settings: CalculatorSettings;
      peopleBefore: number;
      peopleAfter: number;
    };
type TimeLimitDecision = {
  status: CalculationJobStatus;
  restartPlan: JobRestartPlan;
  currentResult: CalculatorResponse | null;
  reason: 'time-limit' | 'manual-stop';
};
type ContinuationComparison = {
  actionLabel: string;
  baseline: CalculatorResponse;
  restartPlan: JobRestartPlan;
};
type WorkerColor = {
  background: string;
  border: string;
  text: string;
};
type License = VirtualPerson['license'];
type ScheduleSeat = 'lower' | 'upper';
type ScheduleSeatRef = {
  slot: number;
  sectorIndex: number;
  seat: ScheduleSeat;
};

function TrashIcon() {
  return (
    <svg aria-hidden="true" className="trash-icon" focusable="false" viewBox="0 0 24 24">
      <path d="M9 4h6l1 2h4v2H4V6h4l1-2Z" />
      <path d="M7 10h2v8H7v-8Zm4 0h2v8h-2v-8Zm4 0h2v8h-2v-8Z" />
      <path d="M6 8h12l-1 13H7L6 8Zm2.2 2 .7 9h6.2l.7-9H8.2Z" />
    </svg>
  );
}

function TheoryPanel() {
  return (
    <div className="theory-page">
      <section className="panel theory-hero-panel">
        <p className="eyebrow">Teorija modela</p>
        <h2>Kako model napove odprtost sektorjev</h2>
        <p>
          Model ima dva glavna vira: zgodovinska urna odprtja sektorjev in dnevne prelete.
          Preleti določijo, koliko sektorskih ur naj ima dan, zgodovinska odprtja pa določijo,
          kako se te sektorske ure porazdelijo po urah.
        </p>
      </section>

      <section className="panel theory-panel">
        <p className="eyebrow">Zaporedje</p>
        <h2>Izračun po korakih</h2>
        <ol className="theory-step-list">
          <li className="theory-step">
            <h3>Napoved preletov</h3>
            <p>
              Za prihodnje datume model vzame primerljive prelete iz prejšnjega leta ali več preteklih let
              z utežmi. Nato jih poveča z vpisano rastjo prometa.
            </p>
            <code>preleti_26(d) = preleti_ref(d) * (1 + rast)</code>
          </li>

          <li className="theory-step">
            <h3>Prometni cilj</h3>
            <p>
              Iz preletov izračuna dnevni cilj sektorskih ur. Intercept pomeni osnovo dneva,
              ko je preletov zelo malo, koeficient pa pove, koliko dodatnih sektorskih ur doda en prelet.
            </p>
            <code>T(d) = max(min SH, intercept + beta * preleti(d) + popravek_dneva)</code>
          </li>

          <li className="theory-step">
            <h3>Zgodovinski urni profil</h3>
            <p>
              Za isti ISO teden, dan v tednu in uro vzame pretekla odprtja sektorjev. Leta imajo uteži,
              zato je lahko npr. 2025 pomembnejše od 2023.
            </p>
            <code>P(w,d,h) = tehtano povprecje zgodovinskih odprtij</code>
          </li>

          <li className="theory-step">
            <h3>Razteg profila</h3>
            <p>
              Dnevni profil se raztegne tako, da njegova vsota doseže prometni cilj. Nočne ure se obravnavajo
              posebej, ker je tam praviloma odprt en sektor.
            </p>
            <code>B = sum(P), k = (T(d) - nocni_dodatek) / B, Z(h) = P(h) * k</code>
          </li>

          <li className="theory-step">
            <h3>Pretvorba v sektorje</h3>
            <p>
              Vrednost Z se primerja s pragovi sektorjev. Če Z preseže prag za 4S, ura dobi vsaj štiri sektorje.
              Pragove model lahko poišče sam z optimizacijo.
            </p>
            <code>sektorji(h) = stevilo pragov, kjer je Z(h) &gt;= meja</code>
          </li>

          <li className="theory-step">
            <h3>Popravki in safety</h3>
            <p>
              Obremenitveni popravek preveri gostoto preletov na sektorsko uro glede na referenčno leto.
              Planerski safety nato končni dnevni seštevek dvigne za izbran odstotek.
            </p>
            <code>final SH = model SH * (1 + planerski safety)</code>
          </li>
        </ol>
      </section>

      <section className="panel theory-panel">
        <p className="eyebrow">Primeri</p>
        <h2>Dva kratka izračuna</h2>
        <div className="theory-example-list">
          <div className="theory-example">
            <h3>Primer 1: prometni cilj dneva</h3>
            <p>
              Če ima primerljiv dan v prejšnjem letu 900 preletov in je rast 11,1 %, dobi model
              približno 1.000 preletov za napovedani dan.
            </p>
            <code>preleti_26 = 900 * 1,111 = 1.000</code>
            <code>T(d) = max(24, 20 + 0,025 * 1.000 - 2,6) = 42,4 SH</code>
          </div>

          <div className="theory-example">
            <h3>Primer 2: ena ura v sektorje</h3>
            <p>
              Če ima zgodovinski profil ob 12:00 vrednost 3,2 sektorja, dnevni profil B = 55 SH,
              prometni cilj pa 64 SH z nočnim dodatkom 5 SH, se profil najprej raztegne.
            </p>
            <code>k = (64 - 5) / 55 = 1,073</code>
            <code>Z(12:00) = 3,2 * 1,073 = 3,43</code>
            <p>
              Pri pragovih 0,5 / 1,35 / 2,5 / 3,65 / 3,9 vrednost 3,43 preseže prve tri pragove,
              četrtega pa ne, zato ura dobi 3 odprte sektorje.
            </p>
          </div>
        </div>
      </section>

      <section className="panel theory-panel">
        <p className="eyebrow">Fit</p>
        <h2>Kaj optimizacija išče</h2>
        <div className="theory-content">
          <p>
            CP-SAT oziroma grid iskanje išče koeficiente, dnevne popravke in pragove sektorjev, ki dajo najmanjše
            odstopanje od realno znanih odprtij. Pri tem se preverita obstoječi del ciljnega leta in analogno obdobje leto prej.
          </p>
          <p>
            Če uporabnik v advanced delu zaklene parametre, model uporabi ročne vrednosti. Če niso zaklenjeni,
            se uporabijo statistično najbolje fitane vrednosti.
          </p>
        </div>
      </section>

      <section className="panel theory-panel">
        <p className="eyebrow">Output</p>
        <h2>Kaj dobi uporabnik</h2>
        <div className="theory-content">
          <p>
            Glavni output je matrika dni in ur: vsaka celica pove, koliko sektorjev naj bo odprtih v določeni uri.
            Iz te matrike se lahko posamezen dan ali celotno obdobje pošlje v queue kalkulatorja konfiguracij.
          </p>
          <p>
            Izvoz v Excel vsebuje iste ključne elemente: uporabljene parametre, dnevno napoved, urni urnik, fit metrike
            in teorijo modela.
          </p>
        </div>
      </section>
    </div>
  );
}

const DAY_START = 7;
const HOURS_IN_DAY = 24;
const sectorColumnLabels = ['ALL', 'LOWER', 'UPPER', 'MID', 'HIGH', 'TOP'];
const FMP_AUTO_SHIFT_CODES = ['A7', 'A8', 'A9', 'A10', 'A11'];
const DEFAULT_FMP_SHIFT = 'A9';
const FMP_BLOCKED_SHIFT_CODES = new Set(['A17', 'A21']);
const distinctWorkerColors: WorkerColor[] = [
  { background: '#ff9f91', border: '#c83f35', text: '#4f110d' },
  { background: '#b7d8ff', border: '#4c8fdb', text: '#123f73' },
  { background: '#b9e8c6', border: '#4ca869', text: '#124b25' },
  { background: '#ffe08a', border: '#c28c13', text: '#604000' },
  { background: '#ceb9ff', border: '#8061d6', text: '#38216f' },
  { background: '#55d6c5', border: '#138c7f', text: '#053f39' },
  { background: '#ffb47d', border: '#ce6f22', text: '#69310b' },
  { background: '#d6e98d', border: '#8ea827', text: '#3d4b0c' },
  { background: '#f3afd7', border: '#c65793', text: '#681b47' },
  { background: '#9fc7ff', border: '#477ecf', text: '#173d76' },
  { background: '#c7c0a6', border: '#85795c', text: '#403723' },
  { background: '#a6e4a0', border: '#47a43f', text: '#175411' },
  { background: '#ffcbc5', border: '#d37268', text: '#742820' },
  { background: '#c9a8ff', border: '#7b55d0', text: '#2f166f' },
  { background: '#e1bd7f', border: '#ae7923', text: '#553707' },
  { background: '#c6b4d9', border: '#8060a2', text: '#42275d' },
  { background: '#b4e2c9', border: '#51a674', text: '#155437' },
  { background: '#f0c08f', border: '#bb7632', text: '#5d310d' },
  { background: '#b8cfeb', border: '#5a89bd', text: '#1f4a77' },
  { background: '#e8b5bf', border: '#b65869', text: '#64202d' },
  { background: '#c9df9c', border: '#849d38', text: '#384714' },
  { background: '#b2d8c8', border: '#53957b', text: '#1b4e3d' },
  { background: '#f5d17d', border: '#bd8e1c', text: '#5a3c00' },
  { background: '#bcbcec', border: '#6668bd', text: '#2a2d73' },
  { background: '#ffbd9c', border: '#c86d3d', text: '#663012' },
  { background: '#a9ded4', border: '#429d91', text: '#0f5148' },
  { background: '#e8b0ee', border: '#ae55bb', text: '#5b1c65' },
  { background: '#b4d490', border: '#719a3b', text: '#2f4a12' },
  { background: '#f0b1a0', border: '#c45e46', text: '#681d11' },
  { background: '#9ed7f0', border: '#3e94bd', text: '#124d66' },
  { background: '#d9c493', border: '#9b7732', text: '#4b3510' },
  { background: '#b8c8a4', border: '#6d8950', text: '#2f441e' },
  { background: '#ddaed0', border: '#a34f86', text: '#532143' },
  { background: '#a7d6ab', border: '#4d9755', text: '#194f1e' },
  { background: '#c8b597', border: '#8b6c3f', text: '#463116' },
  { background: '#a6bfe3', border: '#557cb1', text: '#1d3f6b' },
];

function buildHourLabels(): string[] {
  return Array.from({ length: HOURS_IN_DAY }, (_, index) => {
    const start = (DAY_START + index) % HOURS_IN_DAY;
    const end = (start + 1) % HOURS_IN_DAY;
    return `${start.toString().padStart(2, '0')}:00–${end.toString().padStart(2, '0')}:00`;
  });
}

function createDefaultSectorDemand(maxSectors: number): number[] {
  const daytimeSectors = Math.min(3, Math.max(0, maxSectors));
  const nightSectors = Math.min(1, daytimeSectors);

  return Array.from({ length: HOURS_IN_DAY }, (_, index) => {
    const startHour = (DAY_START + index) % HOURS_IN_DAY;
    return startHour >= 1 && startHour < 5 ? nightSectors : daytimeSectors;
  });
}

function createMaximumSectorDemand(maxSectors: number): number[] {
  return Array.from({ length: HOURS_IN_DAY }, () => Math.max(0, maxSectors));
}

function createUnlimitedSectorLimits(): Array<number | null> {
  return Array.from({ length: HOURS_IN_DAY }, () => null);
}

function normalizeStaffSectorLimits(rawLimits: unknown, maxSectors: number): Array<number | null> {
  if (!Array.isArray(rawLimits)) {
    return createUnlimitedSectorLimits();
  }
  return Array.from({ length: HOURS_IN_DAY }, (_, index) => {
    const rawLimit = rawLimits[index];
    if (rawLimit === null || rawLimit === undefined) {
      return null;
    }
    const limit = clamp(Math.round(finiteNumber(rawLimit, maxSectors)), 0, maxSectors);
    return limit >= maxSectors ? null : limit;
  });
}

function resolveStaffSectorLimits(limits: Array<number | null>, maxSectors: number): number[] {
  return Array.from({ length: HOURS_IN_DAY }, (_, index) => {
    const limit = limits[index];
    return limit === null || limit === undefined
      ? maxSectors
      : clamp(limit, 0, maxSectors);
  });
}

function formatCompactHourLabel(hourIndex: number): string {
  const startHour = (DAY_START + hourIndex) % HOURS_IN_DAY;
  const endHour = (startHour + 1) % HOURS_IN_DAY;
  return `${startHour}-${endHour}`;
}

function createDefaultDemandIntervals(): SectorDemandInterval[] {
  return [
    { id: 1, sectorCount: 4, startHour: 8, endHour: 11 },
    { id: 2, sectorCount: 5, startHour: 10, endHour: 11 },
  ];
}

function createOfficerRows(shifts: ShiftRule[]): OfficerStaffRow[] {
  return shifts.map((shift) => ({
    shift: shift.code,
    fl: 0,
    aps: 0,
    acs: 0,
  }));
}

function mergeOfficerRows(rows: OfficerStaffRow[], shifts: ShiftRule[]): OfficerStaffRow[] {
  const existingRows = new Map(rows.map((row) => [row.shift, row]));
  return shifts.map((shift) => existingRows.get(shift.code) ?? { shift: shift.code, fl: 0, aps: 0, acs: 0 });
}

function officerRowsFromRules(rows: OfficerStaffRule[], shifts: ShiftRule[]): OfficerStaffRow[] {
  const totals = new Map<string, OfficerStaffRow>();
  shifts.forEach((shift) => totals.set(shift.code, { shift: shift.code, fl: 0, aps: 0, acs: 0 }));
  rows.forEach((row) => {
    const current = totals.get(row.shift) ?? { shift: row.shift, fl: 0, aps: 0, acs: 0 };
    if (row.license === 'FL') {
      current.fl += row.count;
    } else if (row.license === 'APS') {
      current.aps += row.count;
    } else {
      current.acs += row.count;
    }
    totals.set(row.shift, current);
  });
  return Array.from(totals.values());
}

function personIndexFromId(id: string): number {
  return id.split('').reduce((total, char) => total * 26 + char.charCodeAt(0) - 64, 0) - 1;
}

function labelForPerson(index: number): string {
  let label = '';
  let cursor = index;
  while (cursor >= 0) {
    label = String.fromCharCode(65 + (cursor % 26)) + label;
    cursor = Math.floor(cursor / 26) - 1;
  }
  return label;
}

function workerColor(workerId: string) {
  const index = Math.max(0, personIndexFromId(workerId));
  return distinctWorkerColors[index % distinctWorkerColors.length];
}

function hourToSlot(hour: number): number {
  return (hour - DAY_START + HOURS_IN_DAY) % HOURS_IN_DAY;
}

function formatLicenseRatioShare(count: number, total: number): string {
  if (total <= 0) {
    return '0 % razmerja';
  }
  return `${Math.round((count / total) * 100)} % razmerja`;
}

function buildSectorDemandFromIntervals(
  maxSectors: number,
  baseSectors: number,
  intervals: SectorDemandInterval[],
): number[] {
  const demand = Array.from({ length: HOURS_IN_DAY }, () => clamp(baseSectors, 0, maxSectors));
  const hasInterval = Array.from({ length: HOURS_IN_DAY }, () => false);

  intervals.forEach((interval) => {
    const sectorCount = clamp(interval.sectorCount, 0, maxSectors);
    const startHour = clamp(interval.startHour, 0, 23);
    const endHour = clamp(interval.endHour, 0, 23);
    if (sectorCount <= 0 || startHour === endHour) {
      return;
    }

    let hour = startHour;
    for (let guard = 0; guard < HOURS_IN_DAY && hour !== endHour; guard += 1) {
      const slot = hourToSlot(hour);
      demand[slot] = hasInterval[slot] ? Math.max(demand[slot], sectorCount) : sectorCount;
      hasInterval[slot] = true;
      hour = (hour + 1) % HOURS_IN_DAY;
    }
  });

  return demand;
}

function bestBaseSectorCount(demand: number[], currentBaseSectors: number, maxSectors: number): number {
  if (demand.includes(0)) {
    return 0;
  }

  const counts = new Map<number, number>();
  demand.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  let bestValue = clamp(currentBaseSectors, 0, maxSectors);
  let bestCount = counts.get(bestValue) ?? -1;

  counts.forEach((count, value) => {
    if (count > bestCount || (count === bestCount && value < bestValue)) {
      bestValue = value;
      bestCount = count;
    }
  });

  return bestValue;
}

function intervalsFromSectorDemand(demand: number[], baseSectors: number, maxSectors: number): SectorDemandInterval[] {
  const intervals: SectorDemandInterval[] = [];
  let slot = 0;

  while (slot < HOURS_IN_DAY) {
    const sectorCount = clamp(demand[slot] ?? baseSectors, 0, maxSectors);
    if (sectorCount === baseSectors) {
      slot += 1;
      continue;
    }

    const startSlot = slot;
    while (slot < HOURS_IN_DAY && clamp(demand[slot] ?? baseSectors, 0, maxSectors) === sectorCount) {
      slot += 1;
    }

    if (sectorCount > 0) {
      intervals.push({
        id: intervals.length + 1,
        sectorCount,
        startHour: (DAY_START + startSlot) % HOURS_IN_DAY,
        endHour: (DAY_START + slot) % HOURS_IN_DAY,
      });
    }
  }

  return intervals;
}

function clampSectorDemand(demand: number[], maxSectors: number): number[] {
  return Array.from({ length: HOURS_IN_DAY }, (_, index) => clamp(demand[index] ?? maxSectors, 0, maxSectors));
}

function summarizeSectorDemand(values: number[]): string {
  const total = values.reduce((sum, value) => sum + value, 0);
  const peak = values.length > 0 ? Math.max(...values) : 0;
  return `${total} SH · max ${peak} sektorjev`;
}

const hourLabels = buildHourLabels();
const jobStatusLabels: Record<CalculationJobStatus['status'], string> = {
  queued: 'Čaka v vrsti',
  running: 'V teku',
  finished: 'Končano',
  failed: 'Napaka',
};

function createQueuedJobStatus(jobId: string, kind = 'calculation'): CalculationJobStatus {
  return {
    job_id: jobId,
    kind,
    status: 'queued',
    progress: 0,
    message: 'Čaka v vrsti za izračun.',
    elapsed_seconds: 0,
    error: null,
    cancel_requested: false,
    best_result_available: false,
    best_result_version: 0,
    best_max_sector_hours: null,
    best_requested_sector_hours: null,
    best_missing_sector_hours: null,
    best_planned_people: null,
    best_utilization_percent: null,
    solver_status: null,
    solver_solution_count: 0,
    solver_objective_value: null,
    solver_best_objective_bound: null,
    solver_optimality_gap_percent: null,
    solver_stop_reason: null,
    solver_best_bound_sector_hours: null,
    solver_sector_gap_to_best_bound: null,
    calculation_phase: null,
    calculation_phase_label: null,
    calculation_phase_detail: null,
    calculation_next_step: null,
    pattern_phase: null,
    pattern_current_people_limit: null,
    pattern_lower_bound: null,
    pattern_upper_bound: null,
    pattern_limit_index: null,
    pattern_limit_count: null,
    pattern_checked_limits: [],
    pattern_pattern_count: null,
    pattern_cache_status: null,
    pattern_cache_path: null,
    pattern_estimate_low_seconds: null,
    pattern_estimate_high_seconds: null,
    pattern_proven_minimum: null,
    warm_start_snapshot_id: null,
  };
}

function formatBestResultSummary(status: CalculationJobStatus): string | null {
  if (!status.best_result_available || status.best_max_sector_hours === null) {
    return null;
  }

  return [
    `Najboljša rešitev: ${status.best_max_sector_hours}/${status.best_requested_sector_hours ?? 0} sektorskih ur`,
    `manjka ${status.best_missing_sector_hours ?? 0}`,
    `${status.best_planned_people ?? 0} ljudi`,
    `${status.best_utilization_percent ?? 0}% izkoriščenost`,
  ].join(' | ');
}

function defaultUserConfigurationName(result: CalculatorResponse): string {
  if (result.missing_sector_hours > 0) {
    return `User ${result.planned_people}p ${result.max_sector_hours}/${result.requested_sector_hours}SH - nepopolna`;
  }
  return `User ${result.planned_people}p ${result.max_sector_hours}SH`;
}

function formatProgressMessage(status: CalculationJobStatus): string {
  if (status.best_result_available && status.message.toLowerCase().includes('najbolj')) {
    return status.calculation_phase_detail ?? 'Iščem boljšo rešitev in dokazujem mejo.';
  }
  return status.message;
}

function formatProofEstimate(status: CalculationJobStatus): string | null {
  if (!status.best_result_available || !status.solver_status) {
    return null;
  }
  if (status.solver_status === 'OPTIMAL' || status.solver_sector_gap_to_best_bound === 0) {
    return 'ocena do dokaza: dokazano';
  }
  if (status.solver_sector_gap_to_best_bound === null) {
    return 'ocena do dokaza: ni dovolj signala';
  }

  const gap = status.solver_sector_gap_to_best_bound;
  let bucket = 'zelo dolgo (30+ min)';
  if (gap <= 1) {
    bucket = status.elapsed_seconds < 120 ? 'hitro (1-10 min)' : 'dolgo (10-30 min)';
  } else if (gap <= 2 && status.elapsed_seconds < 600) {
    bucket = 'dolgo (10-30 min)';
  }
  return `ocena do dokaza: ${bucket}`;
}

function formatSolverSummary(status: CalculationJobStatus): string | null {
  const parts: string[] = [];
  if (status.solver_status) {
    parts.push(`CP-SAT ${status.solver_status}`);
  }
  if (status.solver_solution_count > 0) {
    parts.push(`${status.solver_solution_count} rešitev`);
  }
  if (status.solver_optimality_gap_percent !== null) {
    parts.push(`dokazna vrzel ${status.solver_optimality_gap_percent}%`);
  }
  if (status.solver_sector_gap_to_best_bound !== null && status.solver_best_bound_sector_hours !== null) {
    parts.push(`SH meja dopušča +${status.solver_sector_gap_to_best_bound} sektorskih ur`);
  }
  const proofEstimate = formatProofEstimate(status);
  if (proofEstimate) {
    parts.push(proofEstimate);
  }
  if (status.solver_stop_reason) {
    parts.push(`ustavljeno: ${status.solver_stop_reason}`);
  }
  return parts.length > 0 ? parts.join(' | ') : null;
}

function formatCalculationPhase(status: CalculationJobStatus): { label: string; detail: string; nextStep: string | null } | null {
  if (!status.calculation_phase_label && !status.calculation_phase_detail) {
    return null;
  }
  return {
    label: status.calculation_phase_label ?? 'Faza izračuna',
    detail: status.calculation_phase_detail ?? status.message,
    nextStep: status.calculation_next_step,
  };
}

function officePoolTotal(pool: OfficePool): number {
  return pool.fl + pool.aps + pool.acs;
}

function officePoolRulesFromPool(pool: OfficePool): OfficePoolRule[] {
  return [
    { count: clamp(pool.fl, 0, 80), license: 'FL' },
    { count: clamp(pool.aps, 0, 80), license: 'APS' },
    { count: clamp(pool.acs, 0, 80), license: 'ACS' },
  ].filter((row) => row.count > 0) as OfficePoolRule[];
}

function officerStaffRulesFromPool(pool: OfficePool, shift: string): OfficerStaffRule[] {
  if (!shift) {
    return [];
  }
  return [
    { count: clamp(pool.fl, 0, 80), license: 'FL', shift },
    { count: clamp(pool.aps, 0, 80), license: 'APS', shift },
    { count: clamp(pool.acs, 0, 80), license: 'ACS', shift },
  ].filter((row) => row.count > 0) as OfficerStaffRule[];
}

function mergeOfficerStaffRules(rows: OfficerStaffRule[]): OfficerStaffRule[] {
  const merged = new Map<string, OfficerStaffRule>();
  rows.forEach((row) => {
    const count = clamp(row.count, 0, 80);
    if (count <= 0) {
      return;
    }
    const key = `${row.license}:${row.shift}`;
    const current = merged.get(key);
    if (current) {
      current.count = clamp(current.count + count, 0, 80);
    } else {
      merged.set(key, { ...row, count });
    }
  });
  return Array.from(merged.values());
}

function officePoolFromPayload(payload: CalculatorRequest): OfficePool {
  return payload.office_pool.reduce<OfficePool>((pool, row) => {
    if (row.license === 'FL') {
      return { ...pool, fl: pool.fl + row.count };
    }
    if (row.license === 'APS') {
      return { ...pool, aps: pool.aps + row.count };
    }
    return { ...pool, acs: pool.acs + row.count };
  }, { fl: 0, aps: 0, acs: 0 });
}

function hasOfficePool(payload: CalculatorRequest): boolean {
  return officePoolTotal(officePoolFromPayload(payload)) > 0;
}

function hasOfficeFallbackSelection(selection: OfficeFallbackSelection): boolean {
  if (officePoolTotal(selection.pool) <= 0) {
    return false;
  }
  return selection.mode === 'auto' || Boolean(selection.shift);
}

function statusNeedsTimeLimitDecision(status: CalculationJobStatus, restartPlan: JobRestartPlan | null): boolean {
  if (status.status !== 'finished' || restartPlan === null) {
    return false;
  }
  if ((status.best_missing_sector_hours ?? 0) <= 0) {
    return false;
  }
  return Boolean(status.solver_status || status.solver_stop_reason);
}

function warmStartFromResult(result?: CalculatorResponse | null): CalculatorRequest['warm_start'] {
  if (!result) {
    return null;
  }
  return {
    people: result.people,
    hourly_coverage: result.hourly_coverage,
  };
}

function warmStartContinuationFields(
  warmStartResult?: CalculatorResponse | null,
  warmStartSnapshotId?: string | null,
): Pick<CalculatorRequest, 'warm_start' | 'warm_start_snapshot_id'> {
  if (warmStartSnapshotId) {
    return {
      warm_start: null,
      warm_start_snapshot_id: warmStartSnapshotId,
    };
  }
  return {
    warm_start: warmStartFromResult(warmStartResult),
    warm_start_snapshot_id: null,
  };
}

function clonePayloadForRegularContinuation(
  payload: CalculatorRequest,
  extraSeconds: number,
  warmStartResult?: CalculatorResponse | null,
  warmStartSnapshotId?: string | null,
): CalculatorRequest {
  return {
    ...payload,
    office_fallback_mode: 'disabled',
    continuation_min_sector_hours: Math.max(
      payload.continuation_min_sector_hours ?? 0,
      warmStartResult?.max_sector_hours ?? 0,
    ),
    solver_random_seed: Math.min(2_147_483_647, (payload.solver_random_seed ?? 1) + 1),
    ...warmStartContinuationFields(warmStartResult, warmStartSnapshotId),
    settings: {
      ...payload.settings,
      cp_sat_time_limit_seconds: Math.min(3600, extraSeconds),
      // A visible "5 min" continuation must not stop after the shorter
      // no-improvement limit inherited from the original calculation.
      cp_sat_no_improvement_seconds: 0,
    },
  };
}

function clonePayloadForLeaderCrisis(
  payload: CalculatorRequest,
  maxExceptionHours: number,
  warmStartResult?: CalculatorResponse | null,
  warmStartSnapshotId?: string | null,
): CalculatorRequest {
  return {
    ...clonePayloadForRegularContinuation(
      payload,
      Math.min(300, payload.settings.cp_sat_time_limit_seconds),
      warmStartResult,
      warmStartSnapshotId,
    ),
    leader_exception_mode: 'allow',
    max_leader_exception_hours: clamp(maxExceptionHours, 1, 48),
  };
}

function clonePayloadForEmergencyShift(
  payload: CalculatorRequest,
  shiftCode: string,
  warmStartResult?: CalculatorResponse | null,
  warmStartSnapshotId?: string | null,
): CalculatorRequest {
  const continuation = clonePayloadForRegularContinuation(
    payload,
    Math.min(300, payload.settings.cp_sat_time_limit_seconds),
    warmStartResult,
    warmStartSnapshotId,
  );
  return {
    ...continuation,
    settings: {
      ...continuation.settings,
      shifts: payload.settings.shifts.map((shift) => (
        shift.code === shiftCode ? { ...shift, enabled: true } : shift
      )),
    },
  };
}

function clonePayloadForExtraPerson(
  payload: CalculatorRequest,
  warmStartResult: CalculatorResponse,
  warmStartSnapshotId?: string | null,
): CalculatorRequest | null {
  const totalPeople = nextPeopleLimit(payload, warmStartResult);
  if (totalPeople === null) {
    return null;
  }
  return {
    ...clonePayloadForRegularContinuation(
      payload,
      FULL_WHAT_IF_TIME_LIMIT_SECONDS,
      warmStartResult,
      warmStartSnapshotId,
    ),
    total_people: totalPeople,
    settings: fullWhatIfSolverSettings(payload.settings),
  };
}

function clonePayloadForLeaderSectorHours(
  payload: CalculatorRequest,
  v1SectorLimit: number,
  v2SectorLimit: number,
  warmStartResult?: CalculatorResponse | null,
  warmStartSnapshotId?: string | null,
): CalculatorRequest {
  const continuation = clonePayloadForRegularContinuation(
    payload,
    FULL_WHAT_IF_TIME_LIMIT_SECONDS,
    warmStartResult,
    warmStartSnapshotId,
  );
  return {
    ...continuation,
    settings: {
      ...fullWhatIfSolverSettings(continuation.settings),
      v1_sector_limit: clamp(v1SectorLimit, payload.settings.v1_sector_limit, 24),
      v2_sector_limit: clamp(v2SectorLimit, payload.settings.v2_sector_limit, 24),
    },
  };
}

function lockedStaffFromResultForWarmStart(result: CalculatorResponse, payload: CalculatorRequest): LockedStaffRule[] {
  const regularShiftCodes = activeShiftCodes(payload.settings.shifts);
  return result.people
    .filter((person) => person.source !== 'officer' && person.source !== 'office-pool')
    .filter((person) => regularShiftCodes.has(person.shift))
    .map((person) => ({
      count: 1,
      license: leaderRoleDisplayId(person.role) ? 'FL' : person.license,
      shift: person.shift,
      role: person.role,
      label: person.id,
    }));
}

function officerStaffFromResult(result: CalculatorResponse, payload: CalculatorRequest): OfficerStaffRule[] {
  const activeOfficerShiftCodes = activeShiftCodes(payload.settings.officer_shifts);
  return result.people
    .filter((person) => isOfficeSource(person.source))
    .reduce<OfficerStaffRule[]>((rows, person) => {
      if (!activeOfficerShiftCodes.has(person.shift)) {
        return rows;
      }
      const existing = rows.find((row) => row.license === person.license && row.shift === person.shift);
      if (existing) {
        existing.count += 1;
      } else {
        rows.push({ count: 1, license: person.license, shift: person.shift });
      }
      return rows;
    }, []);
}

function clonePayloadForLockedRoster(payload: CalculatorRequest, result: CalculatorResponse): CalculatorRequest {
  const lockedStaff = lockedStaffFromResultForWarmStart(result, payload);
  const regularLicenseCounts = lockedStaff.reduce<Record<License, number>>((counts, person) => {
    counts[person.license] += person.count;
    return counts;
  }, { FL: 0, APS: 0, ACS: 0 });
  return {
    ...payload,
    calculation_mode: 'staff_to_coverage',
    total_people: lockedStaff.reduce((sum, person) => sum + person.count, 0),
    fl_count: regularLicenseCounts.FL,
    aps_count: regularLicenseCounts.APS,
    acs_count: regularLicenseCounts.ACS,
    fixed_staff: [],
    locked_staff: lockedStaff,
    officer_staff: officerStaffFromResult(result, payload),
    office_pool: [],
    license_mix_percent: null,
    include_pareto: false,
    office_fallback_mode: 'disabled',
    preferred_manual_configuration_id: null,
  };
}

function clonePayloadForOfficeFallback(
  payload: CalculatorRequest,
  includeFmp: boolean,
  officeSelection?: OfficeFallbackSelection,
  warmStartResult?: CalculatorResponse | null,
  warmStartSnapshotId?: string | null,
): CalculatorRequest {
  const currentPayload = applyCurrentFmpSelection(payload, includeFmp);
  const fallbackSelection: OfficeFallbackSelection = officeSelection ?? {
    mode: 'auto',
    pool: officePoolFromPayload(currentPayload),
  };

  if (fallbackSelection.mode === 'fixed' && fallbackSelection.shift) {
    const fixedOfficeRules = officerStaffRulesFromPool(fallbackSelection.pool, fallbackSelection.shift);
    return {
      ...currentPayload,
      fixed_staff: currentPayload.fixed_staff,
      locked_staff: currentPayload.locked_staff,
      officer_staff: mergeOfficerStaffRules([...currentPayload.officer_staff, ...fixedOfficeRules]),
      office_pool: [],
      office_fallback_mode: 'disabled',
      continuation_min_sector_hours: Math.max(
        currentPayload.continuation_min_sector_hours ?? 0,
        warmStartResult?.max_sector_hours ?? 0,
      ),
      warm_start_roster_priority: 100,
      solver_random_seed: Math.min(2_147_483_647, (currentPayload.solver_random_seed ?? 1) + 1),
      ...warmStartContinuationFields(warmStartResult, warmStartSnapshotId),
      settings: fullWhatIfSolverSettings(currentPayload.settings),
    };
  }

  const overrideRules = officePoolTotal(fallbackSelection.pool) > 0
    ? officePoolRulesFromPool(fallbackSelection.pool)
    : currentPayload.office_pool;
  return {
    ...currentPayload,
    fixed_staff: currentPayload.fixed_staff,
    locked_staff: currentPayload.locked_staff,
    office_pool: overrideRules,
    office_fallback_mode: 'force',
    continuation_min_sector_hours: Math.max(
      currentPayload.continuation_min_sector_hours ?? 0,
      warmStartResult?.max_sector_hours ?? 0,
    ),
    warm_start_roster_priority: 100,
    solver_random_seed: Math.min(2_147_483_647, (currentPayload.solver_random_seed ?? 1) + 1),
    ...warmStartContinuationFields(warmStartResult, warmStartSnapshotId),
    settings: fullWhatIfSolverSettings(currentPayload.settings),
  };
}

function formatPatternCacheStatus(status: CalculationJobStatus): string | null {
  if (!status.pattern_pattern_count) {
    return null;
  }
  const cacheLabel = status.pattern_cache_status === 'hit' ? 'cache' : 'novo';
  const estimate = status.pattern_estimate_low_seconds !== null && status.pattern_estimate_high_seconds !== null
    ? `ocena ${status.pattern_estimate_low_seconds}-${status.pattern_estimate_high_seconds} s`
    : null;
  return [`${status.pattern_pattern_count} vzorcev`, cacheLabel, estimate].filter(Boolean).join(' | ');
}

function formatPatternLimitStatus(status: CalculationJobStatus): string | null {
  if (status.pattern_current_people_limit === null && status.pattern_checked_limits.length === 0) {
    return null;
  }
  const current = status.pattern_current_people_limit === null
    ? null
    : `trenutno ${status.pattern_current_people_limit} ljudi`;
  const range = status.pattern_lower_bound !== null && status.pattern_upper_bound !== null
    ? `meje ${status.pattern_lower_bound}-${status.pattern_upper_bound}`
    : null;
  const position = status.pattern_limit_index !== null && status.pattern_limit_count !== null
    ? `${status.pattern_limit_index}/${status.pattern_limit_count}`
    : null;
  return [current, range, position].filter(Boolean).join(' | ');
}

function formatPatternCheckedLimits(status: CalculationJobStatus): string | null {
  if (status.pattern_checked_limits.length === 0) {
    return null;
  }
  return status.pattern_checked_limits
    .slice(-6)
    .map((item) => {
      const statusLabel = item.status === 'INFEASIBLE'
        ? 'ni izvedljivo'
        : item.status === 'OPTIMAL' || item.status === 'FEASIBLE'
          ? 'izvedljivo'
          : item.status.toLowerCase();
      const elapsed = item.elapsed_seconds === null ? '' : ` (${item.elapsed_seconds.toFixed(1)} s)`;
      return `${item.people_limit}: ${statusLabel}${elapsed}`;
    })
    .join(' | ');
}

function formatPatternBreakdown(values: Record<string, number>, limit = 8): string {
  const entries = Object.entries(values).sort((first, second) => second[1] - first[1] || first[0].localeCompare(second[0]));
  if (entries.length === 0) {
    return '—';
  }
  const visible = entries.slice(0, limit).map(([key, value]) => `${key} ${value}`);
  const hiddenCount = entries.length - visible.length;
  return hiddenCount > 0 ? `${visible.join(' · ')} · +${hiddenCount}` : visible.join(' · ');
}

function numericMetric(value: number | string | null | undefined): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value.replace(',', '.'));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatReserve(value: number | null): string {
  if (value === null) {
    return '—';
  }
  return value >= 0 ? `+${value}` : `${value}`;
}

function formatSignedValue(value: number, suffix = ''): string {
  const rounded = Math.abs(value) < 0.05 ? 0 : Math.round(value * 10) / 10;
  const formatted = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return `${rounded > 0 ? '+' : ''}${formatted}${suffix}`;
}

function formatComparisonSource(sourceType: string, sourceLabel: string | null | undefined): string {
  if (sourceType === 'user') {
    return 'Uporabnik';
  }
  if (sourceLabel) {
    return sourceLabel;
  }
  return sourceType === 'excel' ? 'Excel' : sourceType;
}

function formatLicenseDiff(diff: ConfigurationSimilarityMatch['license_diff']): string {
  return `FL ${formatSignedValue(diff.FL)} · APS ${formatSignedValue(diff.APS)} · ACS ${formatSignedValue(diff.ACS)}`;
}

function formatRoleHoursDiff(diff: ConfigurationSimilarityMatch['role_hours_diff']): string {
  return `V1 ${formatSignedValue(diff.V1)} · V2 ${formatSignedValue(diff.V2)} · V3 ${formatSignedValue(diff.V3)} · FMP ${formatSignedValue(diff.FMP)}`;
}

function totalAbsLicenseDiff(diff: ConfigurationSimilarityMatch['license_diff']): number {
  return Math.abs(diff.FL) + Math.abs(diff.APS) + Math.abs(diff.ACS);
}

function totalAbsRoleDiff(diff: ConfigurationSimilarityMatch['role_hours_diff']): number {
  return Math.abs(diff.V1) + Math.abs(diff.V2) + Math.abs(diff.V3) + Math.abs(diff.FMP);
}

function ProgressPhase({ status }: { status: CalculationJobStatus }) {
  const phase = formatCalculationPhase(status);
  if (!phase) {
    return null;
  }
  return (
    <div className="progress-phase">
      <strong>{phase.label}</strong>
      <span>{phase.detail}</span>
      {phase.nextStep ? <small>{phase.nextStep}</small> : null}
    </div>
  );
}

function SaveConfigurationDialog({
  result,
  name,
  duplicateWarning,
  isSaving,
  onNameChange,
  onCancel,
  onConfirm,
}: {
  result: CalculatorResponse;
  name: string;
  duplicateWarning: string | null;
  isSaving: boolean;
  onNameChange: (value: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="dialog-backdrop" role="presentation">
      <form
        className="panel save-config-dialog"
        onSubmit={(event) => {
          event.preventDefault();
          onConfirm();
        }}
      >
        <div>
          <p className="eyebrow">Shranjevanje</p>
          <h2>Shrani uporabniško konfiguracijo</h2>
        </div>
        {result.missing_sector_hours > 0 ? (
          <div className="warning-box">
            Shranjuješ nepopolno konfiguracijo: doseženih je {result.max_sector_hours}/{result.requested_sector_hours} SH,
            manjka {result.missing_sector_hours} SH. Ob ponovnem odprtju bodo cilj in manjkajoče ure ostali vidni.
          </div>
        ) : null}
        {duplicateWarning ? <div className="warning-box">{duplicateWarning}</div> : null}
        <label className="field">
          <span>Ime konfiguracije</span>
          <input
            autoFocus
            disabled={isSaving}
            onChange={(event) => onNameChange(event.target.value)}
            value={name}
          />
        </label>
        <div className="dialog-actions">
          <button className="secondary-button compact-button" disabled={isSaving} onClick={onCancel} type="button">
            Prekliči
          </button>
          <button className="primary-button compact-button" disabled={isSaving} type="submit">
            {isSaving ? 'Shranjujem ...' : 'Shrani konfiguracijo'}
          </button>
        </div>
      </form>
    </div>
  );
}

function TimeLimitDecisionPanel({
  decision,
  onContinue,
  onTryExtraPerson,
  onTryLeaderSectorHours,
  onTryLeaderCrisis,
  onTryOfficeFallback,
  onTryEmergencyShift,
  onKeepCurrent,
  isBusy,
}: {
  decision: TimeLimitDecision;
  onContinue: () => void;
  onTryExtraPerson: () => void;
  onTryLeaderSectorHours: (v1SectorLimit: number, v2SectorLimit: number) => void;
  onTryLeaderCrisis: (maxExceptionHours: number) => void;
  onTryOfficeFallback: (selection: OfficeFallbackSelection) => void;
  onTryEmergencyShift: (shiftCode: string) => void;
  onKeepCurrent: () => void;
  isBusy: boolean;
}) {
  const calculationPayload = decision.restartPlan.kind === 'one-down' ? null : decision.restartPlan.payload;
  const currentResult = decision.currentResult;
  const officeSuggestion = calculationPayload && currentResult
    ? suggestedOfficeLicense(calculationPayload, currentResult)
    : { license: 'FL' as const, reason: 'FL je najbolj prilagodljiv prvi office preizkus.' };
  const emergencyShift = calculationPayload && currentResult
    ? suggestedEmergencyShift(calculationPayload, currentResult)
    : null;
  const initialOfficePool = decision.restartPlan.kind === 'one-down'
    ? { fl: 0, aps: 0, acs: 0 }
    : officePoolFromPayload(decision.restartPlan.payload);
  const officeShiftOptions = decision.restartPlan.kind === 'one-down'
    ? []
    : decision.restartPlan.payload.settings.officer_shifts
      .filter((shift) => shift.enabled !== false)
      .map((shift) => shift.code);
  const defaultOfficeShift = officeShiftOptions[0] ?? fallbackSettings.officer_shifts[0]?.code ?? '';
  const [officeFallbackMode, setOfficeFallbackMode] = useState<OfficeFallbackMode>('auto');
  const [draftOfficeShift, setDraftOfficeShift] = useState(defaultOfficeShift);
  const [maxLeaderExceptionHours, setMaxLeaderExceptionHours] = useState(1);
  const initialLeaderLimits = calculationPayload
    ? increasedLeaderSectorLimits(
        calculationPayload.settings.v1_sector_limit,
        calculationPayload.settings.v2_sector_limit,
      )
    : { v1SectorLimit: 1, v2SectorLimit: 1 };
  const [draftV1SectorLimit, setDraftV1SectorLimit] = useState(initialLeaderLimits.v1SectorLimit);
  const [draftV2SectorLimit, setDraftV2SectorLimit] = useState(initialLeaderLimits.v2SectorLimit);
  const [draftOfficePool, setDraftOfficePool] = useState<OfficePool>(
    officePoolTotal(initialOfficePool) > 0
      ? initialOfficePool
      : {
          fl: officeSuggestion.license === 'FL' ? 1 : 0,
          aps: officeSuggestion.license === 'APS' ? 1 : 0,
          acs: officeSuggestion.license === 'ACS' ? 1 : 0,
        },
  );

  const hasPresetOffice = decision.restartPlan.kind !== 'one-down' && hasOfficePool(decision.restartPlan.payload);
  const officeSelection: OfficeFallbackSelection = {
    mode: officeFallbackMode,
    pool: draftOfficePool,
    shift: officeFallbackMode === 'fixed' && officeShiftOptions.includes(draftOfficeShift)
      ? draftOfficeShift
      : undefined,
  };
  const canTryOfficeFallback = decision.restartPlan.kind !== 'one-down'
    && hasOfficeFallbackSelection(officeSelection);
  const canTryLeaderCrisis = decision.restartPlan.kind !== 'one-down'
    && (currentResult?.missing_sector_hours ?? 0) > 0;
  const canIncreaseV1SectorHours = calculationPayload !== null
    && draftV1SectorLimit > calculationPayload.settings.v1_sector_limit;
  const canIncreaseV2SectorHours = calculationPayload !== null
    && draftV2SectorLimit > calculationPayload.settings.v2_sector_limit;
  const extraPeopleLimit = calculationPayload && currentResult
    ? nextPeopleLimit(calculationPayload, currentResult)
    : null;
  const canContinueSameRules = !coverageIsProven(decision.status);
  const updateDraftOfficePool = (key: keyof OfficePool, value: number) => {
    setDraftOfficePool((current) => ({ ...current, [key]: clamp(value, 0, 20) }));
  };
  const title = decision.reason === 'manual-stop'
    ? 'Izračun si ustavil pred časovno mejo'
    : coverageIsProven(decision.status)
      ? 'Dosežena je meja trenutnih pravil'
      : stoppedOnTimeLimit(decision.status)
        ? 'Časovni limit se je iztekel'
        : 'Izračun se je ustavil z najboljšo najdeno rešitvijo';
  const eyebrow = decision.reason === 'manual-stop'
    ? 'Ročna odločitev'
    : stoppedOnTimeLimit(decision.status)
      ? 'Odločitev po časovni meji'
      : 'Naslednji korak';
  return (
    <div className="time-limit-decision" role="status" aria-live="polite">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h3>{title}</h3>
        <p>
          {coverageIsProven(decision.status)
            ? 'Z enakimi pravili dodatno računanje ne more povečati SH. Izberi najmanjši operativni poseg ali obdrži rezultat.'
            : 'Najboljša rešitev ostane shranjena. Zaženeš lahko novo iskalno pot z enakimi pravili ali preizkusiš en nadzorovan operativni poseg.'}
        </p>
      </div>
      <div className="continuation-metrics" aria-label="Povzetek najboljše rešitve">
        <div><span>Pokritost</span><strong>{decision.status.best_max_sector_hours ?? 0}/{decision.status.best_requested_sector_hours ?? 0} SH</strong></div>
        <div><span>Manjka</span><strong>{decision.status.best_missing_sector_hours ?? 0} SH</strong></div>
        <div><span>Zgornja meja</span><strong>{decision.status.solver_best_bound_sector_hours ?? 'ni dokazana'}</strong></div>
        <div><span>Krizne ure</span><strong>{currentResult?.crisis_exception_hours ?? 0}</strong></div>
      </div>
      {decision.restartPlan.kind !== 'one-down' ? (
        <div className="continuation-options">
          <div className="continuation-option">
            <div>
              <strong>Nadaljuj iskanje 5 minut</strong>
              <span>
                {canContinueSameRules
                  ? 'Ohrani najboljšo rešitev in doseženo SH mejo, nato polnih 5 minut išče po novi poti. Notranjega iskalnega drevesa prejšnjega teka CP-SAT ne more obnoviti.'
                  : 'Maksimalni SH je pri teh pravilih že dokazan.'}
              </span>
            </div>
            <button className="secondary-button compact-button" disabled={isBusy || !canContinueSameRules} onClick={onContinue} type="button">
              Nadaljuj še 5 min
            </button>
          </div>
          <div className="continuation-option">
            <div>
              <strong>Dodaj +1 osebo</strong>
              <span>
                {extraPeopleLimit === null
                  ? 'Ta možnost je na voljo v načinu Odprtost sektorjev do največ 80 ljudi.'
                  : `Povečaj limit na ${extraPeopleLimit} ljudi; solver sam izbere licenco in izmeno ter ohrani trenutno rešitev kot warm-start.`}
              </span>
            </div>
            <button
              className="secondary-button compact-button"
              disabled={isBusy || extraPeopleLimit === null}
              onClick={onTryExtraPerson}
              type="button"
            >
              Dodaj +1 osebo
            </button>
          </div>
          <div className="continuation-option">
            <div>
              <strong>Razširi sektorske ure VI1</strong>
              <span>
                Povečaj samo omejitev VI1; VI2 in druga pravila ostanejo nespremenjena.
              </span>
            </div>
            <label className="crisis-hour-limit">
              VI1 največ ur
              <input
                min={calculationPayload?.settings.v1_sector_limit ?? 0}
                max="24"
                type="number"
                value={draftV1SectorLimit}
                onKeyDown={preventNumberInputArrowStep}
                onChange={(event) => setDraftV1SectorLimit(clamp(
                  Number(event.target.value),
                  calculationPayload?.settings.v1_sector_limit ?? 0,
                  24,
                ))}
              />
            </label>
            <button
              className="secondary-button compact-button"
              disabled={isBusy || !canIncreaseV1SectorHours}
              onClick={() => onTryLeaderSectorHours(
                draftV1SectorLimit,
                calculationPayload?.settings.v2_sector_limit ?? draftV2SectorLimit,
              )}
              type="button"
            >
              Povečaj VI1 in računaj
            </button>
          </div>
          <div className="continuation-option">
            <div>
              <strong>Razširi sektorske ure VI2</strong>
              <span>
                Povečaj samo omejitev VI2; VI1 in druga pravila ostanejo nespremenjena.
              </span>
            </div>
            <label className="crisis-hour-limit">
              VI2 največ ur
              <input
                min={calculationPayload?.settings.v2_sector_limit ?? 0}
                max="24"
                type="number"
                value={draftV2SectorLimit}
                onKeyDown={preventNumberInputArrowStep}
                onChange={(event) => setDraftV2SectorLimit(clamp(
                  Number(event.target.value),
                  calculationPayload?.settings.v2_sector_limit ?? 0,
                  24,
                ))}
              />
            </label>
            <button
              className="secondary-button compact-button"
              disabled={isBusy || !canIncreaseV2SectorHours}
              onClick={() => onTryLeaderSectorHours(
                calculationPayload?.settings.v1_sector_limit ?? draftV1SectorLimit,
                draftV2SectorLimit,
              )}
              type="button"
            >
              Povečaj VI2 in računaj
            </button>
          </div>
          <div className="continuation-option">
            <div>
              <strong>Omejen krizni VI/FMP poskus</strong>
              <span>Izjeme se odklenejo samo za poskus izboljšanja SH; rezerva jih ne more sprožiti.</span>
            </div>
            <label className="crisis-hour-limit">
              Največ ur
              <input
                min="1"
                max="48"
                type="number"
                value={maxLeaderExceptionHours}
                onKeyDown={preventNumberInputArrowStep}
                onChange={(event) => setMaxLeaderExceptionHours(clamp(Number(event.target.value), 1, 48))}
              />
            </label>
            <button
              className="secondary-button compact-button"
              disabled={isBusy || !canTryLeaderCrisis}
              onClick={() => onTryLeaderCrisis(maxLeaderExceptionHours)}
              type="button"
            >
              Preizkusi krizne ure
            </button>
          </div>
          {emergencyShift ? (
            <div className="continuation-option">
              <div>
                <strong>Začasno omogoči {emergencyShift.code}</strong>
                <span>Izmena je trenutno izključena in se prekriva z nepokritimi urami. Sprememba velja samo za ta poskus.</span>
              </div>
              <button
                className="secondary-button compact-button"
                disabled={isBusy}
                onClick={() => onTryEmergencyShift(emergencyShift.code)}
                type="button"
              >
                Preizkusi {emergencyShift.code}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
      {decision.restartPlan.kind !== 'one-down' ? (
        <div className="office-fallback-picker">
          <div>
            <strong>{hasPresetOffice ? 'Operativni office je že vpisan' : 'Dodaj office samo za ta poskus'}</strong>
            <span>
              {!hasPresetOffice ? `${officeSuggestion.reason} ` : ''}
              {officeFallbackMode === 'auto'
                ? 'Solver bo sam preizkusil aktivne office izmene in uporabil tisto, ki najbolje popravi pokritost.'
                : 'Izbrana office izmena bo dodana kot konkretna office oseba. Trenutna rešitev je samo warm start, zato lahko solver premeša ostale izmene.'}
            </span>
          </div>
          <div className="office-fallback-mode">
            <button
              className={officeFallbackMode === 'auto' ? 'active' : ''}
              type="button"
              onClick={() => setOfficeFallbackMode('auto')}
            >
              Poišči najboljši fit
            </button>
            <button
              className={officeFallbackMode === 'fixed' ? 'active' : ''}
              type="button"
              disabled={officeShiftOptions.length === 0}
              onClick={() => setOfficeFallbackMode('fixed')}
            >
              Izbrana office izmena
            </button>
          </div>
          <label>
            FL office
            <input
              min="0"
              max="20"
              type="number"
              value={draftOfficePool.fl}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => updateDraftOfficePool('fl', Number(event.target.value))}
            />
          </label>
          <label>
            APS office
            <input
              min="0"
              max="20"
              type="number"
              value={draftOfficePool.aps}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => updateDraftOfficePool('aps', Number(event.target.value))}
            />
          </label>
          <label>
            ACS office
            <input
              min="0"
              max="20"
              type="number"
              value={draftOfficePool.acs}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => updateDraftOfficePool('acs', Number(event.target.value))}
            />
          </label>
          {officeFallbackMode === 'fixed' ? (
            <label>
              Office izmena
              <select
                value={draftOfficeShift}
                onChange={(event) => setDraftOfficeShift(event.target.value)}
              >
                {officeShiftOptions.map((shiftCode) => (
                  <option key={shiftCode} value={shiftCode}>{shiftCode}</option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
      ) : null}
      <div className="decision-actions">
        {decision.restartPlan.kind === 'one-down' ? (
          <button className="secondary-button compact-button" disabled={isBusy} onClick={onContinue} type="button">
            Nadaljuj preverjanje
          </button>
        ) : null}
        {decision.restartPlan.kind !== 'one-down' ? (
          <button
            className="secondary-button compact-button"
            disabled={isBusy || !canTryOfficeFallback}
            onClick={() => onTryOfficeFallback(officeSelection)}
            type="button"
          >
            {officeFallbackMode === 'auto' ? 'Poišči najboljši office fit' : 'Računaj z izbrano office izmeno'}
          </button>
        ) : null}
        <button className="primary-button compact-button" disabled={isBusy} onClick={onKeepCurrent} type="button">
          Uporabi trenutno rešitev
        </button>
      </div>
    </div>
  );
}

function signedMetric(value: number, suffix = ''): string {
  if (value === 0) {
    return `0${suffix}`;
  }
  return `${value > 0 ? '+' : ''}${value}${suffix}`;
}

function ContinuationComparisonPanel({
  comparison,
  candidate,
  isBusy,
  onAcceptCandidate,
  onRestoreBaseline,
}: {
  comparison: ContinuationComparison;
  candidate: CalculatorResponse | null;
  isBusy: boolean;
  onAcceptCandidate: () => void;
  onRestoreBaseline: () => void;
}) {
  const resolvedCandidate = candidate ?? comparison.baseline;
  const delta = continuationDelta(comparison.baseline, resolvedCandidate);
  const candidateChanged = resolvedCandidate !== comparison.baseline;
  return (
    <div className="continuation-comparison" role="status" aria-live="polite">
      <div className="continuation-comparison-heading">
        <div>
          <p className="eyebrow">Primerjava nadaljevanja</p>
          <h3>{comparison.actionLabel}</h3>
        </div>
        {isBusy ? <span className="continuation-running">Poskus še teče</span> : null}
      </div>
      <div className="continuation-table-wrap">
        <table className="continuation-table">
          <thead>
            <tr>
              <th>Različica</th>
              <th>SH</th>
              <th>Ljudje</th>
              <th>Krizne ure</th>
              <th>Izkoriščenost</th>
              <th>Office uporabljen</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th>Prejšnja</th>
              <td>{comparison.baseline.max_sector_hours}/{comparison.baseline.requested_sector_hours}</td>
              <td>{comparison.baseline.planned_people}</td>
              <td>{comparison.baseline.crisis_exception_hours}</td>
              <td>{comparison.baseline.utilization_percent}%</td>
              <td>{resultUsesOffice(comparison.baseline) ? 'Da' : 'Ne'}</td>
            </tr>
            <tr className="candidate-row">
              <th>Nov poskus</th>
              <td>{resolvedCandidate.max_sector_hours}/{resolvedCandidate.requested_sector_hours} <small>{signedMetric(delta.sectorHours)}</small></td>
              <td>{resolvedCandidate.planned_people} <small>{signedMetric(delta.plannedPeople)}</small></td>
              <td>{resolvedCandidate.crisis_exception_hours} <small>{signedMetric(delta.crisisHours)}</small></td>
              <td>{resolvedCandidate.utilization_percent}% <small>{signedMetric(delta.utilizationPercent, '%')}</small></td>
              <td>{resultUsesOffice(resolvedCandidate) ? 'Da' : 'Ne'}</td>
            </tr>
          </tbody>
        </table>
      </div>
      {!isBusy ? (
        <div className="decision-actions">
          <button className="primary-button compact-button" disabled={!candidateChanged} onClick={onAcceptCandidate} type="button">
            Uporabi novi rezultat
          </button>
          <button className="secondary-button compact-button" onClick={onRestoreBaseline} type="button">
            Vrni prejšnjo rešitev
          </button>
        </div>
      ) : null}
    </div>
  );
}

function formatAuditMetric(value: number | string | null | undefined, suffix = ''): string {
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  return `${value}${suffix}`;
}

function manualAuditStatusLabel(row: ManualConfigurationAuditRow): string {
  if (row.status === 'covered') {
    return manualAuditHasSectorMismatch(row) ? 'Pokrito, drug sektor' : 'Pokrito';
  }
  if (row.status === 'shortfall') {
    return `Manjka ${row.model_missing_sector_hours ?? '?'} SH`;
  }
  if (row.status === 'missing') {
    return 'Manjka v bazi';
  }
  if (row.status === 'missing_schedule') {
    return 'Ni Excel urnika';
  }
  if (row.status === 'unsupported') {
    return 'Nepodprto';
  }
  if (row.status === 'error') {
    return 'Napaka';
  }
  return row.status.toUpperCase();
}

function manualAuditHasSectorMismatch(row: ManualConfigurationAuditRow): boolean {
  return (row.hourly_comparison ?? []).some((hour) => (
    (hour.missing_sectors?.length ?? 0) > 0 || (hour.extra_sectors?.length ?? 0) > 0
  ));
}

function manualAuditRowClass(row: ManualConfigurationAuditRow): string {
  if (row.status === 'covered' && !manualAuditHasSectorMismatch(row) && (row.model_vs_manual_diff ?? 0) === 0) {
    return 'manual-audit-ok';
  }
  if (row.status === 'covered') {
    return 'manual-audit-warning';
  }
  return 'manual-audit-error';
}

function formatAuditList(values: string[] | undefined): string {
  return values && values.length > 0 ? values.join(', ') : '—';
}

function manualAuditHourClass(diff: number, missing: string[] | undefined, extra: string[] | undefined): string {
  if (diff !== 0 || (missing?.length ?? 0) > 0 || (extra?.length ?? 0) > 0) {
    return 'manual-audit-hour has-diff';
  }
  return 'manual-audit-hour';
}

function manualAuditSectorClass(status: string): string {
  if (status === 'same_workers') {
    return 'audit-sector-cell exact';
  }
  if (status === 'same_sector') {
    return 'audit-sector-cell same-sector';
  }
  if (status === 'manual_only' || status === 'model_only') {
    return 'audit-sector-cell mismatch';
  }
  return 'audit-sector-cell closed';
}

const manualSheetSections = [
  { key: 'all', label: 'ALL', lanes: 2 },
  { key: 'lower', label: 'LOWER', lanes: 2 },
  { key: 'upper', label: 'UPPER', lanes: 2 },
  { key: 'mid', label: 'MID', lanes: 2 },
  { key: 'high', label: 'HIGH', lanes: 2 },
  { key: 'top', label: 'TOP', lanes: 2 },
] as const;

type ManualSheetSectionKey = typeof manualSheetSections[number]['key'];
type ManualSheetLicense = 'FL' | 'APS' | 'ACS';
type ManualSheetPerson = {
  id: string;
  label: string;
  source: string;
  shift: string | null;
  role: string | null;
  license: ManualSheetLicense | null;
  metric: string | null;
  startHour: number | null;
  durationHours: number | null;
  slots: number[];
  color: WorkerColor;
};

type ManualSheetLane = {
  index: number;
  sectionKey: ManualSheetSectionKey;
  sectionLabel: string;
  lane: number;
};

type ManualAvailabilitySummary = {
  total: number;
  fl: number;
  aps: number;
  acs: number;
};
type ManualScheduledCell = {
  id: string;
  license: string | null;
  shift: string | null;
  role: string | null;
  source: string | null;
};

const manualSheetLanes: ManualSheetLane[] = manualSheetSections.flatMap((section) => (
  Array.from({ length: section.lanes }, (_, lane) => ({
    index: 0,
    sectionKey: section.key,
    sectionLabel: section.label,
    lane,
  }))
)).map((lane, index) => ({ ...lane, index }));

const manualRosterLabels = [
  'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'P', 'R', 'S',
  'T', 'V', 'Z', 'AA', 'BB', 'CC', 'DD', 'EE', 'FF', 'GG', 'HH', 'II', 'JJ', 'KK',
];

function manualHourLabel(slot: number): string {
  const start = (DAY_START + slot) % HOURS_IN_DAY;
  const end = (start + 1) % HOURS_IN_DAY;
  return `${start}.00 - ${end}.00`;
}

function expandManualPeople(rows: ManualConfigurationStaffRow[]): ManualSheetPerson[] {
  const rawPeople: Array<Omit<ManualSheetPerson, 'id' | 'label' | 'color'> & { rawIndex: number }> = [];
  let rawIndex = 0;

  rows.forEach((row) => {
    const entries: Array<[ManualSheetLicense, number]> = [
      ['FL', row.fl],
      ['APS', row.aps],
      ['ACS', row.acs],
    ];

    entries.forEach(([license, count]) => {
      for (let index = 0; index < count; index += 1) {
        rawPeople.push({
          rawIndex,
          source: row.source,
          shift: row.shift,
          role: row.role,
          license,
          metric: license,
          startHour: row.start_hour,
          durationHours: row.duration_hours,
          slots: row.hour_slots,
        });
        rawIndex += 1;
      }
    });
  });

  const sortedPeople = [...rawPeople].sort((first, second) => (
    (first.role ? 0 : 1) - (second.role ? 0 : 1)
    || (first.startHour ?? 99) - (second.startHour ?? 99)
    || (first.shift ?? '').localeCompare(second.shift ?? '')
    || (first.license ?? '').localeCompare(second.license ?? '')
    || first.rawIndex - second.rawIndex
  ));

  let regularIndex = 0;
  let officerIndex = 1;
  const roleCounts = new Map<string, number>();

  return sortedPeople.map((person, index) => {
    let label: string;
    if (person.role) {
      const seen = roleCounts.get(person.role) ?? 0;
      roleCounts.set(person.role, seen + 1);
      label = seen === 0 ? person.role : `${person.role}-${seen + 1}`;
    } else if (person.source === 'officer') {
      label = `O${officerIndex}`;
      officerIndex += 1;
    } else {
      label = manualRosterLabels[regularIndex] ?? `P${regularIndex + 1}`;
      regularIndex += 1;
    }

    return {
      ...person,
      id: `${label}-${person.shift}-${person.license}-${person.rawIndex}`,
      label,
      color: distinctWorkerColors[index % distinctWorkerColors.length],
    };
  });
}

function summarizeManualAvailability(people: ManualSheetPerson[], slot: number): ManualAvailabilitySummary {
  return people.reduce<ManualAvailabilitySummary>((summary, person) => {
    if (!person.slots.includes(slot)) {
      return summary;
    }
    summary.total += 1;
    if (person.license === 'FL') {
      summary.fl += 1;
    } else if (person.license === 'APS') {
      summary.aps += 1;
    } else {
      summary.acs += 1;
    }
    return summary;
  }, { total: 0, fl: 0, aps: 0, acs: 0 });
}

function manualVirtualPersonLabel(person: Pick<VirtualPerson, 'id' | 'role'> | ManualScheduledCell): string {
  return leaderRoleDisplayId(person.role) ?? person.role ?? person.id;
}

function manualScheduledCellsFromCoverage(
  hourlyCoverage: HourlyCoverage[],
  peopleById: Map<string, ManualScheduledCell>,
): Array<Array<ManualScheduledCell | null>> {
  const laneIndexesBySection = new Map<ManualSheetSectionKey, number[]>();
  manualSheetLanes.forEach((lane) => {
    const lanes = laneIndexesBySection.get(lane.sectionKey) ?? [];
    lanes.push(lane.index);
    laneIndexesBySection.set(lane.sectionKey, lanes);
  });

  const fallbackPerson = (workerId: string): ManualScheduledCell => ({
    id: workerId,
    license: null,
    shift: null,
    role: null,
    source: 'unknown',
  });

  const resolveWorker = (workerId: string): ManualScheduledCell => (
    peopleById.get(workerId)
    ?? peopleById.get(workerId.toUpperCase())
    ?? peopleById.get(workerId.toLowerCase())
    ?? fallbackPerson(workerId)
  );

  return hourlyCoverage.map((hour) => {
    const cells = Array.from<ManualScheduledCell | null>({ length: manualSheetLanes.length }).fill(null);
    const placeWorker = (sectorName: string, workerId: string) => {
      if (!workerId) {
        return;
      }
      const sectionKey = sectorName.toLowerCase() as ManualSheetSectionKey;
      const laneIndexes = laneIndexesBySection.get(sectionKey) ?? [];
      const targetLane = laneIndexes.find((laneIndex) => cells[laneIndex] === null);
      if (targetLane === undefined) {
        return;
      }
      cells[targetLane] = resolveWorker(workerId);
    };

    hour.sector_workers.forEach((sector) => {
      if (!sector) {
        return;
      }
      placeWorker(sector.sector_name, sector.lower_worker);
      placeWorker(sector.sector_name, sector.upper_worker);
    });

    return cells;
  });
}

function manualScheduledCells(result: CalculatorResponse): Array<Array<ManualScheduledCell | null>> {
  const peopleById = new Map<string, ManualScheduledCell>();
  result.people.forEach((person) => {
    const cell = {
      id: person.id,
      license: person.license,
      shift: person.shift,
      role: person.role,
      source: person.source,
    };
    peopleById.set(person.id, cell);
    peopleById.set(person.id.toUpperCase(), cell);
    peopleById.set(person.id.toLowerCase(), cell);
  });
  return manualScheduledCellsFromCoverage(result.hourly_coverage, peopleById);
}

function manualScheduledCellsFromManualSchedule(schedule: ManualConfigurationSchedule): Array<Array<ManualScheduledCell | null>> {
  const peopleById = new Map<string, ManualScheduledCell>();
  schedule.people.forEach((person) => {
    const cell = {
      id: person.label,
      license: null,
      shift: person.shift,
      role: person.role,
      source: person.source,
    };
    peopleById.set(person.label, cell);
    peopleById.set(person.label.toUpperCase(), cell);
    peopleById.set(person.label.toLowerCase(), cell);
  });
  return manualScheduledCellsFromCoverage(schedule.hourly_coverage, peopleById);
}

function manualPeopleFromResult(result: CalculatorResponse): ManualSheetPerson[] {
  return [...result.people]
    .sort((first, second) => personIndexFromId(first.id) - personIndexFromId(second.id))
    .map((person) => ({
      id: person.id,
      label: manualVirtualPersonLabel(person),
      source: person.source,
      shift: person.shift,
      role: person.role,
      license: person.license,
      metric: person.license,
      startHour: null,
      durationHours: null,
      slots: [],
      color: workerColor(person.id),
    }));
}

function manualPeopleFromManualSchedule(schedule: ManualConfigurationSchedule): ManualSheetPerson[] {
  return schedule.people.map((person, index) => ({
    id: person.label,
    label: person.label,
    source: person.source ?? 'manual',
    shift: person.shift,
    role: person.role,
    license: null,
    metric: person.sector_hours === null || person.sector_hours === undefined ? null : String(person.sector_hours),
    startHour: null,
    durationHours: null,
    slots: [],
    color: distinctWorkerColors[index % distinctWorkerColors.length],
  }));
}

type PairShiftSummary = {
  key: string;
  label: string;
  fl: number;
  aps: number;
  acs: number;
  total: number;
  sort: number;
};

type PairHourSummary = {
  hour: string;
  open: number;
  sectors: string[];
  signature: string;
};

type PairWorkloadPerson = {
  label: string;
  meta: string;
  hours: number;
};

type PairWorkloadBucket = {
  hours: number;
  count: number;
};

type PairConfigMetrics = {
  id: string;
  name: string;
  source: string;
  calculatorResult: CalculatorResponse | null;
  sectorHours: number;
  people: number;
  requiredFl: number;
  utilizationPercent: number;
  averageHours: number;
  controllerHours: number;
  licenseText: string;
  shiftRows: PairShiftSummary[];
  hourlyRows: PairHourSummary[];
  workloadPeople: PairWorkloadPerson[];
  workloadBuckets: PairWorkloadBucket[];
};

function pairShiftLabel(row: ManualConfigurationStaffRow): string {
  if (row.role) {
    return `${row.role}/${row.shift}`;
  }
  return row.shift;
}

function pairShiftSort(row: ManualConfigurationStaffRow): number {
  const start = row.start_hour ?? 99;
  const sourceOffset = row.source === 'officer' ? 0.2 : 0;
  const roleOffset = row.role ? -0.2 : 0;
  return start + sourceOffset + roleOffset;
}

function manualConfigSectorHours(detail: ManualConfigurationDetail): number {
  const scheduleHours = numericMetric(detail.manual_schedule?.max_sector_hours);
  if (scheduleHours !== null) {
    return scheduleHours;
  }
  const modelHours = numericMetric(detail.model_max_sector_hours);
  if (modelHours !== null) {
    return modelHours;
  }
  return detail.manual_schedule?.hourly_coverage.reduce((sum, hour) => sum + hour.open_sectors, 0) ?? 0;
}

function manualConfigControllerHours(detail: ManualConfigurationDetail): number {
  const scheduleHours = numericMetric(detail.manual_schedule?.scheduled_person_hours);
  if (scheduleHours !== null) {
    return scheduleHours;
  }
  return manualConfigSectorHours(detail) * 2;
}

function manualConfigRequiredFl(detail: ManualConfigurationDetail): number {
  return detail.staff_rows.reduce((sum, row) => (
    row.role ? sum + row.fl : sum
  ), 0);
}

function manualConfigShiftRows(detail: ManualConfigurationDetail): PairShiftSummary[] {
  const rows = new Map<string, PairShiftSummary>();
  detail.staff_rows.forEach((row) => {
    const label = pairShiftLabel(row);
    const existing = rows.get(label) ?? {
      key: label,
      label,
      fl: 0,
      aps: 0,
      acs: 0,
      total: 0,
      sort: pairShiftSort(row),
    };
    existing.fl += row.fl;
    existing.aps += row.aps;
    existing.acs += row.acs;
    existing.total += row.total;
    existing.sort = Math.min(existing.sort, pairShiftSort(row));
    rows.set(label, existing);
  });
  return [...rows.values()].sort((first, second) => first.sort - second.sort || first.label.localeCompare(second.label));
}

function manualConfigHourlyRows(detail: ManualConfigurationDetail): PairHourSummary[] {
  const coverage = detail.manual_schedule?.hourly_coverage ?? [];
  return hourLabels.map((hour, index) => {
    const currentHour = coverage[index];
    const sectors = (currentHour?.sector_workers ?? [])
      .filter((sector): sector is NonNullable<typeof sector> => Boolean(sector))
      .map((sector) => `${sector.sector_name} ${sector.lower_worker}/${sector.upper_worker}`);
    return {
      hour,
      open: currentHour?.open_sectors ?? 0,
      sectors,
      signature: sectors.join('|'),
    };
  });
}

function manualConfigWorkloadPeople(detail: ManualConfigurationDetail): PairWorkloadPerson[] {
  const people = detail.manual_schedule?.people ?? [];
  return people
    .map((person) => {
      const hours = numericMetric(person.sector_hours) ?? 0;
      const meta = [person.role, person.shift].filter(Boolean).join(' / ') || 'brez vloge';
      return { label: person.label, meta, hours };
    })
    .sort((first, second) => second.hours - first.hours || first.label.localeCompare(second.label));
}

function workloadBuckets(people: PairWorkloadPerson[]): PairWorkloadBucket[] {
  const counts = new Map<number, number>();
  people.forEach((person) => {
    counts.set(person.hours, (counts.get(person.hours) ?? 0) + 1);
  });
  return [...counts.entries()]
    .map(([hours, count]) => ({ hours, count }))
    .sort((first, second) => second.hours - first.hours);
}

function metricsFromManualConfiguration(detail: ManualConfigurationDetail): PairConfigMetrics {
  const people = Math.max(
    0,
    detail.parsed_total || detail.manual_schedule?.people.length || detail.total_without_waiting || 0,
  );
  const controllerHours = manualConfigControllerHours(detail);
  const workloadPeople = manualConfigWorkloadPeople(detail);
  return {
    id: detail.id,
    name: detail.name,
    source: formatComparisonSource(detail.source_type ?? 'excel', detail.source_label),
    calculatorResult: detail.calculator_result,
    sectorHours: manualConfigSectorHours(detail),
    people,
    requiredFl: manualConfigRequiredFl(detail),
    utilizationPercent: people > 0 ? Math.round((controllerHours / (people * 5)) * 100) : 0,
    averageHours: people > 0 ? Math.round((controllerHours / people) * 10) / 10 : 0,
    controllerHours,
    licenseText: `FL ${detail.license_counts.FL} · APS ${detail.license_counts.APS} · ACS ${detail.license_counts.ACS}`,
    shiftRows: manualConfigShiftRows(detail),
    hourlyRows: manualConfigHourlyRows(detail),
    workloadPeople,
    workloadBuckets: workloadBuckets(workloadPeople),
  };
}

function resultShiftRows(result: CalculatorResponse): PairShiftSummary[] {
  const rows = new Map<string, PairShiftSummary>();
  result.shift_summary.forEach((row, index) => {
    const label = shiftSummaryLabel(row.shift);
    const existing = rows.get(label) ?? {
      key: label,
      label,
      fl: 0,
      aps: 0,
      acs: 0,
      total: 0,
      sort: index,
    };
    existing.fl += row.fl;
    existing.aps += row.aps;
    existing.acs += row.acs;
    existing.total += row.total;
    existing.sort = Math.min(existing.sort, index);
    rows.set(label, existing);
  });
  return [...rows.values()].sort((first, second) => first.sort - second.sort || first.label.localeCompare(second.label));
}

function resultHourlyRows(result: CalculatorResponse): PairHourSummary[] {
  return hourLabels.map((hour, index) => {
    const currentHour = result.hourly_coverage[index];
    const sectors = (currentHour?.sector_workers ?? [])
      .filter((sector): sector is NonNullable<typeof sector> => Boolean(sector))
      .map((sector) => `${sector.sector_name} ${sector.lower_worker}/${sector.upper_worker}`);
    return {
      hour: currentHour?.hour ?? hour,
      open: currentHour?.open_sectors ?? 0,
      sectors,
      signature: sectors.join('|'),
    };
  });
}

function resultWorkloadPeople(result: CalculatorResponse): PairWorkloadPerson[] {
  return result.people
    .map((person) => ({
      label: personDisplayId(person),
      meta: `${person.license} / ${personShiftLabel(person)} / ${personSourceLabel(person.source)}`,
      hours: person.sector_hours,
    }))
    .sort((first, second) => second.hours - first.hours || first.label.localeCompare(second.label));
}

function metricsFromCalculatorResult(result: CalculatorResponse): PairConfigMetrics {
  const licenseCounts = result.people.reduce<Record<License, number>>((counts, person) => {
    counts[person.license] += 1;
    return counts;
  }, { FL: 0, APS: 0, ACS: 0 });
  const workloadPeople = resultWorkloadPeople(result);
  return {
    id: 'current-calculator-result',
    name: `Trenutni izračun (${result.planned_people} ljudi, ${result.max_sector_hours} SH)`,
    source: 'Kalkulator · ni shranjeno',
    calculatorResult: result,
    sectorHours: result.max_sector_hours,
    people: result.planned_people,
    requiredFl: result.minimum_required_fl,
    utilizationPercent: result.utilization_percent,
    averageHours: result.planned_people > 0
      ? Math.round((result.scheduled_person_hours / result.planned_people) * 10) / 10
      : 0,
    controllerHours: result.scheduled_person_hours,
    licenseText: `FL ${licenseCounts.FL} · APS ${licenseCounts.APS} · ACS ${licenseCounts.ACS}`,
    shiftRows: resultShiftRows(result),
    hourlyRows: resultHourlyRows(result),
    workloadPeople,
    workloadBuckets: workloadBuckets(workloadPeople),
  };
}

function pairDeltaLabel(value: number, suffix = ''): string {
  if (value === 0) {
    return `0${suffix}`;
  }
  return formatSignedValue(value, suffix);
}

function pairDeltaClass(value: number): string {
  const rounded = Math.abs(value) < 0.05 ? 0 : Math.round(value * 10) / 10;
  if (rounded > 0) {
    return 'pair-delta positive';
  }
  if (rounded < 0) {
    return 'pair-delta negative';
  }
  return 'pair-delta neutral';
}

function mapShiftRows(rows: PairShiftSummary[]): Map<string, PairShiftSummary> {
  return new Map(rows.map((row) => [row.key, row]));
}

function mapHourRows(rows: PairHourSummary[]): Map<string, PairHourSummary> {
  return new Map(rows.map((row) => [row.hour, row]));
}

function mapWorkloadBuckets(rows: PairWorkloadBucket[]): Map<number, PairWorkloadBucket> {
  return new Map(rows.map((row) => [row.hours, row]));
}

function manualRoleSectorLimit(
  configuration: ManualConfigurationDetail,
  role: 'V1' | 'V2' | 'V3' | 'FMP',
  fallback: number,
): number {
  const values = configuration.manual_schedule?.people
    .filter((person) => person.role === role)
    .map((person) => numericMetric(person.sector_hours))
    .filter((value): value is number => value !== null) ?? [];
  if (values.length === 0) {
    return fallback;
  }
  const manualLimit = clamp(Math.ceil(Math.max(...values)), 0, 24);
  return clamp(Math.max(fallback, manualLimit), 0, 24);
}

function ManualConfigurationSheet({
  detail,
  requiredSectorHours,
  scheduleResult,
}: {
  detail: ManualConfigurationDetail;
  requiredSectorHours: number;
  scheduleResult: CalculatorResponse | null;
}) {
  const manualPeople = useMemo(() => expandManualPeople(detail.staff_rows), [detail.staff_rows]);
  const excelPeople = useMemo(() => (
    detail.manual_schedule ? manualPeopleFromManualSchedule(detail.manual_schedule) : null
  ), [detail.manual_schedule]);
  const resultPeople = useMemo(() => (scheduleResult ? manualPeopleFromResult(scheduleResult) : null), [scheduleResult]);
  const people = excelPeople ?? resultPeople ?? manualPeople;
  const availabilityBySlot = useMemo(() => (
    hourLabels.map((_, slot) => summarizeManualAvailability(manualPeople, slot))
  ), [manualPeople]);
  const scheduledCells = useMemo(() => {
    if (detail.manual_schedule) {
      return manualScheduledCellsFromManualSchedule(detail.manual_schedule);
    }
    return scheduleResult ? manualScheduledCells(scheduleResult) : null;
  }, [detail.manual_schedule, scheduleResult]);

  if (detail.staff_rows.length === 0) {
    return <p className="demand-help">Konfiguracija nima podprtih izmen za prikaz.</p>;
  }

  const totalRows = Math.max(people.length, HOURS_IN_DAY);
  const modelMax = numericMetric(detail.model_max_sector_hours);
  const reserve = modelMax === null ? null : modelMax - requiredSectorHours;
  const laneColumnStart = 6;
  const scheduleSourceLabel = detail.manual_schedule
    ? `ročni Excel ${detail.manual_schedule.max_sector_hours} SH`
    : scheduleResult
      ? `izračun ${scheduleResult.planned_people} ljudi / ${scheduleResult.max_sector_hours} SH`
      : null;

  return (
    <div className="manual-sheet-shell">
      <div className="manual-sheet-scroll" aria-label="Vizualni list ročne konfiguracije">
        <div
          className="manual-sheet-grid"
          style={{
            gridTemplateColumns: `48px 38px 30px 74px 30px repeat(${manualSheetLanes.length}, minmax(32px, 1fr))`,
          }}
        >
          <div className="manual-sheet-title" style={{ gridColumn: '1 / span 3', gridRow: 1 }}>
            {detail.name}
          </div>
          <div className="manual-sheet-head manual-sheet-loc" style={{ gridColumn: '4 / span 2', gridRow: 1 }}>
            LOC
          </div>
          {manualSheetSections.reduce<Array<{ key: ManualSheetSectionKey; label: string; start: number; lanes: number }>>((items, section) => {
            const previous = items.reduce((sum, item) => sum + item.lanes, 0);
            return [...items, { key: section.key, label: section.label, start: previous, lanes: section.lanes }];
          }, []).map((section) => (
            <div
              className={`manual-sheet-head manual-sheet-section section-${section.key}`}
              key={section.key}
              style={{ gridColumn: `${laneColumnStart + section.start} / span ${section.lanes}`, gridRow: 1 }}
            >
              {section.label}
            </div>
          ))}

          <div className="manual-sheet-subhead" style={{ gridColumn: 1, gridRow: 2 }}>ID</div>
          <div className="manual-sheet-subhead" style={{ gridColumn: 2, gridRow: 2 }}>Izm.</div>
          <div className="manual-sheet-subhead" style={{ gridColumn: 3, gridRow: 2 }}>{detail.manual_schedule ? 'SH' : 'Lic.'}</div>
          <div className="manual-sheet-subhead" style={{ gridColumn: 4, gridRow: 2 }}>Ura</div>
          <div className="manual-sheet-subhead" style={{ gridColumn: 5, gridRow: 2 }}>#</div>
          {manualSheetLanes.map((lane) => (
            <div
              className={`manual-sheet-subhead manual-lane-head section-${lane.sectionKey}`}
              key={`lane-head-${lane.index}`}
              style={{ gridColumn: laneColumnStart + lane.index, gridRow: 2 }}
            >
              {lane.lane + 1}
            </div>
          ))}

          {Array.from({ length: totalRows }, (_, rowIndex) => {
            const gridRow = rowIndex + 3;
            const person = people[rowIndex];
            const availability = availabilityBySlot[rowIndex];
            const scheduled = scheduledCells?.[rowIndex] ?? null;
            const hasHour = rowIndex < HOURS_IN_DAY;
            const isBreak = rowIndex === 7 || rowIndex === 14;
            const availableLaneCount = availability ? Math.min(availability.total, manualSheetLanes.length) : 0;
            const scheduledWorkerCount = scheduled?.filter(Boolean).length ?? null;
            const scheduledSectorCount = detail.manual_schedule?.hourly_coverage[rowIndex]?.open_sectors
              ?? scheduleResult?.hourly_coverage[rowIndex]?.open_sectors
              ?? (scheduledWorkerCount === null ? null : scheduledWorkerCount / 2);
            const availabilityTitle = availability
              ? `Na voljo ${availability.total} ljudi: FL ${availability.fl}, APS ${availability.aps}, ACS ${availability.acs}`
              : undefined;
            return (
              <Fragment key={`manual-sheet-row-${rowIndex}`}>
                <div
                  className={`manual-sheet-roster-cell roster-name ${isBreak ? 'manual-row-break' : ''}`}
                  style={person ? {
                    gridColumn: 1,
                    gridRow,
                    backgroundColor: person.color.background,
                    borderColor: person.color.border,
                    color: person.color.text,
                  } : { gridColumn: 1, gridRow }}
                >
                  {person?.label ?? ''}
                </div>
                <div className={`manual-sheet-roster-cell ${isBreak ? 'manual-row-break' : ''}`} style={{ gridColumn: 2, gridRow }}>
                  {person?.shift ?? ''}
                </div>
                <div className={`manual-sheet-roster-cell license-${person?.license?.toLowerCase() ?? 'empty'} ${isBreak ? 'manual-row-break' : ''}`} style={{ gridColumn: 3, gridRow }}>
                  {person?.metric ?? person?.license ?? ''}
                </div>
                <div className={`manual-sheet-time ${isBreak ? 'manual-row-break' : ''}`} style={{ gridColumn: 4, gridRow }}>
                  {hasHour ? manualHourLabel(rowIndex) : ''}
                </div>
                <div className={`manual-sheet-count ${isBreak ? 'manual-row-break' : ''}`} style={{ gridColumn: 5, gridRow }}>
                  {scheduledSectorCount ?? (availability ? availability.total : '')}
                </div>
                {manualSheetLanes.map((lane) => {
                  const scheduledPerson = scheduled?.[lane.index] ?? null;
                  const isAvailableCapacity = !scheduledCells && hasHour && lane.index < availableLaneCount;
                  const scheduledColor = scheduledPerson
                    ? people.find((item) => item.label.toLowerCase() === scheduledPerson.id.toLowerCase())?.color ?? workerColor(scheduledPerson.id)
                    : null;
                  return (
                    <div
                      className={`manual-sheet-sector-cell section-${lane.sectionKey} ${isBreak ? 'manual-row-break' : ''} ${scheduledPerson ? 'filled' : ''} ${isAvailableCapacity ? 'available' : ''}`}
                      key={`manual-sheet-${rowIndex}-${lane.index}`}
                      style={scheduledPerson && scheduledColor ? {
                        gridColumn: laneColumnStart + lane.index,
                        gridRow,
                        backgroundColor: scheduledColor.background,
                        color: scheduledColor.text,
                      } : { gridColumn: laneColumnStart + lane.index, gridRow }}
                      title={scheduledPerson
                        ? [
                            manualVirtualPersonLabel(scheduledPerson),
                            scheduledPerson.shift,
                            scheduledPerson.license,
                          ].filter(Boolean).join(' · ')
                        : isAvailableCapacity ? availabilityTitle : undefined}
                    >
                      {scheduledPerson ? manualVirtualPersonLabel(scheduledPerson) : ''}
                    </div>
                  );
                })}
              </Fragment>
            );
          })}
        </div>
      </div>

      <div className="manual-sheet-footer">
        <div className="manual-sheet-total">
          <strong>{detail.parsed_total}</strong>
          <span>kontrolorjev v izmenah</span>
        </div>
        <div className="manual-sheet-license-list">
          <span><b>FL</b> {detail.license_counts.FL}</span>
          <span><b>APS</b> {detail.license_counts.APS}</span>
          <span><b>ACS</b> {detail.license_counts.ACS}</span>
          <span><b>Čak.</b> {detail.waiting_count}</span>
        </div>
        <div className="manual-sheet-note">
          <strong>Opombe</strong>
          <span>
            MODEL {detail.model_max_sector_hours ?? '—'} SH · cilj {requiredSectorHours} SH · rezerva {formatReserve(reserve)}
            {scheduleSourceLabel ? ` · ${scheduleSourceLabel}` : ''}
            {detail.manual_schedule
              ? ' · prikazan je ročni razpored iz Excela'
              : scheduleResult ? ' · prikazan je izračunan razpored' : ' · pred izračunom je prikazana razpoložljivost'}
          </span>
        </div>
      </div>
    </div>
  );
}

function UserConfigurationNoteEditor({
  configuration,
  isUpdating,
  onUpdate,
}: {
  configuration: ManualConfigurationDetail;
  isUpdating: boolean;
  onUpdate: (
    configuration: ManualConfigurationDetail,
    updates: { name: string; note: string | null },
  ) => Promise<boolean>;
}) {
  const [noteDraft, setNoteDraft] = useState(configuration.note ?? '');
  const normalizedNote = noteDraft.trim() || null;
  const savedNote = configuration.note?.trim() || null;
  const hasChanges = normalizedNote !== savedNote;
  const isSaved = savedNote !== null && !hasChanges;

  return (
    <div className={isSaved ? 'manual-note-editor saved' : 'manual-note-editor'}>
      <label htmlFor={`manual-note-${configuration.id}`}>
        <strong>Opomba</strong>
        <span>Dodaj pojasnilo, namen ali posebnosti te konfiguracije.</span>
      </label>
      <textarea
        id={`manual-note-${configuration.id}`}
        disabled={isUpdating}
        maxLength={2000}
        onChange={(event) => setNoteDraft(event.target.value)}
        placeholder="Vpiši opombo ..."
        rows={3}
        value={noteDraft}
      />
      <div className="manual-note-actions">
        <small>{isSaved ? 'Shranjeno' : `${noteDraft.length}/2000`}</small>
        <button
          className="secondary-button compact-button"
          disabled={isUpdating || !hasChanges}
          onClick={() => {
            void onUpdate(configuration, {
              name: configuration.name,
              note: normalizedNote,
            });
          }}
          type="button"
        >
          {isUpdating ? 'Shranjujem ...' : 'Shrani opombo'}
        </button>
      </div>
    </div>
  );
}

function ManualConfigurationsPanel({
  library,
  detail,
  currentDemand,
  currentResult,
  manualResultConfigId,
  error,
  isLoading,
  oneDownConfigId,
  exportingExcelConfigId,
  excelExportError,
  deletingConfigId,
  updatingConfigId,
  selectedId,
  onLoadLibrary,
  onSelect,
  onOpenInCalculator,
  onTransferDemandInput,
  onRunOneDown,
  onExportExcel,
  onDeleteUserConfiguration,
  onUpdateUserConfiguration,
}: {
  library: ManualConfigurationLibrary | null;
  detail: ManualConfigurationDetail | null;
  currentDemand: number[];
  currentResult: CalculatorResponse | null;
  manualResultConfigId: string | null;
  error: string | null;
  isLoading: boolean;
  oneDownConfigId: string | null;
  exportingExcelConfigId: string | null;
  excelExportError: string | null;
  deletingConfigId: string | null;
  updatingConfigId: string | null;
  selectedId: string | null;
  onLoadLibrary: () => Promise<void>;
  onSelect: (id: string) => Promise<void>;
  onOpenInCalculator: (configuration: ManualConfigurationDetail) => void;
  onTransferDemandInput: (configuration: ManualConfigurationDetail) => void;
  onRunOneDown: (configuration: ManualConfigurationDetail) => Promise<void>;
  onExportExcel: (configuration: ManualConfigurationDetail) => Promise<void>;
  onDeleteUserConfiguration: (configuration: ManualConfigurationSummary | ManualConfigurationDetail) => Promise<void>;
  onUpdateUserConfiguration: (
    configuration: ManualConfigurationSummary | ManualConfigurationDetail,
    updates: { name: string; note: string | null },
  ) => Promise<boolean>;
}) {
  const [search, setSearch] = useState('');
  const [audit, setAudit] = useState<ManualConfigurationAudit | null>(null);
  const [isAuditLoading, setIsAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [selectedAuditName, setSelectedAuditName] = useState<string | null>(null);
  const [focusNames, setFocusNames] = useState<string[]>(() => {
    try {
      const stored = window.localStorage.getItem(FOCUS_STORAGE_KEY);
      const parsed = stored ? JSON.parse(stored) : null;
      if (Array.isArray(parsed)) {
        const names = parsed.map((name) => String(name).trim()).filter(Boolean);
        return names.length > 0 ? [...new Set(names)] : DEFAULT_FOCUS_CONFIGURATION_NAMES;
      }
    } catch {
      return DEFAULT_FOCUS_CONFIGURATION_NAMES;
    }
    return DEFAULT_FOCUS_CONFIGURATION_NAMES;
  });
  const [focusCandidate, setFocusCandidate] = useState('');
  const [calibration, setCalibration] = useState<ManualFocusCalibration | null>(null);
  const [isCalibrationLoading, setIsCalibrationLoading] = useState(false);
  const [calibrationError, setCalibrationError] = useState<string | null>(null);
  const [editingName, setEditingName] = useState<{ id: string; location: 'list' | 'detail' } | null>(null);
  const [nameDraft, setNameDraft] = useState('');
  const demandSectorHours = currentDemand.reduce((sum, value) => sum + value, 0);
  const [targetSectorHoursInput, setTargetSectorHoursInput] = useState(() => String(demandSectorHours));
  const [targetSectorHoursTouched, setTargetSectorHoursTouched] = useState(false);
  const displayedTargetSectorHours = targetSectorHoursTouched
    ? targetSectorHoursInput
    : String(demandSectorHours);
  const parsedTargetSectorHours = numericMetric(displayedTargetSectorHours);
  const requiredSectorHours = parsedTargetSectorHours === null
    ? demandSectorHours
    : Math.max(0, Math.round(parsedTargetSectorHours));
  const scheduleResult = detail && detail.id === manualResultConfigId ? currentResult : null;

  const beginNameEdit = (
    configuration: ManualConfigurationSummary | ManualConfigurationDetail,
    location: 'list' | 'detail',
  ) => {
    if (configuration.source_type !== 'user' || updatingConfigId === configuration.id) {
      return;
    }
    setNameDraft(configuration.name);
    setEditingName({ id: configuration.id, location });
  };

  const commitNameEdit = async (
    configuration: ManualConfigurationSummary | ManualConfigurationDetail,
  ) => {
    const cleanedName = nameDraft.trim();
    if (!cleanedName || cleanedName === configuration.name) {
      setNameDraft(configuration.name);
      setEditingName(null);
      return;
    }
    const updated = await onUpdateUserConfiguration(configuration, {
      name: cleanedName,
      note: configuration.note ?? null,
    });
    if (updated) {
      setFocusNames((current) => current.map((name) => (name === configuration.name ? cleanedName : name)));
      setEditingName(null);
    }
  };

  useEffect(() => {
    if (!library && !isLoading) {
      void onLoadLibrary();
    }
  }, [isLoading, library, onLoadLibrary]);

  useEffect(() => {
    window.localStorage.setItem(FOCUS_STORAGE_KEY, JSON.stringify(focusNames));
  }, [focusNames]);

  const configurations = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    const items = library?.configurations ?? [];
    const filtered = normalizedSearch
      ? items.filter((item) => item.name.toLowerCase().includes(normalizedSearch))
      : items;
    return [...filtered].sort((first, second) => {
      const firstMax = numericMetric(first.model_max_sector_hours) ?? -1;
      const secondMax = numericMetric(second.model_max_sector_hours) ?? -1;
      return (
        first.parsed_total - second.parsed_total
        || firstMax - secondMax
        || first.name.localeCompare(second.name)
      );
    });
  }, [library?.configurations, search]);

  const bestMatches = useMemo(() => (
    configurations
      .map((item) => {
        const maxHours = numericMetric(item.model_max_sector_hours);
        return {
          ...item,
          reserve: maxHours === null ? null : maxHours - requiredSectorHours,
          distance: maxHours === null ? null : Math.abs(maxHours - requiredSectorHours),
        };
      })
      .filter((item) => item.reserve !== null && item.distance !== null && item.status === 'OK')
      .sort((first, second) => (
        (first.distance ?? 999) - (second.distance ?? 999)
        || (first.reserve !== null && first.reserve >= 0 ? 0 : 1) - (second.reserve !== null && second.reserve >= 0 ? 0 : 1)
        || first.parsed_total - second.parsed_total
        || first.name.localeCompare(second.name)
      ))
      .slice(0, 8)
  ), [configurations, requiredSectorHours]);

  const focusCandidates = useMemo(() => (
    (library?.configurations ?? [])
      .filter((item) => item.has_manual_schedule && !focusNames.includes(item.name))
      .sort((first, second) => (
        first.parsed_total - second.parsed_total
        || first.name.localeCompare(second.name)
      ))
  ), [focusNames, library?.configurations]);
  const activeFocusCandidate = focusCandidate && !focusNames.includes(focusCandidate)
    ? focusCandidate
    : focusCandidates[0]?.name ?? '';

  const auditSummary = useMemo(() => {
    const rows = audit?.rows ?? [];
    const coveredRows = rows.filter((row) => row.status === 'covered').length;
    const sectorMismatchRows = rows.filter((row) => manualAuditHasSectorMismatch(row)).length;
    const shortfallRows = rows.filter((row) => row.status === 'shortfall').length;
    const similarityRows = rows
      .map((row) => row.manual_similarity_percent)
      .filter((value): value is number => typeof value === 'number');
    const averageSimilarity = similarityRows.length > 0
      ? Math.round(similarityRows.reduce((sum, value) => sum + value, 0) / similarityRows.length)
      : null;
    return { total: rows.length, coveredRows, sectorMismatchRows, shortfallRows, averageSimilarity };
  }, [audit?.rows]);

  const selectedAuditRow = useMemo(() => {
    const rows = audit?.rows ?? [];
    if (rows.length === 0) {
      return null;
    }
    return rows.find((row) => row.name === selectedAuditName) ?? rows[0];
  }, [audit?.rows, selectedAuditName]);

  const calibrationBlockingRows = useMemo(() => (
    calibration?.sector_profile_calibration.solver_audit_recommended_profiles?.per_configuration
      ?.filter((row) => row.status !== 'covered')
      ?? []
  ), [calibration]);

  const addFocusConfiguration = () => {
    const name = activeFocusCandidate.trim();
    if (!name || focusNames.includes(name)) {
      return;
    }
    setFocusNames((current) => [...current, name]);
    setCalibration(null);
    setAudit(null);
  };

  const removeFocusConfiguration = (name: string) => {
    setFocusNames((current) => {
      const next = current.filter((item) => item !== name);
      return next.length > 0 ? next : current;
    });
    setCalibration(null);
    setAudit(null);
  };

  const resetFocusConfigurations = () => {
    setFocusNames(DEFAULT_FOCUS_CONFIGURATION_NAMES);
    setCalibration(null);
    setAudit(null);
  };

  const runFocusAudit = async () => {
    if (focusNames.length === 0) {
      setAuditError('Za fokus audit izberi vsaj eno konfiguracijo.');
      return;
    }
    setIsAuditLoading(true);
    setAuditError(null);
    try {
      const response = await getManualConfigurationFocusAudit(3, focusNames);
      setAudit(response);
      setSelectedAuditName(response.rows[0]?.name ?? null);
    } catch (caught) {
      setAuditError(caught instanceof Error ? caught.message : 'Napaka pri primerjavi ročnih konfiguracij.');
    } finally {
      setIsAuditLoading(false);
    }
  };

  const runFocusCalibration = async () => {
    if (focusNames.length === 0) {
      setCalibrationError('Za kalibracijo izberi vsaj eno konfiguracijo.');
      return;
    }
    setIsCalibrationLoading(true);
    setCalibrationError(null);
    try {
      const response = await calibrateManualConfigurationFocus(focusNames, 3, true);
      setCalibration(response);
      const auditResponse = await getManualConfigurationFocusAudit(3, focusNames);
      setAudit(auditResponse);
      setSelectedAuditName(auditResponse.rows[0]?.name ?? null);
    } catch (caught) {
      setCalibrationError(caught instanceof Error ? caught.message : 'Napaka pri kalibraciji fokus konfiguracij.');
    } finally {
      setIsCalibrationLoading(false);
    }
  };

  return (
    <div className="manual-config-page">
      <section className="panel manual-config-hero">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Ročna baza</p>
            <h2>Ročne konfiguracije</h2>
          </div>
          <button className="secondary-button" disabled={isLoading} onClick={() => void onLoadLibrary()} type="button">
            {isLoading ? 'Nalagam ...' : 'Osveži bazo'}
          </button>
        </div>
        <p>
          Baza uporablja obstoječe ročne konfiguracije iz CSV. Konfiguracijo lahko odpreš v kalkulatorju,
          jo popraviš kot vhodne podatke ali jo poženeš proti trenutno aktivni odprtosti.
        </p>
        {library?.source_path ? <small>Vir: {library.source_path}</small> : null}
        {library?.workbook_path ? <small>Excel urniki: {library.workbook_path}</small> : null}
        {library?.user_source_path ? <small>Uporabniške konfiguracije: {library.user_source_path}</small> : null}
      </section>

      {error ? <div className="error-box">{error}</div> : null}

      <section className="panel manual-audit-panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Fokus audit</p>
            <h2>Model proti ročnim konfiguracijam</h2>
          </div>
          <div className="manual-audit-actions">
            <button className="secondary-button" disabled={isAuditLoading || isCalibrationLoading} onClick={() => void runFocusAudit()} type="button">
              {isAuditLoading ? 'Računam ...' : 'Zaženi fokusni audit'}
            </button>
            <button className="primary-button" disabled={isAuditLoading || isCalibrationLoading} onClick={() => void runFocusCalibration()} type="button">
              {isCalibrationLoading ? 'Kalibriram ...' : 'Kalibriraj mehki model'}
            </button>
          </div>
        </div>
        <div className="focus-config-editor">
          <div className="focus-config-editor-head">
            <div>
              <span>Fokus konfiguracije</span>
              <strong>{focusNames.length}</strong>
            </div>
            <button className="secondary-button subtle" onClick={resetFocusConfigurations} type="button">
              Privzeti fokus
            </button>
          </div>
          <div className="focus-chip-list">
            {focusNames.map((name) => (
              <span className="focus-chip" key={name}>
                {name}
                <button
                  aria-label={`Odstrani ${name} iz fokusa`}
                  disabled={focusNames.length <= 1}
                  onClick={() => removeFocusConfiguration(name)}
                  type="button"
                >
                  <TrashIcon />
                </button>
              </span>
            ))}
          </div>
          <div className="focus-add-row">
            <label className="field">
              <span>Dodaj konfiguracijo</span>
              <select value={activeFocusCandidate} onChange={(event) => setFocusCandidate(event.target.value)}>
                {focusCandidates.map((item) => (
                  <option key={item.id} value={item.name}>
                    {item.name} · {item.parsed_total} ljudi · {formatAuditMetric(item.model_max_sector_hours)} SH
                  </option>
                ))}
              </select>
            </label>
            <button className="secondary-button" disabled={!activeFocusCandidate} onClick={addFocusConfiguration} type="button">
              Dodaj v fokus
            </button>
          </div>
          <p>
            Kalibracija se sprejme samo, če po učenju vseh izbranih fokus konfiguracij pokrije 100 %. Trda pravila se pri tem ne spreminjajo.
          </p>
        </div>
        {auditError ? <div className="error-box">{auditError}</div> : null}
        {calibrationError ? <div className="error-box">{calibrationError}</div> : null}
        {calibration ? (
          <div className={`focus-calibration-card ${calibration.success ? 'accepted' : 'rejected'}`}>
            <div>
              <p className="eyebrow">Kalibracija</p>
              <h3>{calibration.success ? 'Sprejeto' : 'Zavrnjeno'}</h3>
              <p>{calibration.message}</p>
            </div>
            <div className="focus-calibration-metrics">
              <span>
                <small>Manjko prej</small>
                <strong>{formatAuditMetric(calibration.current_missing_sector_hours)} SH</strong>
              </span>
              <span>
                <small>Manjko po učenju</small>
                <strong>{formatAuditMetric(calibration.recommended_missing_sector_hours)} SH</strong>
              </span>
              <span>
                <small>Profili sektorjev</small>
                <strong>
                  {formatAuditMetric(calibration.sector_profile_calibration.profile_score_current.exact_match_percent, '%')}
                  {' → '}
                  {formatAuditMetric(calibration.sector_profile_calibration.profile_score_recommended.exact_match_percent, '%')}
                </strong>
              </span>
              <span>
                <small>Licence fokusa</small>
                <strong>
                  FL {calibration.composition.license_ratio_percent.FL}% · APS {calibration.composition.license_ratio_percent.APS}% · ACS {calibration.composition.license_ratio_percent.ACS}%
                </strong>
              </span>
              <span>
                <small>Uporabljeno</small>
                <strong>{calibration.applied ? 'Da' : 'Ne'}</strong>
              </span>
            </div>
            {calibrationBlockingRows.length > 0 ? (
              <div className="focus-calibration-blockers">
                <strong>Blokira 100 % pokritost:</strong>
                {calibrationBlockingRows.map((row) => (
                  <span key={row.name}>
                    {row.name}: {formatAuditMetric(row.model_sector_hours)}/{formatAuditMetric(row.manual_sector_hours)} SH,
                    manjka {formatAuditMetric(row.missing_sector_hours)}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
        {audit ? (
          <>
            <div className="manual-audit-summary">
              <div>
                <span>Konfiguracij</span>
                <strong>{auditSummary.total}</strong>
              </div>
              <div>
                <span>Pokrito</span>
                <strong>{auditSummary.coveredRows}</strong>
              </div>
              <div>
                <span>Sektor ≠ Excel</span>
                <strong>{auditSummary.sectorMismatchRows}</strong>
              </div>
              <div>
                <span>Manjko modela</span>
                <strong>{auditSummary.shortfallRows}</strong>
              </div>
              <div>
                <span>Podobnost</span>
                <strong>{formatAuditMetric(auditSummary.averageSimilarity, '%')}</strong>
              </div>
              <div>
                <span>Čas</span>
                <strong>{audit.elapsed_seconds.toFixed(2)} s</strong>
              </div>
            </div>
            <div className="responsive-table manual-audit-table">
              <table>
                <thead>
                  <tr>
                    <th>Konf.</th>
                    <th>Ljudje</th>
                    <th>Excel SH</th>
                    <th>Model zdaj</th>
                    <th>Manjka</th>
                    <th>Pokritost</th>
                    <th>Podobnost</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.rows.map((row) => (
                    <tr
                      className={`${manualAuditRowClass(row)} ${selectedAuditRow?.name === row.name ? 'selected-audit-row' : ''}`}
                      key={row.name}
                      onClick={() => {
                        setSelectedAuditName(row.name);
                        if (row.id) {
                          void onSelect(row.id);
                        }
                      }}
                    >
                      <td className="strong">{row.name}</td>
                      <td>{formatAuditMetric(row.parsed_total)}</td>
                      <td>{formatAuditMetric(row.manual_sector_hours)}</td>
                      <td>{formatAuditMetric(row.model_sector_hours)}</td>
                      <td>{formatAuditMetric(row.model_missing_sector_hours)}</td>
                      <td>{formatAuditMetric(row.model_coverage_percent, '%')}</td>
                      <td>{formatAuditMetric(row.manual_similarity_percent, '%')}</td>
                      <td>{manualAuditStatusLabel(row)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {selectedAuditRow ? (
              <div className="manual-audit-detail">
                <div className="manual-audit-detail-head">
                  <div>
                    <p className="eyebrow">Urni detail</p>
                    <h3>{selectedAuditRow.name}</h3>
                  </div>
                  <div className="manual-audit-detail-metrics">
                    <span>Excel {formatAuditMetric(selectedAuditRow.manual_sector_hours)} SH</span>
                    <span>Model {formatAuditMetric(selectedAuditRow.model_sector_hours)} SH</span>
                    <span>Manjka {formatAuditMetric(selectedAuditRow.model_missing_sector_hours)} SH</span>
                    <span>Podobnost {formatAuditMetric(selectedAuditRow.manual_similarity_percent, '%')}</span>
                    <span>Ljudje Δ {formatAuditMetric(selectedAuditRow.manual_similarity_people_diff)}</span>
                    <span>Profil Δ {formatAuditMetric(selectedAuditRow.manual_similarity_sector_profile_diff)}</span>
                    <span>Workload Δ {formatAuditMetric(selectedAuditRow.manual_similarity_workload_diff)}</span>
                    <span>Solver {selectedAuditRow.solver_status ?? '—'}</span>
                  </div>
                </div>

                <div className="manual-audit-hour-grid">
                  {(selectedAuditRow.hourly_comparison ?? []).map((hour) => (
                    <div
                      className={manualAuditHourClass(hour.diff, hour.missing_sectors, hour.extra_sectors)}
                      key={hour.hour}
                      title={`Excel ${hour.manual}, model ${hour.model}`}
                    >
                      <span>{hour.hour}</span>
                      <strong>{hour.manual}/{hour.model}</strong>
                    </div>
                  ))}
                </div>

                <div className="responsive-table manual-audit-sector-table">
                  <table>
                    <thead>
                      <tr>
                        <th>Ura</th>
                        <th>SH</th>
                        <th>Excel sektorji</th>
                        <th>Model sektorji</th>
                        <th>Manjka</th>
                        <th>Dodatni</th>
                        <th>Delavci</th>
                        <th>Celice sektorjev</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(selectedAuditRow.hourly_comparison ?? []).map((hour) => (
                        <tr key={hour.hour}>
                          <td className="strong">{hour.hour}</td>
                          <td>{hour.manual}/{hour.model}</td>
                          <td>{formatAuditList(hour.manual_sectors)}</td>
                          <td>{formatAuditList(hour.model_sectors)}</td>
                          <td>{formatAuditList(hour.missing_sectors)}</td>
                          <td>{formatAuditList(hour.extra_sectors)}</td>
                          <td>{formatAuditMetric(hour.manual_workers)} / {formatAuditMetric(hour.model_workers)}</td>
                          <td>
                            <div className="audit-sector-cell-list">
                              {(hour.sectors ?? [])
                                .filter((sector) => sector.status !== 'closed')
                                .map((sector) => (
                                  <span
                                    className={manualAuditSectorClass(sector.status)}
                                    key={sector.sector_name}
                                    title={`Excel: ${sector.manual ?? '—'} | Model: ${sector.model ?? '—'}`}
                                  >
                                    {sector.sector_name}
                                  </span>
                                ))}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <div className="manual-audit-empty">
            Fokus je pripravljen. Začni z auditom ali kalibracijo mehkih profilov.
          </div>
        )}
      </section>

      <section className="panel manual-config-layout">
        <div className="manual-config-list" data-tour="manual-config-list">
          <div className="manual-config-toolbar">
            <label className="field">
              <span>Iskanje</span>
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="npr. 25n5" />
            </label>
            <label className="manual-current-demand">
              <span>Aktivni cilj</span>
              <span className="manual-current-demand-input">
                <input
                  min={0}
                  onChange={(event) => {
                    setTargetSectorHoursTouched(true);
                    setTargetSectorHoursInput(event.target.value);
                  }}
                  type="number"
                  value={displayedTargetSectorHours}
                  onKeyDown={preventNumberInputArrowStep}
                />
                <strong>SH</strong>
              </span>
            </label>
          </div>

          {bestMatches.length > 0 ? (
            <div className="manual-match-strip" aria-label="Najbližje ročne konfiguracije">
              {bestMatches.map((item) => (
                <button
                  className={selectedId === item.id ? 'manual-match active' : 'manual-match'}
                  key={item.id}
                  onClick={() => void onSelect(item.id)}
                  type="button"
                >
                  <strong>{item.name}</strong>
                  <span>
                    {item.source_type === 'user' ? 'uporabniška · ' : ''}
                    {item.parsed_total} ljudi · {item.model_max_sector_hours} SH · rezerva {formatReserve(item.reserve)}
                  </span>
                </button>
              ))}
            </div>
          ) : null}

          <div className="responsive-table manual-config-table">
            <table>
              <thead>
                <tr>
                  <th>Konf.</th>
                  <th>Ljudje</th>
                  <th>FL</th>
                  <th>APS</th>
                  <th>ACS</th>
                  <th>MODEL SH</th>
                  <th>Rezerva</th>
                  <th>Vir</th>
                  <th>Status</th>
                  <th>Akcija</th>
                </tr>
              </thead>
              <tbody>
                {configurations.map((item) => {
                  const maxHours = numericMetric(item.model_max_sector_hours);
                  const reserve = maxHours === null ? null : maxHours - requiredSectorHours;
                  return (
                    <tr
                      className={selectedId === item.id ? 'selected-config-row' : ''}
                      key={item.id}
                      onClick={() => void onSelect(item.id)}
                    >
                      <td
                        className={item.source_type === 'user' ? 'strong editable-config-name' : 'strong'}
                        onDoubleClick={(event) => {
                          if (item.source_type !== 'user') {
                            return;
                          }
                          event.stopPropagation();
                          beginNameEdit(item, 'list');
                        }}
                        title={item.source_type === 'user' ? 'Dvoklikni za preimenovanje' : undefined}
                      >
                        {editingName?.id === item.id && editingName.location === 'list' ? (
                          <input
                            aria-label={`Novo ime konfiguracije ${item.name}`}
                            autoFocus
                            className="manual-name-input"
                            disabled={updatingConfigId === item.id}
                            maxLength={120}
                            onBlur={() => void commitNameEdit(item)}
                            onChange={(event) => setNameDraft(event.target.value)}
                            onClick={(event) => event.stopPropagation()}
                            onDoubleClick={(event) => event.stopPropagation()}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') {
                                event.preventDefault();
                                event.currentTarget.blur();
                              } else if (event.key === 'Escape') {
                                event.preventDefault();
                                setNameDraft(item.name);
                                setEditingName(null);
                              }
                            }}
                            value={nameDraft}
                          />
                        ) : (
                          <>
                            <span>{item.name}</span>
                            {item.source_type === 'user' && item.is_complete === false ? (
                              <small className="manual-incomplete-label">
                                Nepopolna · manjka {item.missing_sector_hours ?? '?'} SH
                              </small>
                            ) : null}
                          </>
                        )}
                      </td>
                      <td>{item.parsed_total}</td>
                      <td>{item.license_counts.FL}</td>
                      <td>{item.license_counts.APS}</td>
                      <td>{item.license_counts.ACS}</td>
                      <td>{item.model_max_sector_hours ?? '—'}</td>
                      <td>{formatReserve(reserve)}</td>
                      <td>{item.source_type === 'user' ? 'Uporabnik' : 'Excel'}</td>
                      <td>{item.status}</td>
                      <td>
                        {item.source_type === 'user' ? (
                          <button
                            aria-label={`Izbriši konfiguracijo ${item.name}`}
                            className="secondary-button compact-button danger-button trash-button"
                            disabled={deletingConfigId === item.id}
                            onClick={(event) => {
                              event.stopPropagation();
                              void onDeleteUserConfiguration(item);
                            }}
                            title={`Izbriši konfiguracijo ${item.name}`}
                            type="button"
                          >
                            <TrashIcon />
                          </button>
                        ) : (
                          <span className="muted-cell">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="manual-config-detail">
          {detail ? (
            <>
              <div className="panel-header compact">
                <div>
                  <p className="eyebrow">Izbrana konfiguracija</p>
                  {editingName?.id === detail.id && editingName.location === 'detail' ? (
                    <input
                      aria-label={`Novo ime konfiguracije ${detail.name}`}
                      autoFocus
                      className="manual-detail-name-input"
                      disabled={updatingConfigId === detail.id}
                      maxLength={120}
                      onBlur={() => void commitNameEdit(detail)}
                      onChange={(event) => setNameDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          event.currentTarget.blur();
                        } else if (event.key === 'Escape') {
                          event.preventDefault();
                          setNameDraft(detail.name);
                          setEditingName(null);
                        }
                      }}
                      value={nameDraft}
                    />
                  ) : (
                    <h2
                      className={detail.source_type === 'user' ? 'editable-config-name' : undefined}
                      onDoubleClick={() => beginNameEdit(detail, 'detail')}
                      title={detail.source_type === 'user' ? 'Dvoklikni za preimenovanje' : undefined}
                    >
                      {detail.name}
                    </h2>
                  )}
                  {detail.source_type === 'user' ? (
                    <span className={detail.is_complete === false ? 'manual-source-badge incomplete' : 'manual-source-badge'}>
                      {detail.is_complete === false
                        ? `Nepopolna · ${detail.model_max_sector_hours}/${detail.requested_sector_hours ?? '?'} SH · manjka ${detail.missing_sector_hours ?? '?'} SH`
                        : 'Shranjena s strani uporabnika'}
                    </span>
                  ) : null}
                </div>
                <div className="panel-actions">
                  <button
                    className="secondary-button compact-button"
                    disabled={
                      exportingExcelConfigId === detail.id
                      || !(scheduleResult ?? detail.calculator_result)
                    }
                    onClick={() => void onExportExcel(detail)}
                    type="button"
                  >
                    {exportingExcelConfigId === detail.id ? 'Pripravljam Excel ...' : 'Izvozi Excel'}
                  </button>
                  <button
                    className="secondary-button compact-button"
                    onClick={() => onOpenInCalculator(detail)}
                    type="button"
                  >
                    Odpri v ATCConfMakerju
                  </button>
                  <button
                    className="secondary-button compact-button manual-transfer-button"
                    data-tour="manual-transfer-demand"
                    onClick={() => onTransferDemandInput(detail)}
                    type="button"
                  >
                    Prenesi sektorske ure in št. ljudi v ATCConfMaker
                  </button>
                  <button
                    className="secondary-button compact-button"
                    disabled={oneDownConfigId === detail.id || detail.parsed_total <= 1}
                    onClick={() => void onRunOneDown(detail)}
                    type="button"
                  >
                    {oneDownConfigId === detail.id ? 'Računam one-down ...' : 'Izračunaj možnost one-down'}
                  </button>
                  {detail.source_type === 'user' ? (
                    <button
                      aria-label={`Izbriši konfiguracijo ${detail.name}`}
                      className="secondary-button compact-button danger-button trash-button"
                      disabled={deletingConfigId === detail.id}
                      onClick={() => void onDeleteUserConfiguration(detail)}
                      title={`Izbriši konfiguracijo ${detail.name}`}
                      type="button"
                    >
                      <TrashIcon />
                    </button>
                  ) : null}
                </div>
              </div>

              {excelExportError ? <div className="error-box">{excelExportError}</div> : null}

              {detail.source_type === 'user' ? (
                <UserConfigurationNoteEditor
                  configuration={detail}
                  isUpdating={updatingConfigId === detail.id}
                  key={`${detail.id}:${detail.note ?? ''}`}
                  onUpdate={onUpdateUserConfiguration}
                />
              ) : null}

              {detail.unsupported_rows.length > 0 ? (
                <div className="error-box">
                  Nepodprte vrstice: {detail.unsupported_rows.join(', ')}
                </div>
              ) : null}

              <ManualConfigurationSheet
                detail={detail}
                requiredSectorHours={requiredSectorHours}
                scheduleResult={scheduleResult}
              />
            </>
          ) : (
            <div className="empty-state manual-empty">
              <div className="empty-icon">⌁</div>
              <h2>{isLoading ? 'Nalagam bazo konfiguracij' : 'Izberi konfiguracijo'}</h2>
              <p>Detail bo pokazal ljudi po izmenah, licencah in urah.</p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function cleanCopyCell(value: string | number | null | undefined): string {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

function toTsv(rows: (string | number | null | undefined)[][]): string {
  return rows.map((row) => row.map(cleanCopyCell).join('\t')).join('\n');
}

const resultCsvMarker = '__KONFMAKER_RESULT_JSON__';

function csvEscape(value: string | number | null | undefined): string {
  const text = String(value ?? '');
  if (/[",\n\r;]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

function toCsv(rows: (string | number | null | undefined)[][]): string {
  return rows.map((row) => row.map(csvEscape).join(';')).join('\n');
}

function parseCsvLine(line: string): string[] {
  const cells: string[] = [];
  let current = '';
  let inQuotes = false;

  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    const next = line[index + 1];
    if (char === '"' && inQuotes && next === '"') {
      current += '"';
      index += 1;
    } else if (char === '"') {
      inQuotes = !inQuotes;
    } else if ((char === ';' || char === ',') && !inQuotes) {
      cells.push(current);
      current = '';
    } else {
      current += char;
    }
  }
  cells.push(current);
  return cells;
}

function downloadTextFile(filename: string, text: string, mimeType: string): void {
  const blob = new Blob([text], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function downloadBlobFile(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function safeFilenamePart(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase();
}

function isCalculatorResponse(value: unknown): value is CalculatorResponse {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const candidate = value as Partial<CalculatorResponse>;
  return (
    typeof candidate.max_sector_hours === 'number'
    && Array.isArray(candidate.people)
    && Array.isArray(candidate.shift_summary)
    && Array.isArray(candidate.hourly_coverage)
    && Array.isArray(candidate.notes)
    && Array.isArray(candidate.warnings)
  );
}

function personShiftLabel(person: CalculatorResponse['people'][number]): string {
  return person.role ? `${person.role}/${person.shift}` : person.shift;
}

function shiftSummaryLabel(shift: string): string {
  return shift.replace(/^(officer|office-pool)\//, '');
}

function personSourceLabel(source: string): string {
  return source === 'officer' || source === 'office-pool' ? 'office' : source;
}

function isOfficeSource(source: string | undefined): boolean {
  return source === 'officer' || source === 'office-pool';
}

function leaderRoleDisplayId(role: string | null | undefined): string | null {
  if (role === 'V1') {
    return 'Vi1';
  }
  if (role === 'V2') {
    return 'Vi2';
  }
  if (role === 'V3') {
    return 'Vi3';
  }
  return null;
}

function personDisplayId(person: Pick<VirtualPerson, 'id' | 'role'>): string {
  return leaderRoleDisplayId(person.role) ?? person.id;
}

function shiftStartLabel(shift: string): string {
  const match = shift.match(/^A(\d{1,2})o?$/);
  if (!match) {
    return '';
  }
  return `${match[1].padStart(2, '0')}:00`;
}

function normalizedWorkerId(workerId: string): string {
  return workerId.trim();
}

function seatWorker(sector: SectorAssignment, seat: ScheduleSeat): string {
  return seat === 'lower' ? sector.lower_worker : sector.upper_worker;
}

function setSeatWorker(sector: SectorAssignment, seat: ScheduleSeat, workerId: string): SectorAssignment {
  return seat === 'lower'
    ? { ...sector, lower_worker: workerId }
    : { ...sector, upper_worker: workerId };
}

function workerIdsForSector(sector: SectorAssignment | null): string[] {
  if (!sector) {
    return [];
  }
  return [sector.lower_worker, sector.upper_worker].map(normalizedWorkerId).filter(Boolean);
}

function canLicenseFillSector(license: License | undefined, sectorName: string): boolean {
  if (!license) {
    return false;
  }
  if (sectorName === 'ALL') {
    return license === 'FL';
  }
  if (sectorName === 'LOWER') {
    return license === 'FL' || license === 'APS';
  }
  return license === 'FL' || license === 'ACS';
}

function isCoveredSector(sector: SectorAssignment | null, peopleById: Map<string, VirtualPerson>): boolean {
  if (!sector) {
    return false;
  }
  const lowerWorker = normalizedWorkerId(sector.lower_worker);
  const upperWorker = normalizedWorkerId(sector.upper_worker);
  if (!lowerWorker || !upperWorker || lowerWorker === upperWorker) {
    return false;
  }
  return (
    canLicenseFillSector(peopleById.get(lowerWorker)?.license, sector.sector_name)
    && canLicenseFillSector(peopleById.get(upperWorker)?.license, sector.sector_name)
  );
}

function maxSectorHoursForDuration(durationHours: number, maxConsecutive = 2, restAfterMax = 1): number {
  if (durationHours <= 0) {
    return 0;
  }
  let worked = 0;
  let cursor = 0;
  while (cursor < durationHours) {
    const block = Math.min(maxConsecutive, durationHours - cursor);
    worked += block;
    cursor += block + restAfterMax;
  }
  return worked;
}

function estimatedMaxSectorHoursForShift(shift: string, shifts: ShiftRule[]): number {
  const normalizedShift = shift === 'V3' ? 'A21' : shift;
  const rule = [...shifts, ...fallbackSettings.officer_shifts].find((item) => item.code === normalizedShift);
  if (rule) {
    return maxSectorHoursForDuration(rule.duration_hours);
  }
  const match = normalizedShift.match(/^A(\d{1,2})o?$/);
  if (!match) {
    return 5;
  }
  return Number(match[1]) === 21 ? 7 : 5;
}

function shiftRuleForPerson(person: VirtualPerson, shifts: ShiftRule[]): ShiftRule | null {
  const normalizedShift = person.shift === 'V3' ? 'A21' : person.shift;
  const knownShift = [
    ...shifts,
    { code: 'V3', start_hour: 21, duration_hours: 10, enabled: true },
    ...fallbackSettings.officer_shifts,
  ].find((shift) => shift.code === normalizedShift);
  if (knownShift) {
    return knownShift;
  }
  const match = normalizedShift.match(/^A(\d{1,2})o?$/);
  if (!match) {
    return null;
  }
  return {
    code: normalizedShift,
    start_hour: Number(match[1]),
    duration_hours: normalizedShift.endsWith('o') ? 8 : Number(match[1]) === 14 ? 7 : 8,
    enabled: true,
  };
}

function shiftSlotViolation(person: VirtualPerson, slot: number, shifts: ShiftRule[]): string | null {
  const rule = shiftRuleForPerson(person, shifts);
  if (!rule) {
    return `Izmena ${person.shift} ni definirana v pravilih.`;
  }
  if (rule.enabled === false) {
    return `Izmena ${person.shift} je izključena v nastavitvah pravil.`;
  }
  const hour = (DAY_START + slot) % HOURS_IN_DAY;
  const elapsed = (hour - rule.start_hour + HOURS_IN_DAY) % HOURS_IN_DAY;
  if (elapsed >= rule.duration_hours) {
    return `${personDisplayId(person)} v tej uri ni v svoji izmeni ${person.shift}.`;
  }
  return null;
}

function personIsActiveInSlot(person: VirtualPerson, slot: number, shifts: ShiftRule[]): boolean {
  const rule = shiftRuleForPerson(person, shifts);
  if (!rule || rule.enabled === false) {
    return false;
  }
  const hour = (DAY_START + slot) % HOURS_IN_DAY;
  const elapsed = (hour - rule.start_hour + HOURS_IN_DAY) % HOURS_IN_DAY;
  return elapsed < rule.duration_hours;
}

function roleSectorSlotViolation(person: VirtualPerson, slot: number, shifts: ShiftRule[]): string | null {
  if (!['V1', 'V2', 'V3'].includes(person.role ?? '')) {
    return null;
  }
  const rule = shiftRuleForPerson(person, shifts);
  if (!rule) {
    return `Za vlogo ${person.role} ni mogoče določiti izmene.`;
  }
  const hour = (DAY_START + slot) % HOURS_IN_DAY;
  const elapsed = (hour - rule.start_hour + HOURS_IN_DAY) % HOURS_IN_DAY;
  if (elapsed < 0 || elapsed >= rule.duration_hours) {
    return `${personDisplayId(person)} v tej uri ni v svoji izmeni ${person.shift}.`;
  }
  const roleLabel = leaderRoleDisplayId(person.role) ?? person.role;
  if ((person.role === 'V1' || person.role === 'V2') && elapsed === 0) {
    return `${roleLabel} ne sme delati prve ure svoje izmene.`;
  }
  if ((person.role === 'V1' || person.role === 'V2') && elapsed === rule.duration_hours - 1) {
    return `${roleLabel} ne sme delati zadnje ure svoje izmene.`;
  }
  if (person.role === 'V3' && elapsed === 0) {
    return `${roleLabel} ne sme delati prve ure svoje izmene.`;
  }
  return null;
}

function scheduledSlotsForWorker(result: CalculatorResponse, workerId: string): Set<number> {
  const slots = new Set<number>();
  result.hourly_coverage.forEach((hour, slot) => {
    if (hour.sector_workers.some((sector) => workerIdsForSector(sector).includes(workerId))) {
      slots.add(slot);
    }
  });
  return slots;
}

function workerSeatCountInSlot(result: CalculatorResponse, workerId: string, slot: number): number {
  const hour = result.hourly_coverage[slot];
  if (!hour) {
    return 0;
  }
  return hour.sector_workers.reduce((count, sector) => (
    count + workerIdsForSector(sector).filter((currentWorkerId) => currentWorkerId === workerId).length
  ), 0);
}

function wouldExceedMaxConsecutiveSectorHours(
  result: CalculatorResponse,
  workerId: string,
  slot: number,
  maxConsecutive = 2,
): boolean {
  const slots = scheduledSlotsForWorker(result, workerId);
  slots.add(slot);
  let consecutive = 1;
  for (let offset = 1; offset < HOURS_IN_DAY; offset += 1) {
    if (!slots.has((slot - offset + HOURS_IN_DAY) % HOURS_IN_DAY)) {
      break;
    }
    consecutive += 1;
  }
  for (let offset = 1; offset < HOURS_IN_DAY; offset += 1) {
    if (!slots.has((slot + offset) % HOURS_IN_DAY)) {
      break;
    }
    consecutive += 1;
  }
  return consecutive > maxConsecutive;
}

function sectorSlotViolations(
  result: CalculatorResponse,
  workerId: string,
  slot: number,
  sectorName: string,
  peopleById: Map<string, VirtualPerson>,
  shifts: ShiftRule[],
): string[] {
  const person = peopleById.get(workerId);
  if (!person) {
    return ['Oseba ni v seznamu navideznih ljudi.'];
  }
  const violations: string[] = [];
  if (!canLicenseFillSector(person.license, sectorName)) {
    violations.push(`Licenca ${person.license} ne ustreza sektorju ${sectorName}.`);
  }
  const shiftViolation = shiftSlotViolation(person, slot, shifts);
  if (shiftViolation) {
    violations.push(shiftViolation);
  }
  const roleViolation = roleSectorSlotViolation(person, slot, shifts);
  if (roleViolation) {
    violations.push(roleViolation);
  }
  if (wouldExceedMaxConsecutiveSectorHours(result, workerId, slot)) {
    violations.push('Kršitev ritma 2-1-2: več kot 2 uri zapored na sektorju.');
  }
  if (workerSeatCountInSlot(result, workerId, slot) > 1) {
    violations.push(`${personDisplayId(person)} je v tej uri že razporejen na drugem sektorju.`);
  }
  return violations;
}

function sectorCellViolations(
  result: CalculatorResponse,
  sector: SectorAssignment | null,
  slot: number,
  peopleById: Map<string, VirtualPerson>,
  shifts: ShiftRule[],
): string[] {
  if (!sector) {
    return [];
  }
  const lowerWorker = normalizedWorkerId(sector.lower_worker);
  const upperWorker = normalizedWorkerId(sector.upper_worker);
  const violations: string[] = [];
  if (!lowerWorker || !upperWorker) {
    violations.push(`${sector.sector_name}: manjka ${!lowerWorker ? 'levi' : 'desni'} kontrolor.`);
  }
  if (lowerWorker && upperWorker && lowerWorker === upperWorker) {
    violations.push(`${sector.sector_name}: ista oseba je vpisana na oba sedeža.`);
  }
  [lowerWorker, upperWorker].filter(Boolean).forEach((workerId) => {
    sectorSlotViolations(result, workerId, slot, sector.sector_name, peopleById, shifts).forEach((violation) => {
      if (!violations.includes(violation)) {
        violations.push(violation);
      }
    });
  });
  return violations;
}

function shiftSummaryKeyForPerson(person: VirtualPerson): string {
  if (isOfficeSource(person.source)) {
    return `officer/${person.shift}`;
  }
  return person.role ? `${person.role}/${person.shift}` : person.shift;
}

function recomputeEditedResult(result: CalculatorResponse, shifts: ShiftRule[]): CalculatorResponse {
  const initialPeopleById = new Map(result.people.map((person) => [person.id, person]));
  const scheduledHoursById = new Map<string, number>();
  const nextHourlyCoverage = result.hourly_coverage.map((hour) => {
    const workers = hour.sector_workers.flatMap(workerIdsForSector);
    workers.forEach((workerId) => {
      scheduledHoursById.set(workerId, (scheduledHoursById.get(workerId) ?? 0) + 1);
    });
    const peopleById = new Map(
      result.people.map((person) => [
        person.id,
        { ...person, sector_hours: scheduledHoursById.get(person.id) ?? person.sector_hours },
      ]),
    );
    const openSectors = hour.sector_workers.filter((sector) => isCoveredSector(sector, peopleById)).length;
    return {
      ...hour,
      open_sectors: openSectors,
      workers,
    };
  });
  const nextPeople = result.people.map((person) => {
    const sectorHours = scheduledHoursById.get(person.id) ?? 0;
    const maxSectorHours = person.max_sector_hours || estimatedMaxSectorHoursForShift(person.shift, shifts);
    return {
      ...person,
      sector_hours: sectorHours,
      used_as_sector_controller: sectorHours > 0,
      max_sector_hours: maxSectorHours,
      utilization_percent: maxSectorHours > 0 ? Math.round((sectorHours / maxSectorHours) * 100) : 0,
    };
  });
  const shiftCounters = new Map<string, Record<License, number>>();
  nextPeople.forEach((person) => {
    const key = shiftSummaryKeyForPerson(person);
    const counter = shiftCounters.get(key) ?? { FL: 0, APS: 0, ACS: 0 };
    counter[person.license] += 1;
    shiftCounters.set(key, counter);
  });
  const shiftOrder = [...fallbackSettings.shifts, { code: 'V3', start_hour: 21, duration_hours: 10 }, ...fallbackSettings.officer_shifts]
    .map((shift) => shift.code);
  const shiftSummary = [...shiftCounters.entries()]
    .sort(([first], [second]) => {
      const firstParts = first.split('/');
      const secondParts = second.split('/');
      const firstShift = firstParts[firstParts.length - 1] ?? first;
      const secondShift = secondParts[secondParts.length - 1] ?? second;
      const firstIndex = shiftOrder.indexOf(firstShift);
      const secondIndex = shiftOrder.indexOf(secondShift);
      return (firstIndex === -1 ? 999 : firstIndex) - (secondIndex === -1 ? 999 : secondIndex)
        || first.localeCompare(second);
    })
    .map(([shift, counter]) => ({
      shift,
      fl: counter.FL,
      aps: counter.APS,
      acs: counter.ACS,
      total: counter.FL + counter.APS + counter.ACS,
    }));
  const maxSectorHours = nextHourlyCoverage.reduce((sum, hour) => sum + hour.open_sectors, 0);
  const scheduledPersonHours = nextPeople.reduce((sum, person) => sum + person.sector_hours, 0);
  const totalPersonCapacityHours = nextPeople.reduce((sum, person) => sum + person.max_sector_hours, 0);
  const activePeople = nextPeople.filter((person) => person.sector_hours > 0).length;
  const notes = result.notes.includes('Rezultat je ročno urejen po izračunu.')
    ? result.notes
    : ['Rezultat je ročno urejen po izračunu.', ...result.notes];

  initialPeopleById.forEach((_person, personId) => {
    if (!nextPeople.some((person) => person.id === personId)) {
      scheduledHoursById.delete(personId);
    }
  });

  return {
    ...result,
    max_sector_hours: maxSectorHours,
    missing_sector_hours: Math.max(0, result.requested_sector_hours - maxSectorHours),
    planned_people: nextPeople.length,
    active_people: activePeople,
    unused_people: Math.max(0, nextPeople.length - activePeople),
    scheduled_person_hours: scheduledPersonHours,
    total_person_capacity_hours: totalPersonCapacityHours,
    utilization_percent: totalPersonCapacityHours > 0 ? Math.round((scheduledPersonHours / totalPersonCapacityHours) * 100) : 0,
    people: nextPeople,
    shift_summary: shiftSummary,
    hourly_coverage: nextHourlyCoverage,
    notes,
  };
}

function nextGeneratedPersonId(people: VirtualPerson[]): string {
  const used = new Set(people.map((person) => person.id.toUpperCase()));
  let index = 0;
  while (index < 200) {
    const label = labelForPerson(index);
    if (!used.has(label.toUpperCase())) {
      return label;
    }
    index += 1;
  }
  return `X${people.length + 1}`;
}

function sectorNameForIndex(index: number): string {
  return sectorColumnLabels[index] ?? `EXTRA ${index + 1}`;
}

function normalizeEditedSector(sector: SectorAssignment | null): SectorAssignment | null {
  if (!sector) {
    return null;
  }
  return workerIdsForSector(sector).length === 0 ? null : sector;
}

function parseScheduleSeatRef(value: string): ScheduleSeatRef | null {
  try {
    const parsed = JSON.parse(value) as Partial<ScheduleSeatRef>;
    if (
      typeof parsed.slot === 'number'
      && typeof parsed.sectorIndex === 'number'
      && (parsed.seat === 'lower' || parsed.seat === 'upper')
    ) {
      return { slot: parsed.slot, sectorIndex: parsed.sectorIndex, seat: parsed.seat };
    }
  } catch {
    return null;
  }
  return null;
}

function cloneSectorWorkers(result: CalculatorResponse): Array<Array<SectorAssignment | null>> {
  return result.hourly_coverage.map((hour) => (
    hour.sector_workers.map((sector) => (sector ? { ...sector } : null))
  ));
}

function resultWithSectorWorkers(
  result: CalculatorResponse,
  sectorWorkersByHour: Array<Array<SectorAssignment | null>>,
): CalculatorResponse {
  return {
    ...result,
    hourly_coverage: result.hourly_coverage.map((hour, slot) => ({
      ...hour,
      sector_workers: sectorWorkersByHour[slot] ?? hour.sector_workers,
    })),
  };
}

function ensureEditableSector(
  sectorWorkers: Array<Array<SectorAssignment | null>>,
  ref: ScheduleSeatRef,
): SectorAssignment | null {
  const hourSectors = sectorWorkers[ref.slot];
  if (!hourSectors) {
    return null;
  }
  const existing = hourSectors[ref.sectorIndex];
  if (existing) {
    return existing;
  }
  const created = {
    sector_name: sectorNameForIndex(ref.sectorIndex),
    lower_worker: '',
    upper_worker: '',
  };
  hourSectors[ref.sectorIndex] = created;
  return created;
}

function moveScheduleSeat(result: CalculatorResponse, source: ScheduleSeatRef, target: ScheduleSeatRef): CalculatorResponse {
  if (
    source.slot === target.slot
    && source.sectorIndex === target.sectorIndex
    && source.seat === target.seat
  ) {
    return result;
  }
  const sectorWorkersByHour = cloneSectorWorkers(result);
  const sourceSector = sectorWorkersByHour[source.slot]?.[source.sectorIndex];
  if (!sourceSector) {
    return result;
  }
  const sourceWorker = seatWorker(sourceSector, source.seat);
  if (!sourceWorker) {
    return result;
  }
  if (source.slot === target.slot && source.sectorIndex === target.sectorIndex) {
    const targetWorker = seatWorker(sourceSector, target.seat);
    let updatedSector = setSeatWorker(sourceSector, source.seat, targetWorker);
    updatedSector = setSeatWorker(updatedSector, target.seat, sourceWorker);
    sectorWorkersByHour[source.slot][source.sectorIndex] = normalizeEditedSector(updatedSector);
    return resultWithSectorWorkers(result, sectorWorkersByHour);
  }
  const targetSector = ensureEditableSector(sectorWorkersByHour, target);
  if (!targetSector) {
    return result;
  }
  const targetWorker = seatWorker(targetSector, target.seat);
  sectorWorkersByHour[source.slot][source.sectorIndex] = normalizeEditedSector(
    setSeatWorker(sourceSector, source.seat, targetWorker),
  );
  sectorWorkersByHour[target.slot][target.sectorIndex] = normalizeEditedSector(
    setSeatWorker(targetSector, target.seat, sourceWorker),
  );
  return resultWithSectorWorkers(result, sectorWorkersByHour);
}

function removeScheduleSeat(result: CalculatorResponse, target: ScheduleSeatRef): CalculatorResponse {
  const sectorWorkersByHour = cloneSectorWorkers(result);
  const sector = sectorWorkersByHour[target.slot]?.[target.sectorIndex];
  if (!sector) {
    return result;
  }
  sectorWorkersByHour[target.slot][target.sectorIndex] = normalizeEditedSector(setSeatWorker(sector, target.seat, ''));
  return resultWithSectorWorkers(result, sectorWorkersByHour);
}

function removePersonFromResult(result: CalculatorResponse, personId: string): CalculatorResponse {
  const nextHourlyCoverage = result.hourly_coverage.map((hour) => ({
    ...hour,
    sector_workers: hour.sector_workers.map((sector) => {
      if (!sector) {
        return null;
      }
      const nextSector = {
        ...sector,
        lower_worker: normalizedWorkerId(sector.lower_worker) === personId ? '' : sector.lower_worker,
        upper_worker: normalizedWorkerId(sector.upper_worker) === personId ? '' : sector.upper_worker,
      };
      return normalizeEditedSector(nextSector);
    }),
  }));
  return {
    ...result,
    people: result.people.filter((person) => person.id !== personId),
    hourly_coverage: nextHourlyCoverage,
  };
}

function assignScheduleSeat(
  result: CalculatorResponse,
  target: ScheduleSeatRef,
  person: VirtualPerson,
): CalculatorResponse {
  const sectorWorkersByHour = cloneSectorWorkers(result);
  const targetSector = ensureEditableSector(sectorWorkersByHour, target);
  if (!targetSector) {
    return result;
  }
  const alreadyAssignedInHour = sectorWorkersByHour[target.slot]?.some((sector, sectorIndex) => (
    sector
    && (
      sectorIndex !== target.sectorIndex
      || seatWorker(sector, target.seat) !== person.id
    )
    && workerIdsForSector(sector).includes(person.id)
  ));
  if (alreadyAssignedInHour) {
    window.alert(`${personDisplayId(person)} je v tej uri že razporejen.`);
    return result;
  }
  sectorWorkersByHour[target.slot][target.sectorIndex] = normalizeEditedSector(
    setSeatWorker(targetSector, target.seat, person.id),
  );
  const people = result.people.some((currentPerson) => currentPerson.id === person.id)
    ? result.people
    : [...result.people, person];
  return resultWithSectorWorkers({ ...result, people }, sectorWorkersByHour);
}

function formatMetricsForCopy(result: CalculatorResponse): string {
  return toTsv([
    ['Podatek', 'Vrednost'],
    ['Najdeno max sektorskih ur', result.max_sector_hours],
    ['Željene sektorske ure', result.requested_sector_hours],
    ['Spodnja meja / minimum ljudi', result.baseline_min_people ?? ''],
    ['Razlaga meje', result.baseline_min_people_formula ?? ''],
    ['CP-SAT SH meja', result.solver_upper_bound_sector_hours ?? ''],
    ['Vrzel do SH meje', result.solver_gap_to_upper_bound ?? ''],
    ['Krizne VI/FMP ure', result.crisis_exception_hours],
    ['VI na robnih urah', result.leader_edge_exception_hours],
    ['VI/FMP prekrivanje', result.fmp_vi_overlap_hours],
    ['Splaniranih ljudi', result.planned_people],
    ['Aktivnih na sektorju', result.active_people],
    ['Obvezne FL vloge', result.minimum_required_fl],
    ['Manjkajoče ure', result.missing_sector_hours],
    ['Neuporabljeni ljudje', result.unused_people],
    ['Izkoriščenost ljudi', `${result.utilization_percent}%`],
    ['Kontrolorske ure opravljene', result.scheduled_person_hours],
    ['Kontrolorske ure možne', result.total_person_capacity_hours],
  ]);
}

function formatShiftSummaryForCopy(result: CalculatorResponse): string {
  return toTsv([
    ['Izmena / vloga', 'FL', 'APS', 'ACS', 'Skupaj'],
    ...result.shift_summary.map((row) => [shiftSummaryLabel(row.shift), row.fl, row.aps, row.acs, row.total]),
  ]);
}

function formatCoverageForCopy(result: CalculatorResponse, targetDemand?: number[]): string {
  const peopleById = new Map(result.people.map((person) => [person.id, person]));
  return toTsv([
    ['Ura', 'Želja', 'Realnost', 'Razlika', 'Delavci'],
    ...result.hourly_coverage.map((hour, index) => {
      const target = targetDemand?.[index] ?? hour.open_sectors;
      return [
        hour.hour,
        target,
        hour.open_sectors,
        hour.open_sectors - target,
        hour.workers.map((workerId) => {
          const person = peopleById.get(workerId);
          return person ? personDisplayId(person) : workerId;
        }).join(', '),
      ];
    }),
  ]);
}

function formatSectorScheduleForCopy(result: CalculatorResponse): string {
  const maxSectors = Math.max(...result.hourly_coverage.map((hour) => hour.sector_workers.length), 1);
  const peopleById = new Map(result.people.map((person) => [person.id, person]));
  const headers = [
    'Ura',
    ...Array.from({ length: maxSectors }, (_, index) => sectorColumnLabels[index] ?? `Sektor ${index + 1}`),
  ];
  const scheduleRows = [
    headers,
    ...result.hourly_coverage.map((hour) => [
      hour.hour,
      ...hour.sector_workers.map((sector) => {
        if (!sector) {
          return '';
        }
        return [sector.lower_worker, sector.upper_worker]
          .filter(Boolean)
          .map((workerId) => {
            const person = peopleById.get(workerId);
            return person ? personDisplayId(person) : workerId;
          })
          .join(' ');
      }),
    ]),
  ];
  const people = [...result.people].sort((first, second) => personIndexFromId(first.id) - personIndexFromId(second.id));
  const shiftRows = [
    ['Oseba', 'Prihod'],
    ...people.map((person) => [personDisplayId(person), shiftStartLabel(person.shift)]),
  ];
  const ratingRows = [
    ['Oseba', 'Rating'],
    ...people.map((person) => [personDisplayId(person), person.license]),
  ];
  const rowCount = Math.max(scheduleRows.length, shiftRows.length, ratingRows.length);

  return toTsv(Array.from({ length: rowCount }, (_, index) => [
    ...(scheduleRows[index] ?? Array.from({ length: headers.length }, () => '')),
    '',
    ...(shiftRows[index] ?? ['', '']),
    '',
    ...(ratingRows[index] ?? ['', '']),
  ]));
}

function formatPeopleForCopy(result: CalculatorResponse): string {
  return toTsv([
    ['Oseba', 'Vloga', 'Vir', 'Izmena', 'Licenca', 'Sektorske ure', 'Maks. ure', 'Izkoriščenost'],
    ...result.people.map((person) => [
      personDisplayId(person),
      person.role ?? '',
      personSourceLabel(person.source),
      person.shift,
      person.license,
      person.sector_hours,
      person.max_sector_hours,
      `${person.utilization_percent}%`,
    ]),
  ]);
}

function formatParetoPointsForCopy(points: ParetoPoint[]): string {
  return toTsv([
    [
      'Limit ljudi',
      'Uporabljenih',
      'Aktivnih',
      'Sektorske ure',
      'Zahtevane ure',
      'Uspešnost',
      'Manjka',
      'Kontrolorske ure',
      'Kapaciteta',
      'Izkoriščenost',
      'Officerji',
      'Status',
      'Vrzel do dokaza',
    ],
    ...points.map((point) => [
      point.people_limit,
      point.planned_people,
      point.active_people,
      point.max_sector_hours,
      point.requested_sector_hours,
      `${point.coverage_percent}%`,
      point.missing_sector_hours,
      point.scheduled_person_hours,
      point.total_person_capacity_hours,
      `${point.utilization_percent}%`,
      point.used_officers,
      point.solver_status ?? '',
      point.solver_optimality_gap_percent === null ? '' : `${point.solver_optimality_gap_percent}%`,
    ]),
  ]);
}

function formatParetoForCopy(result: CalculatorResponse): string {
  return formatParetoPointsForCopy(result.pareto_points ?? []);
}

function formatNotesForCopy(result: CalculatorResponse): string {
  return toTsv([
    ['Tip', 'Sporočilo'],
    ...result.warnings.map((warning) => ['Opozorilo', warning]),
    ...result.notes.map((note) => ['Info', note]),
  ]);
}

function formatAllResultForCopy(result: CalculatorResponse): string {
  const sections = [
    'POVZETEK',
    formatMetricsForCopy(result),
    '',
    'SESTAVA IZMEN',
    formatShiftSummaryForCopy(result),
    '',
    'ODPRTOST PO URAH',
    formatCoverageForCopy(result),
    '',
    'RAZPORED PO SEKTORJIH',
    formatSectorScheduleForCopy(result),
    '',
    'LJUDJE',
    formatPeopleForCopy(result),
    '',
  ];
  if ((result.pareto_points ?? []).length > 0) {
    sections.push('PARETO ANALIZA LJUDI', formatParetoForCopy(result), '');
  }
  sections.push(
    'OPOMBE',
    formatNotesForCopy(result),
  );
  return sections.join('\n');
}

function formatResultForCsv(result: CalculatorResponse): string {
  const rows: (string | number | null | undefined)[][] = [
    ['ATCConfMaker rezultat'],
    [],
    ['Povzetek'],
    ['Podatek', 'Vrednost'],
    ['Najdeno max sektorskih ur', result.max_sector_hours],
    ['Željene sektorske ure', result.requested_sector_hours],
    ['Spodnja meja / minimum ljudi', result.baseline_min_people ?? ''],
    ['Razlaga meje', result.baseline_min_people_formula ?? ''],
    ['CP-SAT SH meja', result.solver_upper_bound_sector_hours ?? ''],
    ['Vrzel do SH meje', result.solver_gap_to_upper_bound ?? ''],
    ['Krizne VI/FMP ure', result.crisis_exception_hours],
    ['VI na robnih urah', result.leader_edge_exception_hours],
    ['VI/FMP prekrivanje', result.fmp_vi_overlap_hours],
    ['Splaniranih ljudi', result.planned_people],
    ['Aktivnih na sektorju', result.active_people],
    ['Manjkajoče ure', result.missing_sector_hours],
    ['Izkoriščenost ljudi', `${result.utilization_percent}%`],
    ['Kontrolorske ure', `${result.scheduled_person_hours}/${result.total_person_capacity_hours}`],
    [],
    ['Sestava izmen'],
    ['Izmena / vloga', 'FL', 'APS', 'ACS', 'Skupaj'],
    ...result.shift_summary.map((row) => [row.shift, row.fl, row.aps, row.acs, row.total]),
    [],
    ['Ljudje'],
    ['Oseba', 'Vloga', 'Vir', 'Izmena', 'Licenca', 'Sektorske ure', 'Maks. ure', 'Izkoriščenost'],
    ...result.people.map((person) => [
      personDisplayId(person),
      person.role ?? '',
      person.source,
      person.shift,
      person.license,
      person.sector_hours,
      person.max_sector_hours,
      `${person.utilization_percent}%`,
    ]),
    [],
    ['Odprtost po urah'],
    ['Ura', 'Odprti sektorji', 'Delavci'],
    ...result.hourly_coverage.map((hour) => [hour.hour, hour.open_sectors, hour.workers.join(', ')]),
    [],
    [resultCsvMarker, JSON.stringify(result)],
  ];
  return toCsv(rows);
}

function parseResultFromCsv(text: string): CalculatorResponse {
  for (const line of text.split(/\r?\n/)) {
    const [key, payload] = parseCsvLine(line);
    if (key === resultCsvMarker && payload) {
      const parsed = JSON.parse(payload) as unknown;
      if (isCalculatorResponse(parsed)) {
        return parsed;
      }
      break;
    }
  }
  throw new Error('CSV ne vsebuje veljavnega ATCConfMaker rezultata.');
}

async function copyTextToClipboard(text: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    // Fall through to the textarea fallback for browser contexts that expose
    // navigator.clipboard but reject writes from embedded/local pages.
  }

  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.left = '-9999px';
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  const copied = document.execCommand('copy');
  document.body.removeChild(textArea);
  if (!copied) {
    throw new Error('Kopiranje ni uspelo.');
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function normalizeShiftRules(shifts: ShiftRule[]): ShiftRule[] {
  return shifts.map((shift) => ({ ...shift, enabled: shift.enabled !== false }));
}

function normalizeSettings(settings: CalculatorSettings): CalculatorSettings {
  return {
    ...settings,
    coverage_priority: clamp(settings.coverage_priority ?? 100, 0, 100),
    license_mix_priority: clamp(settings.license_mix_priority ?? 25, 0, 100),
    short_shift_priority: clamp(settings.short_shift_priority ?? 100, 0, 100),
    include_required_shift_leaders: true,
    include_night_fl_requirement: settings.include_night_fl_requirement ?? true,
    shifts: normalizeShiftRules(settings.shifts),
    officer_shifts: normalizeShiftRules(settings.officer_shifts),
  };
}

function activeShiftCodes(shifts: ShiftRule[]): Set<string> {
  return new Set(shifts.filter((shift) => shift.enabled !== false).map((shift) => shift.code));
}

function filterFixedStaffForActiveShifts(rows: FixedStaffRule[], settings: CalculatorSettings): FixedStaffRule[] {
  const activeCodes = activeShiftCodes(settings.shifts);
  return rows.filter((row) => activeCodes.has(row.shift));
}

function filterOfficerStaffForActiveShifts(rows: OfficerStaffRule[], settings: CalculatorSettings): OfficerStaffRule[] {
  const activeCodes = activeShiftCodes(settings.officer_shifts);
  return rows.filter((row) => activeCodes.has(row.shift));
}

function officePoolFromOfficerRules(rows: OfficerStaffRule[], settings: CalculatorSettings): OfficePool {
  return filterOfficerStaffForActiveShifts(rows, settings).reduce<OfficePool>((pool, row) => {
    const nextPool = { ...pool };
    const normalizedCount = clamp(row.count, 0, 80);
    if (row.license === 'FL') {
      nextPool.fl += normalizedCount;
    } else if (row.license === 'APS') {
      nextPool.aps += normalizedCount;
    } else {
      nextPool.acs += normalizedCount;
    }
    return nextPool;
  }, { fl: 0, aps: 0, acs: 0 });
}

function mergeSavedShiftRules(defaultRules: ShiftRule[], savedRules: unknown): ShiftRule[] {
  if (!Array.isArray(savedRules)) {
    return defaultRules;
  }
  const savedByCode = new Map(
    savedRules
      .filter((rule): rule is ShiftRule => !!rule && typeof rule === 'object' && typeof (rule as ShiftRule).code === 'string')
      .map((rule) => [rule.code, rule]),
  );
  return defaultRules.map((rule) => {
    const saved = savedByCode.get(rule.code);
    if (!saved) {
      return rule;
    }
    return {
      ...rule,
      start_hour: Number.isFinite(saved.start_hour) ? saved.start_hour : rule.start_hour,
      duration_hours: Number.isFinite(saved.duration_hours) ? saved.duration_hours : rule.duration_hours,
      enabled: saved.enabled !== false,
    };
  });
}

function loadSavedCalculatorSettings(defaultSettings: CalculatorSettings): CalculatorSettings {
  try {
    const stored = window.localStorage.getItem(savedSettingsStorageKey);
    if (!stored) {
      return defaultSettings;
    }
    const parsed = JSON.parse(stored) as Partial<CalculatorSettings>;
    if (
      parsed.v1_sector_limit === 1
      && parsed.v2_sector_limit === 1
      && parsed.v3_sector_limit === 2
    ) {
      parsed.v3_sector_limit = defaultSettings.v3_sector_limit;
    }
    return normalizeSettings({
      ...defaultSettings,
      ...parsed,
      shifts: mergeSavedShiftRules(defaultSettings.shifts, parsed.shifts),
      officer_shifts: mergeSavedShiftRules(defaultSettings.officer_shifts, parsed.officer_shifts),
    });
  } catch {
    return defaultSettings;
  }
}

function finiteNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function normalizeSavedIntervals(rawIntervals: unknown, maxSectors: number): SectorDemandInterval[] {
  if (!Array.isArray(rawIntervals)) {
    return createDefaultDemandIntervals();
  }
  const intervals = rawIntervals
    .filter((interval): interval is Partial<SectorDemandInterval> => !!interval && typeof interval === 'object')
    .map((interval, index) => ({
      id: index + 1,
      sectorCount: clamp(Math.round(finiteNumber(interval.sectorCount, 0)), 0, maxSectors),
      startHour: clamp(Math.round(finiteNumber(interval.startHour, 8)), 0, 23),
      endHour: clamp(Math.round(finiteNumber(interval.endHour, 11)), 0, 23),
    }))
    .filter((interval) => interval.sectorCount > 0 && interval.startHour !== interval.endHour);
  return intervals.length > 0 ? intervals : createDefaultDemandIntervals();
}

function normalizeSavedFixedStaff(rawRows: unknown, shifts: ShiftRule[]): FixedStaffRow[] {
  if (!Array.isArray(rawRows)) {
    return [];
  }
  const shiftCodes = new Set(shifts.map((shift) => shift.code));
  return rawRows
    .filter((row): row is Partial<FixedStaffRow> => !!row && typeof row === 'object')
    .map((row, index) => ({
      id: index + 1,
      count: clamp(Math.round(finiteNumber(row.count, 1)), 1, 80),
      license: row.license === 'FL' || row.license === 'APS' || row.license === 'ACS' ? row.license : 'ACS',
      shift: typeof row.shift === 'string' && shiftCodes.has(row.shift) ? row.shift : shifts[0]?.code ?? 'A7',
      role: typeof row.role === 'string' ? row.role : '',
    }))
    .filter((row) => shiftCodes.has(row.shift));
}

function normalizeSavedOfficerRows(rawRows: unknown, shifts: ShiftRule[]): OfficerStaffRow[] {
  if (!Array.isArray(rawRows)) {
    return createOfficerRows(shifts);
  }
  return mergeOfficerRows(
    rawRows
      .filter((row): row is Partial<OfficerStaffRow> => !!row && typeof row === 'object')
      .map((row) => ({
        shift: typeof row.shift === 'string' ? row.shift : '',
        fl: clamp(Math.round(finiteNumber(row.fl, 0)), 0, 80),
        aps: clamp(Math.round(finiteNumber(row.aps, 0)), 0, 80),
        acs: clamp(Math.round(finiteNumber(row.acs, 0)), 0, 80),
      })),
    shifts,
  );
}

function normalizeSavedOfficePool(rawPool: unknown): OfficePool {
  const pool = rawPool && typeof rawPool === 'object' ? rawPool as Partial<OfficePool> : {};
  return {
    fl: clamp(Math.round(finiteNumber(pool.fl, 0)), 0, 80),
    aps: clamp(Math.round(finiteNumber(pool.aps, 0)), 0, 80),
    acs: clamp(Math.round(finiteNumber(pool.acs, 0)), 0, 80),
  };
}

function loadSavedCalculatorInputs(settings: CalculatorSettings): SavedCalculatorInputs | null {
  try {
    const stored = window.localStorage.getItem(savedCalculatorInputsStorageKey);
    if (!stored) {
      return null;
    }
    const parsed = JSON.parse(stored) as SavedCalculatorInputs;
    const totalPeople = clamp(Math.round(finiteNumber(parsed.totalPeople, 28)), 1, 80);
    const flCount = clamp(Math.round(finiteNumber(parsed.flCount, 12)), 0, totalPeople);
    const apsCount = clamp(Math.round(finiteNumber(parsed.apsCount, 0)), 0, totalPeople - flCount);
    const ratio: Partial<{ fl: number; aps: number; acs: number }> = parsed.minimumLicenseRatio ?? {};
    const configuredShiftCodes = new Set(settings.shifts.map((shift) => shift.code));
    const parsedFmpShift = typeof parsed.fmpShift === 'string' && parsed.fmpShift.trim()
      ? parsed.fmpShift.trim()
      : DEFAULT_FMP_SHIFT;
    const savedBaseSectors = clamp(
      Math.round(finiteNumber(parsed.baseSectors, 3)),
      0,
      settings.max_sectors_per_hour,
    );
    const savedSectorIntervals = normalizeSavedIntervals(
      parsed.sectorIntervals,
      settings.max_sectors_per_hour,
    );
    const hasLegacyStaffLimits = parsed.baseSectors !== undefined || Array.isArray(parsed.sectorIntervals);
    return {
      calculationMode: parsed.calculationMode === 'staff_to_coverage' ? 'staff_to_coverage' : 'demand_to_staff',
      totalPeople,
      flCount,
      apsCount,
      preferMinimalFl: parsed.preferMinimalFl === true,
      usePeopleLimit: parsed.usePeopleLimit === true,
      minimumLicenseRatio: {
        fl: clamp(Math.round(finiteNumber(ratio.fl, 50)), 0, 100),
        aps: clamp(Math.round(finiteNumber(ratio.aps, 0)), 0, 100),
        acs: clamp(Math.round(finiteNumber(ratio.acs, 50)), 0, 100),
      },
      sectorDemand: Array.isArray(parsed.sectorDemand)
        ? clampSectorDemand(parsed.sectorDemand, settings.max_sectors_per_hour)
        : createDefaultSectorDemand(settings.max_sectors_per_hour),
      staffSectorLimits: Array.isArray(parsed.staffSectorLimits)
        ? normalizeStaffSectorLimits(parsed.staffSectorLimits, settings.max_sectors_per_hour)
        : hasLegacyStaffLimits
          ? buildSectorDemandFromIntervals(
            settings.max_sectors_per_hour,
            savedBaseSectors,
            savedSectorIntervals,
          )
          : createUnlimitedSectorLimits(),
      baseSectors: savedBaseSectors,
      sectorIntervals: savedSectorIntervals,
      fixedStaff: normalizeSavedFixedStaff(parsed.fixedStaff, settings.shifts),
      officerStaff: normalizeSavedOfficerRows(parsed.officerStaff, settings.officer_shifts),
      officePool: normalizeSavedOfficePool(parsed.officePool),
      includeFmp: parsed.includeFmp !== false,
      fmpShiftMode: parsed.fmpShiftMode === 'fixed' ? 'fixed' : 'auto',
      fmpShift: configuredShiftCodes.has(parsedFmpShift) && !FMP_BLOCKED_SHIFT_CODES.has(parsedFmpShift)
        ? parsedFmpShift
        : DEFAULT_FMP_SHIFT,
    };
  } catch {
    return null;
  }
}

function NumberField({
  label,
  value,
  min = 0,
  max,
  onChange,
  helper,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  onChange: (value: number) => void;
  helper?: string;
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {helper ? <MetricInfo text={helper} /> : null}
      </span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onKeyDown={preventNumberInputArrowStep}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function RequiredRoleLimitsEditor({
  settings,
  onChange,
}: {
  settings: CalculatorSettings;
  onChange: (settings: CalculatorSettings) => void;
}) {
  return (
    <section className="demand-card role-limits-card">
      <div className="demand-header">
        <div>
          <p className="eyebrow">Omejitve vlog</p>
          <h3>
            V1, V2, V3 in FMP na sektorju
            <MetricInfo text="Omejitev sektorskih ur za vloge V1/V2/V3 in FMP" />
          </h3>
        </div>
      </div>
      <div className="form-grid role-limits-grid">
        <NumberField
          label="V1 max sektorskih ur"
          min={0}
          max={24}
          value={settings.v1_sector_limit}
          onChange={(value) => onChange({ ...settings, v1_sector_limit: value })}
        />
        <NumberField
          label="V2 max sektorskih ur"
          min={0}
          max={24}
          value={settings.v2_sector_limit}
          onChange={(value) => onChange({ ...settings, v2_sector_limit: value })}
        />
        <NumberField
          label="V3 max sektorskih ur"
          min={0}
          max={24}
          value={settings.v3_sector_limit}
          onChange={(value) => onChange({ ...settings, v3_sector_limit: value })}
        />
        <NumberField
          label="FMP max sektorskih ur"
          min={0}
          max={24}
          value={settings.fmp_sector_limit}
          onChange={(value) => onChange({ ...settings, fmp_sector_limit: value })}
        />
      </div>
    </section>
  );
}

function optimizationPriorityLabel(value: number, zeroLabel = 'Izklopljeno'): string {
  if (value <= 0) return zeroLabel;
  if (value < 35) return 'Nizka';
  if (value < 70) return 'Srednja';
  if (value < 95) return 'Visoka';
  return 'Najvišja';
}

function OptimizationPrioritySlider({
  label,
  value,
  helper,
  zeroLabel,
  onChange,
}: {
  label: string;
  value: number;
  helper: string;
  zeroLabel?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="optimization-priority-slider">
      <span className="optimization-priority-heading">
        <strong>{label}</strong>
        <b>{value}/100 · {optimizationPriorityLabel(value, zeroLabel)}</b>
      </span>
      <input
        aria-label={label}
        min="0"
        max="100"
        step="5"
        type="range"
        value={value}
        onChange={(event) => onChange(clamp(Number(event.target.value), 0, 100))}
      />
      <small>{helper}</small>
    </label>
  );
}

function OptimizationPrioritiesEditor({
  settings,
  onChange,
}: {
  settings: CalculatorSettings;
  onChange: (settings: CalculatorSettings) => void;
}) {
  return (
    <section className="demand-card optimization-priorities-card" data-tour="optimization-priorities">
      <div className="demand-header">
        <div>
          <p className="eyebrow">Prioritete optimizacije</p>
          <h3>Kaj je solverju najpomembnejše?</h3>
        </div>
      </div>
      <p className="demand-help compact-help">
        Višja vrednost pomeni večjo težo. Ko je polna želena odprtost enkrat najdena, je drugi kriteriji ne smejo poslabšati.
      </p>
      <div className="optimization-priority-grid">
        <OptimizationPrioritySlider
          label="Doseganje želene odprtosti SH"
          value={settings.coverage_priority}
          zeroLabel="Samo varovalka"
          helper="100 pomeni: najprej išči največ SH; doseženih 71 SH ostane zaščitenih."
          onChange={(value) => onChange({ ...settings, coverage_priority: value })}
        />
        <OptimizationPrioritySlider
          label="Ciljno razmerje FL / APS / ACS"
          value={settings.license_mix_priority}
          helper="Višje vrednosti močneje držijo odstotke licenc, vendar ne zmanjšajo že dosežene polne pokritosti."
          onChange={(value) => onChange({ ...settings, license_mix_priority: value })}
        />
        <OptimizationPrioritySlider
          label="Krajše izmene: 7 ur pred 8 urami"
          value={settings.short_shift_priority}
          helper="Višje vrednosti zmanjšujejo skupno dolžino izmen in dajejo prednost A7/A14, kadar je pokritost enaka."
          onChange={(value) => onChange({ ...settings, short_shift_priority: value })}
        />
      </div>
    </section>
  );
}

function CopyButton({
  label = 'Kopiraj',
  textFactory,
}: {
  label?: string;
  textFactory: () => string;
}) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');

  const copy = async () => {
    try {
      await copyTextToClipboard(textFactory());
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
    window.setTimeout(() => setCopyState('idle'), 1600);
  };

  return (
    <button className="secondary-button compact-button copy-button" onClick={copy} type="button">
      {copyState === 'copied' ? 'Kopirano' : copyState === 'failed' ? 'Napaka' : label}
    </button>
  );
}

function ResultCsvImportButton({ onImport }: { onImport: (result: CalculatorResponse) => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [importState, setImportState] = useState<'idle' | 'imported' | 'failed'>('idle');

  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    try {
      onImport(parseResultFromCsv(await file.text()));
      setImportState('imported');
    } catch {
      setImportState('failed');
    } finally {
      event.target.value = '';
      window.setTimeout(() => setImportState('idle'), 1800);
    }
  };

  return (
    <>
      <button className="secondary-button compact-button copy-button" onClick={() => inputRef.current?.click()} type="button">
        {importState === 'imported' ? 'Uvoženo' : importState === 'failed' ? 'Napaka' : 'Uvozi CSV'}
      </button>
      <input
        ref={inputRef}
        className="hidden-file-input"
        type="file"
        accept=".csv,text/csv,text/plain"
        onChange={(event) => void importFile(event)}
      />
    </>
  );
}

function SettingsPanel({
  settings,
  defaultExtraNightA21FlCount,
  saveState,
  onChange,
  onSave,
  onReset,
}: {
  settings: CalculatorSettings;
  defaultExtraNightA21FlCount: number;
  saveState: 'idle' | 'saved' | 'failed';
  onChange: (settings: CalculatorSettings) => void;
  onSave: () => void;
  onReset: () => void;
}) {
  const includedV3Count = settings.include_required_shift_leaders ? 1 : 0;
  const extraNightA21FlCount = Math.max(0, settings.required_night_fl_count - includedV3Count);

  const updateShift = (index: number, patch: Partial<ShiftRule>) => {
    onChange({
      ...settings,
      shifts: settings.shifts.map((shift, shiftIndex) => (shiftIndex === index ? { ...shift, ...patch } : shift)),
    });
  };

  const updateOfficerShift = (index: number, patch: Partial<ShiftRule>) => {
    onChange({
      ...settings,
      officer_shifts: settings.officer_shifts.map((shift, shiftIndex) => (
        shiftIndex === index ? { ...shift, ...patch } : shift
      )),
    });
  };

  return (
    <section className="panel settings-panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Nastavitve pravil</p>
          <h2>Fiksna pravila kalkulatorja</h2>
        </div>
        <div className="panel-actions">
          <button className="secondary-button" onClick={onReset} type="button">
            Ponastavi privzeto
          </button>
          <button className="primary-button compact-run-button" onClick={onSave} type="button">
            {saveState === 'saved' ? 'Pravila shranjena' : saveState === 'failed' ? 'Napaka pri shranjevanju' : 'Shrani pravila'}
          </button>
        </div>
      </div>

      <div className="settings-grid">
        <NumberField
          label="Največ sektorjev hkrati"
          min={1}
          max={8}
          value={settings.max_sectors_per_hour}
          onChange={(value) => onChange({ ...settings, max_sectors_per_hour: value })}
        />
        <NumberField
          label="Največ zaporednih ur dela"
          min={1}
          max={6}
          value={settings.max_consecutive_work_hours}
          onChange={(value) => onChange({ ...settings, max_consecutive_work_hours: value })}
        />
        <NumberField
          label="Počitek po maksimumu"
          min={1}
          max={4}
          value={settings.rest_after_max_consecutive_hours}
          onChange={(value) => onChange({ ...settings, rest_after_max_consecutive_hours: value })}
          helper="Trenutni MVP uporablja 1 celico počitka po 2 urah."
        />
        <NumberField
          label="Nočnih A21 FL poleg V3"
          min={0}
          max={9}
          value={extraNightA21FlCount}
          onChange={(value) => onChange({ ...settings, required_night_fl_count: value + includedV3Count })}
          helper={
            settings.include_night_fl_requirement
              ? `Privzeto ${defaultExtraNightA21FlCount} pomeni V3 + ${defaultExtraNightA21FlCount}× A21 FL.`
              : 'Ignorirano, dokler pravilo za dodatne nočne A21 FL ni vklopljeno.'
          }
        />
        <NumberField
          label="CP-SAT časovni limit"
          min={1}
          max={7200}
          value={settings.cp_sat_time_limit_seconds}
          onChange={(value) => onChange({ ...settings, cp_sat_time_limit_seconds: value })}
          helper="Sekunde. Privzeto 600; lahko prekineš in obdržiš najboljšo najdeno rešitev."
        />
        <NumberField
          label="Brez izboljšave"
          min={0}
          max={7200}
          value={settings.cp_sat_no_improvement_seconds}
          onChange={(value) => onChange({ ...settings, cp_sat_no_improvement_seconds: value })}
          helper="Ustavi šele, ko se pokritost ne izboljša in je dokazna razlika znotraj dovoljene meje."
        />
        <NumberField
          label="Dovoljena dokazna razlika"
          min={0}
          max={100}
          value={settings.cp_sat_acceptable_sector_gap}
          onChange={(value) => onChange({ ...settings, cp_sat_acceptable_sector_gap: value })}
          helper="Sektorske ure. 0 pomeni čakaj na dokaz; 1 pomeni zaključi, ko dokaz dopušča največ še 1 uro boljšo rešitev."
        />
        <NumberField
          label="Min. pokritost za stop"
          min={0}
          max={100}
          value={settings.cp_sat_min_auto_stop_coverage_percent}
          onChange={(value) => onChange({ ...settings, cp_sat_min_auto_stop_coverage_percent: value })}
          helper="Odstotek zahtevane odprtosti, preden politika sme samodejno zaključiti."
        />
      </div>

      <div className="check-row fixed-rule-row">
        <span className="fixed-check" aria-hidden="true">✓</span>
        <span>V1/A7, V2/A14 in V3 so vedno zahtevani kot FL vodje izmen.</span>
      </div>

      <label className="check-row">
        <input
          type="checkbox"
          checked={settings.include_night_fl_requirement}
          onChange={(event) => onChange({ ...settings, include_night_fl_requirement: event.target.checked })}
        />
        <span>Zahtevaj nočne A21 FL poleg V3</span>
      </label>

      <div className="table-card">
        <div className="table-title">Izmene</div>
        <div className="responsive-table">
          <table>
            <thead>
              <tr>
                <th>Aktivna</th>
                <th>Izmena</th>
                <th>Začetek</th>
                <th>Trajanje</th>
              </tr>
            </thead>
            <tbody>
              {settings.shifts.map((shift, index) => (
                <tr key={shift.code}>
                  <td>
                    <input
                      aria-label={`${shift.code} aktivna`}
                      className="table-checkbox"
                      type="checkbox"
                      checked={shift.enabled !== false}
                      onChange={(event) => updateShift(index, { enabled: event.target.checked })}
                    />
                  </td>
                  <td className="strong">{shift.code}</td>
                  <td>
                    <input
                      className="mini-input"
                      type="number"
                      min={0}
                      max={23}
                      value={shift.start_hour}
                      onKeyDown={preventNumberInputArrowStep}
                      onChange={(event) => updateShift(index, { start_hour: Number(event.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      className="mini-input"
                      type="number"
                      min={1}
                      max={24}
                      value={shift.duration_hours}
                      onKeyDown={preventNumberInputArrowStep}
                      onChange={(event) => updateShift(index, { duration_hours: Number(event.target.value) })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="table-card">
        <div className="table-title">Officer izmene</div>
        <div className="responsive-table">
          <table>
            <thead>
              <tr>
                <th>Aktivna</th>
                <th>Izmena</th>
                <th>Začetek</th>
                <th>Trajanje</th>
              </tr>
            </thead>
            <tbody>
              {settings.officer_shifts.map((shift, index) => (
                <tr key={shift.code}>
                  <td>
                    <input
                      aria-label={`${shift.code} aktivna`}
                      className="table-checkbox"
                      type="checkbox"
                      checked={shift.enabled !== false}
                      onChange={(event) => updateOfficerShift(index, { enabled: event.target.checked })}
                    />
                  </td>
                  <td className="strong">{shift.code}</td>
                  <td>
                    <input
                      className="mini-input"
                      type="number"
                      min={0}
                      max={23}
                      value={shift.start_hour}
                      onKeyDown={preventNumberInputArrowStep}
                      onChange={(event) => updateOfficerShift(index, { start_hour: Number(event.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      className="mini-input"
                      type="number"
                      min={1}
                      max={24}
                      value={shift.duration_hours}
                      onKeyDown={preventNumberInputArrowStep}
                      onChange={(event) => updateOfficerShift(index, { duration_hours: Number(event.target.value) })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function SectorDemandInput({
  maxSectors,
  values,
  onChange,
}: {
  maxSectors: number;
  values: number[];
  onChange: (values: number[]) => void;
}) {
  const sectorHeaders = Array.from({ length: maxSectors }, (_, index) => `S${index + 1}`);

  const setHourDemand = (hourIndex: number, sectorIndex: number) => {
    const clickedCount = sectorIndex + 1;
    const currentCount = values[hourIndex] ?? 0;
    const nextCount = clickedCount <= currentCount ? sectorIndex : clickedCount;
    onChange(values.map((value, index) => (index === hourIndex ? nextCount : value)));
  };

  return (
    <section className="demand-card sector-demand-card" data-tour="sector-demand">
      <div className="demand-header">
        <div>
          <p className="eyebrow">Faza 2</p>
          <h3>Želena odprtost po urah</h3>
        </div>
        <button className="secondary-button compact-button" onClick={() => onChange(createMaximumSectorDemand(maxSectors))} type="button">
          Vse na max
        </button>
      </div>
      <p className="demand-help">Klikni celice po urah. Označene celice povedo, koliko sektorjev želiš imeti odprtih.</p>
      <div className="demand-scroll">
        <div className="demand-grid" style={{ gridTemplateColumns: `64px repeat(${maxSectors}, minmax(42px, 1fr))` }}>
          <div className="demand-cell demand-head sticky-col">Ura</div>
          {sectorHeaders.map((sector) => (
            <div className="demand-cell demand-head" key={sector}>{sector.replace(' ', '\u00a0')}</div>
          ))}
          {hourLabels.flatMap((hour, hourIndex) => [
            <div className="demand-cell demand-hour sticky-col" key={`${hour}-label`}>{formatCompactHourLabel(hourIndex)}</div>,
            ...sectorHeaders.map((sector, sectorIndex) => {
              const selected = sectorIndex < (values[hourIndex] ?? 0);
              return (
                <button
                  aria-label={`${hour}, sektor ${sectorIndex + 1}`}
                  className={`demand-cell demand-toggle ${selected ? 'selected' : ''}`}
                  key={`${hour}-${sector}`}
                  onClick={() => setHourDemand(hourIndex, sectorIndex)}
                  type="button"
                >
                  {selected ? '✓' : '–'}
                </button>
              );
            }),
          ])}
        </div>
      </div>
    </section>
  );
}

function SectorLimitInput({
  maxSectors,
  values,
  onChange,
}: {
  maxSectors: number;
  values: Array<number | null>;
  onChange: (values: Array<number | null>) => void;
}) {
  const sectorHeaders = Array.from({ length: maxSectors }, (_, index) => `S${index + 1}`);

  const setHourLimit = (hourIndex: number, sectorIndex: number) => {
    const clickedLimit = sectorIndex + 1;
    const currentLimit = values[hourIndex] ?? null;
    const nextLimit = clickedLimit === maxSectors || currentLimit === clickedLimit
      ? null
      : clickedLimit;
    onChange(values.map((value, index) => (index === hourIndex ? nextLimit : value)));
  };

  return (
    <section className="demand-card sector-demand-card sector-limit-card">
      <div className="demand-header">
        <div>
          <p className="eyebrow">Urne omejitve</p>
          <h3>Največ sektorjev po urah</h3>
        </div>
        <button
          className="secondary-button compact-button"
          onClick={() => onChange(createUnlimitedSectorLimits())}
          type="button"
        >
          Brez omejitev
        </button>
      </div>
      <p className="demand-help">
        Privzeto ni dodatne omejitve. Klik na S2 pomeni, da sta v tej uri dovoljena največ 2 sektorja.
      </p>
      <div className="demand-scroll">
        <div className="demand-grid" style={{ gridTemplateColumns: `64px repeat(${maxSectors}, minmax(42px, 1fr))` }}>
          <div className="demand-cell demand-head sticky-col">Ura</div>
          {sectorHeaders.map((sector) => (
            <div className="demand-cell demand-head" key={sector}>{sector}</div>
          ))}
          {hourLabels.flatMap((hour, hourIndex) => {
            const storedLimit = values[hourIndex] ?? null;
            const unrestricted = storedLimit === null;
            const currentLimit = storedLimit ?? maxSectors;
            return [
              <div
                className={`demand-cell demand-hour sticky-col${unrestricted ? ' unrestricted' : ''}`}
                key={`${hour}-limit-label`}
                title={unrestricted ? 'Brez omejitve' : `Omejitev sektorjev: ${currentLimit}`}
              >
                {formatCompactHourLabel(hourIndex)}
              </div>,
              ...sectorHeaders.map((sector, sectorIndex) => {
                const selected = sectorIndex < currentLimit;
                const clickedLimit = sectorIndex + 1;
                return (
                  <button
                    aria-label={`${hour}, omejitev sektorjev ${clickedLimit}`}
                    className={`demand-cell demand-toggle limit-toggle ${selected ? 'selected' : ''}${unrestricted ? ' unrestricted' : ''}`}
                    key={`${hour}-limit-${sector}`}
                    onClick={() => setHourLimit(hourIndex, sectorIndex)}
                    title={clickedLimit === maxSectors ? 'Brez dodatne omejitve' : `Omejitev sektorjev: ${clickedLimit}`}
                    type="button"
                  >
                    {selected ? '✓' : '–'}
                  </button>
                );
              }),
            ];
          })}
        </div>
      </div>
    </section>
  );
}

function FixedStaffEditor({
  rows,
  shifts,
  onChange,
}: {
  rows: FixedStaffRow[];
  shifts: ShiftRule[];
  onChange: (rows: FixedStaffRow[]) => void;
}) {
  const shiftOptions = shifts.filter((shift) => shift.enabled !== false).map((shift) => shift.code);
  const defaultShift = shiftOptions[0] ?? 'A7';

  const addRow = () => {
    const nextId = Math.max(0, ...rows.map((row) => row.id)) + 1;
    onChange([
      ...rows,
      { id: nextId, count: 1, license: 'ACS', shift: defaultShift, role: '' },
    ]);
  };

  const updateRow = (id: number, patch: Partial<FixedStaffRow>) => {
    onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  };

  const removeRow = (id: number) => {
    onChange(rows.filter((row) => row.id !== id));
  };

  return (
    <section className="demand-card fixed-staff-card" data-tour="fixed-shifts">
      <div className="demand-header">
        <div>
          <p className="eyebrow">Vhodne izmene</p>
          <h3>
            Fiksno vpisane izmene
            <MetricInfo text="Fiksne izmene: npr. 3 x A21 pomeni največ 3 ljudi v A21; generator jih ne dodaja čez to mejo." />
          </h3>
        </div>
        <button className="secondary-button compact-button" onClick={addRow} type="button">
          + Dodaj
        </button>
      </div>
      {rows.length > 0 ? (
        <div className="fixed-staff-list">
          <div className="fixed-staff-row fixed-staff-head">
            <span>Št.</span>
            <span>Licenca</span>
            <span>Izmena</span>
            <span>Vloga</span>
            <span />
          </div>
          {rows.map((row) => (
            <div className="fixed-staff-row" key={row.id}>
              <input
                min={1}
                max={80}
                type="number"
                value={row.count}
                onKeyDown={preventNumberInputArrowStep}
                onChange={(event) => updateRow(row.id, { count: Number(event.target.value) })}
              />
              <select
                value={row.license}
                onChange={(event) => updateRow(row.id, { license: event.target.value as FixedStaffRule['license'] })}
              >
                <option value="FL">FL</option>
                <option value="APS">APS</option>
                <option value="ACS">ACS</option>
              </select>
              <select value={row.shift} onChange={(event) => updateRow(row.id, { shift: event.target.value })}>
                {shiftOptions.map((shift) => (
                  <option key={shift} value={shift}>{shift}</option>
                ))}
              </select>
              <input
                placeholder="npr. instr."
                type="text"
                value={row.role}
                onChange={(event) => updateRow(row.id, { role: event.target.value })}
              />
              <button className="secondary-button compact-button" onClick={() => removeRow(row.id)} type="button">
                Odstrani
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="demand-help">Ni dodatnih fiksnih izmen.</p>
      )}
    </section>
  );
}

function OfficePoolEditor({
  pool,
  onChange,
}: {
  pool: OfficePool;
  onChange: (pool: OfficePool) => void;
}) {
  const totalPool = pool.fl + pool.aps + pool.acs;

  return (
    <section className="demand-card office-pool-card">
      <div className="demand-header">
        <div>
          <p className="eyebrow">Priporočilni modul</p>
          <h3>
            Operativni office
            <MetricInfo text="Dodatni ljudje niso del osnovnega števila. Solver jih uporabi kot zadnji vzvod, če izboljšajo pokritost." />
          </h3>
        </div>
        <div className="interval-summary" aria-label="Skupaj operativnih officev">
          <span>Skupaj</span>
          <strong>{totalPool}</strong>
        </div>
      </div>
      <div className="form-grid office-pool-grid">
        <NumberField
          label="FL office"
          min={0}
          max={80}
          value={pool.fl}
          onChange={(value) => onChange({ ...pool, fl: clamp(value, 0, 80) })}
        />
        <NumberField
          label="APS office"
          min={0}
          max={80}
          value={pool.aps}
          onChange={(value) => onChange({ ...pool, aps: clamp(value, 0, 80) })}
        />
        <NumberField
          label="ACS office"
          min={0}
          max={80}
          value={pool.acs}
          onChange={(value) => onChange({ ...pool, acs: clamp(value, 0, 80) })}
        />
      </div>
    </section>
  );
}

function OfficerStaffEditor({
  rows,
  shifts,
  onChange,
}: {
  rows: OfficerStaffRow[];
  shifts: ShiftRule[];
  onChange: (rows: OfficerStaffRow[]) => void;
}) {
  const visibleRows = mergeOfficerRows(rows, shifts);

  const updateRow = (shift: string, patch: Partial<OfficerStaffRow>) => {
    onChange(
      visibleRows.map((row) => (row.shift === shift ? { ...row, ...patch } : row)),
    );
  };

  const totalOfficers = visibleRows.reduce((total, row) => total + row.fl + row.aps + row.acs, 0);

  return (
    <details className="demand-card officer-card officer-manual-card">
      <summary className="officer-manual-summary">
        <div>
          <p className="eyebrow">Ročni override</p>
          <h3>Konkreten office po izmenah</h3>
        </div>
        <div className="interval-summary" aria-label="Skupaj officerjev">
          <span>Skupaj</span>
          <strong>{totalOfficers}</strong>
        </div>
      </summary>
      <div className="officer-grid">
        <div className="officer-row officer-head">
          <span>Izmena</span>
          <span>FL</span>
          <span>APS</span>
          <span>ACS</span>
        </div>
        {visibleRows.map((row) => (
          <div className="officer-row" key={row.shift}>
            <span className="strong">{row.shift}</span>
            <input
              min={0}
              max={80}
              type="number"
              value={row.fl}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => updateRow(row.shift, { fl: clamp(Number(event.target.value), 0, 80) })}
            />
            <input
              min={0}
              max={80}
              type="number"
              value={row.aps}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => updateRow(row.shift, { aps: clamp(Number(event.target.value), 0, 80) })}
            />
            <input
              min={0}
              max={80}
              type="number"
              value={row.acs}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => updateRow(row.shift, { acs: clamp(Number(event.target.value), 0, 80) })}
            />
          </div>
        ))}
      </div>
      <p className="demand-help">
        Štejejo v skupno število ljudi/licenc; solver jih da na sektor šele, ko izboljšajo rešitev.
      </p>
    </details>
  );
}

function WorkerChip({
  workerId,
  person,
  invalid = false,
  draggable = false,
  titleOverride,
  onDragStart,
  onRemove,
}: {
  workerId: string;
  person: CalculatorResponse['people'][number] | undefined;
  invalid?: boolean;
  draggable?: boolean;
  titleOverride?: string;
  onDragStart?: (event: DragEvent<HTMLSpanElement>) => void;
  onRemove?: () => void;
}) {
  const color = workerColor(workerId);
  const shiftLabel = person ? (person.role ? `${person.role}/${person.shift}` : person.shift) : '';
  const metaLabel = person ? `${person.license} | ${shiftLabel || '—'}` : '';
  const className = [
    'worker-chip',
    isOfficeSource(person?.source) ? 'officer-worker' : '',
    invalid ? 'invalid-worker' : '',
    draggable ? 'draggable-worker' : '',
  ].filter(Boolean).join(' ');
  const displayId = person ? personDisplayId(person) : workerId;

  return (
    <span
      className={className}
      style={{ backgroundColor: color.background, borderColor: color.border, color: color.text }}
      title={titleOverride ?? (person ? `${person.id} / ${person.license} / ${shiftLabel}` : workerId)}
      draggable={draggable}
      onDragStart={onDragStart}
    >
      <span className="worker-letter">{displayId}</span>
      {person ? <span className="worker-meta">{metaLabel}</span> : null}
      {onRemove ? (
        <button
          className="worker-remove-button"
          onClick={(event) => {
            event.stopPropagation();
            onRemove();
          }}
          onMouseDown={(event) => event.stopPropagation()}
          title={`Odstrani ${displayId} iz tega sedeža`}
          type="button"
        >
          ×
        </button>
      ) : null}
    </span>
  );
}

function ScheduleSeatChip({
  sector,
  seat,
  seatRef,
  result,
  shifts,
  peopleById,
  onMoveSeat,
  onRemoveSeat,
  onAddSeat,
}: {
  sector: SectorAssignment;
  seat: ScheduleSeat;
  seatRef: ScheduleSeatRef;
  result: CalculatorResponse;
  shifts: ShiftRule[];
  peopleById: Map<string, VirtualPerson>;
  onMoveSeat: (source: ScheduleSeatRef, target: ScheduleSeatRef) => void;
  onRemoveSeat: (target: ScheduleSeatRef) => void;
  onAddSeat: (target: ScheduleSeatRef) => void;
}) {
  const workerId = seatWorker(sector, seat);
  const person = workerId ? peopleById.get(workerId) : undefined;
  const violations = workerId
    ? sectorSlotViolations(result, workerId, seatRef.slot, sector.sector_name, peopleById, shifts)
    : [];
  const isInvalid = violations.length > 0;

  return (
    <span
      className={`schedule-seat ${workerId ? 'filled-seat' : 'empty-seat'}`}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
      }}
      onDrop={(event) => {
        event.preventDefault();
        const source = parseScheduleSeatRef(event.dataTransfer.getData('application/x-konfmaker-seat'));
        if (source) {
          onMoveSeat(source, seatRef);
        }
      }}
    >
      {workerId ? (
        <WorkerChip
          workerId={workerId}
          person={person}
          invalid={isInvalid}
          draggable
          onDragStart={(event) => {
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('application/x-konfmaker-seat', JSON.stringify(seatRef));
          }}
          onRemove={() => onRemoveSeat(seatRef)}
          titleOverride={violations.length ? violations.join('\n') : undefined}
        />
      ) : (
        <button
          className="schedule-seat-add"
          onClick={() => onAddSeat(seatRef)}
          title="Dodaj osebo v ta sedež"
          type="button"
        >
          +
        </button>
      )}
    </span>
  );
}

function isDefaultHiddenScheduleHour(hourLabel: string): boolean {
  const match = hourLabel.match(/^(\d{1,2}):/);
  if (!match) {
    return false;
  }

  const startHour = Number(match[1]);
  return startHour >= 1 && startHour < 5;
}

function ScheduleHourLabel({ hour }: { hour: string }) {
  const match = hour.match(/^\s*(\d{1,2})(?:(?::|\.)00)?\s*[–-]\s*(\d{1,2})(?:(?::|\.)00)?\s*$/);
  if (!match) {
    return <>{hour}</>;
  }

  const start = Number(match[1]);
  const end = Number(match[2]);
  return (
    <span className="schedule-hour-label">
      <span>{start}-{end}</span>
    </span>
  );
}

function SectorSchedule({
  result,
  shifts,
  onEditResult,
}: {
  result: CalculatorResponse;
  shifts: ShiftRule[];
  onEditResult: (updater: (current: CalculatorResponse) => CalculatorResponse) => void;
}) {
  const [showNightHours, setShowNightHours] = useState(false);
  const peopleById = useMemo(() => new Map(result.people.map((person) => [person.id, person])), [result.people]);
  const maxSectors = Math.max(...result.hourly_coverage.map((hour) => hour.sector_workers.length), 1);
  const hiddenNightHourCount = result.hourly_coverage.filter((hour) => isDefaultHiddenScheduleHour(hour.hour)).length;
  const visibleHourlyCoverage = result.hourly_coverage
    .map((hour, slot) => ({ hour, slot }))
    .filter(({ hour }) => showNightHours || !isDefaultHiddenScheduleHour(hour.hour));
  const sectorHeaders = Array.from(
    { length: maxSectors },
    (_, index) => sectorColumnLabels[index] ?? `Sektor ${index + 1}`,
  );
  const breakPeopleBySlot = useMemo(() => (
    result.hourly_coverage.map((hour, slot) => {
      const assignedWorkerIds = new Set(hour.sector_workers.flatMap(workerIdsForSector));
      return result.people.filter((person) => (
        personIsActiveInSlot(person, slot, shifts)
        && !assignedWorkerIds.has(person.id)
      ));
    })
  ), [result.hourly_coverage, result.people, shifts]);
  const applyManualEdit = (mutator: (current: CalculatorResponse) => CalculatorResponse) => {
    onEditResult((current) => recomputeEditedResult(mutator(current), shifts));
  };
  const moveSeat = (source: ScheduleSeatRef, target: ScheduleSeatRef) => {
    applyManualEdit((current) => moveScheduleSeat(current, source, target));
  };
  const removeSeat = (target: ScheduleSeatRef) => {
    applyManualEdit((current) => removeScheduleSeat(current, target));
  };
  const addSeat = (target: ScheduleSeatRef) => {
    const suggestedId = nextGeneratedPersonId(result.people);
    const enteredId = window.prompt('Oseba za ta sedež (obstoječa oznaka ali nova)', suggestedId);
    if (enteredId === null) {
      return;
    }
    const normalizedId = enteredId.trim();
    if (!normalizedId) {
      return;
    }
    const existingPerson = result.people.find((person) => (
      person.id.toLowerCase() === normalizedId.toLowerCase()
      || personDisplayId(person).toLowerCase() === normalizedId.toLowerCase()
    ));
    if (existingPerson) {
      applyManualEdit((current) => assignScheduleSeat(current, target, existingPerson));
      return;
    }
    const enteredLicense = window.prompt('Licenca nove osebe: FL, APS ali ACS', 'FL');
    if (enteredLicense === null) {
      return;
    }
    const license = enteredLicense.trim().toUpperCase();
    if (license !== 'FL' && license !== 'APS' && license !== 'ACS') {
      window.alert('Licenca mora biti FL, APS ali ACS.');
      return;
    }
    const enteredShift = window.prompt('Izmena nove osebe', 'A7');
    if (enteredShift === null) {
      return;
    }
    const shift = enteredShift.trim() || 'A7';
    const enteredRole = window.prompt('Vloga nove osebe (prazno, V1, V2, V3 ali FMP)', '');
    if (enteredRole === null) {
      return;
    }
    const rawRole = enteredRole.trim().toUpperCase();
    const role = rawRole === '' ? null : rawRole;
    if (role !== null && !['V1', 'V2', 'V3', 'FMP'].includes(role)) {
      window.alert('Vloga mora biti prazna, V1, V2, V3 ali FMP.');
      return;
    }
    const personLicense: License = role && leaderRoleDisplayId(role) ? 'FL' : license;
    const person: VirtualPerson = {
      id: normalizedId,
      license: personLicense,
      shift,
      role,
      sector_hours: 0,
      max_sector_hours: estimatedMaxSectorHoursForShift(shift, shifts),
      utilization_percent: 0,
      used_as_sector_controller: false,
      source: 'what-if',
    };
    applyManualEdit((current) => assignScheduleSeat(current, target, person));
  };

  return (
    <section className="panel schedule-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Razpored po sektorjih</p>
          <h2>Kdo dela v kateri uri</h2>
        </div>
        <div className="panel-actions">
          {hiddenNightHourCount > 0 ? (
            <button
              className="secondary-button compact-button schedule-toggle-button"
              onClick={() => setShowNightHours((current) => !current)}
              type="button"
            >
              {showNightHours ? 'Skrij 01:00-05:00' : 'Prikaži 01:00-05:00'}
            </button>
          ) : null}
          <CopyButton textFactory={() => formatSectorScheduleForCopy(result)} />
        </div>
      </div>
      <div className="schedule-scroll" aria-label="Razpored ljudi po sektorjih in urah">
        <div className="schedule-grid" style={{ gridTemplateColumns: `70px repeat(${maxSectors}, minmax(124px, 1fr)) minmax(168px, 1.12fr)` }}>
          <div className="schedule-cell schedule-head sticky-col">Ura</div>
          {sectorHeaders.map((sector) => (
            <div className="schedule-cell schedule-head" key={sector}>{sector}</div>
          ))}
          <div className="schedule-cell schedule-head break-head">Pavza</div>
          {visibleHourlyCoverage.flatMap(({ hour, slot }) => [
            <div className="schedule-cell schedule-hour sticky-col" key={`${hour.hour}-label`}>
              <ScheduleHourLabel hour={hour.hour} />
            </div>,
            ...hour.sector_workers.map((sector, index) => {
              const editableSector = sector ?? {
                sector_name: sectorNameForIndex(index),
                lower_worker: '',
                upper_worker: '',
              };
              const cellViolations = sectorCellViolations(result, sector, slot, peopleById, shifts);
              const covered = sector !== null && cellViolations.length === 0 && isCoveredSector(sector, peopleById);

              return (
                <div
                  className={`schedule-cell ${sector ? 'assigned' : 'closed'} ${sector && !covered ? 'incomplete-sector' : ''}`}
                  key={`${hour.hour}-${index}`}
                  title={cellViolations.length ? cellViolations.join('\n') : undefined}
                >
                  {!sector ? <span className="closed-label">Zaprto</span> : null}
                  <span className="worker-pair">
                    <ScheduleSeatChip
                      sector={editableSector}
                      seat="lower"
                      seatRef={{ slot, sectorIndex: index, seat: 'lower' }}
                      result={result}
                      shifts={shifts}
                      peopleById={peopleById}
                      onMoveSeat={moveSeat}
                      onRemoveSeat={removeSeat}
                      onAddSeat={addSeat}
                    />
                    <ScheduleSeatChip
                      sector={editableSector}
                      seat="upper"
                      seatRef={{ slot, sectorIndex: index, seat: 'upper' }}
                      result={result}
                      shifts={shifts}
                      peopleById={peopleById}
                      onMoveSeat={moveSeat}
                      onRemoveSeat={removeSeat}
                      onAddSeat={addSeat}
                    />
                  </span>
                </div>
              );
            }),
            <div className="schedule-cell break-cell" key={`${hour.hour}-break`}>
              {breakPeopleBySlot[slot]?.length ? (
                <span
                  className="break-worker-list"
                  title={breakPeopleBySlot[slot].map((person) => `${personDisplayId(person)} ${person.license} ${person.shift}`).join(', ')}
                >
                  {breakPeopleBySlot[slot].map((person) => {
                    const color = workerColor(person.id);
                    const blockedByRestRule = wouldExceedMaxConsecutiveSectorHours(result, person.id, slot);
                    return (
                      <span
                        className={`break-person-pill ${blockedByRestRule ? 'break-person-blocked' : ''}`}
                        key={person.id}
                        style={{ backgroundColor: color.background, borderColor: color.border }}
                        title={blockedByRestRule ? 'Ne vpisuj: kršitev ritma 2-1-2, oseba bi imela več kot 2 uri zapored na sektorju.' : undefined}
                      >
                        {personDisplayId(person)}
                      </span>
                    );
                  })}
                </span>
              ) : (
                <span className="muted-cell">—</span>
              )}
            </div>,
          ])}
        </div>
      </div>
    </section>
  );
}

function ParetoAnalysis({
  points,
  currentMaxSectorHours,
}: {
  points: ParetoPoint[];
  currentMaxSectorHours?: number;
}) {
  if (points.length === 0) {
    return null;
  }

  const bestPoint = points.reduce((best, point) => (
    point.coverage_percent > best.coverage_percent
      || (point.coverage_percent === best.coverage_percent && point.people_limit < best.people_limit)
      ? point
      : best
  ), points[0]);
  const firstFeasible = points.find((point) => point.feasible);
  const targetHours = currentMaxSectorHours ?? bestPoint.max_sector_hours;
  const sameAsCurrent = points.find((point) => point.max_sector_hours >= targetHours);

  return (
    <section className="panel pareto-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Pareto</p>
          <h2>Uspešnost po številu ljudi</h2>
        </div>
        <div className="panel-actions">
          <CopyButton textFactory={() => formatParetoPointsForCopy(points)} />
        </div>
      </div>

      <div className="pareto-summary">
        <div>
          <span>Najboljša uspešnost</span>
          <strong>{bestPoint.coverage_percent}%</strong>
          <small>{bestPoint.max_sector_hours}/{bestPoint.requested_sector_hours} sektorskih ur</small>
        </div>
        <div>
          <span>Najmanj za 100%</span>
          <strong>{firstFeasible ? firstFeasible.people_limit : '—'}</strong>
          <small>{firstFeasible ? `${firstFeasible.planned_people} uporabljenih` : 'ni doseženo'}</small>
        </div>
        <div>
          <span>{currentMaxSectorHours === undefined ? 'Najmanj za najboljši rezultat' : 'Najmanj za isti rezultat'}</span>
          <strong>{sameAsCurrent ? sameAsCurrent.people_limit : '—'}</strong>
          <small>{sameAsCurrent ? `${sameAsCurrent.coverage_percent}% uspešnost` : 'ni najdeno'}</small>
        </div>
      </div>

      <div className="responsive-table">
        <table className="pareto-table">
          <thead>
            <tr>
              <th>Limit</th>
              <th>Uporab.</th>
              <th>Uspešnost</th>
              <th>Sektorske ure</th>
              <th>Manjka</th>
              <th>Izkorišč.</th>
              <th>Officerji</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.people_limit} className={point.feasible ? 'pareto-feasible' : ''}>
                <td className="strong">{point.people_limit}</td>
                <td>{point.planned_people}</td>
                <td>
                  <div className="pareto-coverage">
                    <div className="pareto-track">
                      <div className="pareto-fill" style={{ width: `${point.coverage_percent}%` }} />
                    </div>
                    <strong>{point.coverage_percent}%</strong>
                  </div>
                </td>
                <td>{point.max_sector_hours}/{point.requested_sector_hours}</td>
                <td>{point.missing_sector_hours}</td>
                <td>{point.utilization_percent}%</td>
                <td>{point.used_officers}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function MetricInfo({ text }: { text: string }) {
  return (
    <span className="metric-info" tabIndex={0} aria-label={text} data-tip={text}>
      ?
    </span>
  );
}

function shouldShowOnboarding(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }
  try {
    return window.localStorage.getItem(onboardingCompletedStorageKey) !== 'true';
  } catch {
    return true;
  }
}

function ATCConfMakerOnboarding({
  onDismiss,
  onStartTour,
}: {
  onDismiss: () => void;
  onStartTour: (kind: GuidedTourKind) => void;
}) {
  const [step, setStep] = useState<0 | 1>(0);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onDismiss();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [onDismiss]);

  const isFirstStep = step === 0;
  return (
    <div className="onboarding-backdrop" role="presentation">
      <section
        aria-describedby="konfmaker-onboarding-description"
        aria-labelledby="konfmaker-onboarding-title"
        aria-modal="true"
        className="onboarding-dialog"
        role="dialog"
      >
        <div className="onboarding-topline">
          <div>
            <p className="eyebrow">Hiter začetek</p>
            <span className="onboarding-step-count">{step + 1} od 2</span>
          </div>
          <button aria-label="Zapri vodič" className="onboarding-close" onClick={onDismiss} type="button">×</button>
        </div>

        <div className="onboarding-progress" aria-label={`Korak ${step + 1} od 2`}>
          <span className="complete" />
          <span className={step === 1 ? 'complete' : ''} />
        </div>

        {isFirstStep ? (
          <div className="onboarding-content">
            <span className="onboarding-number">1</span>
            <h2 id="konfmaker-onboarding-title">Nova konfiguracija iz vhodnih podatkov</h2>
            <p id="konfmaker-onboarding-description">
              Vnesi limit ljudi ali ga izpusti in določi samo želeno razmerje licenc. Nato nastavi ostale vhodne podatke,
              izberi želeno število odprtih sektorjev po urah in zaženi izračun.
            </p>
            <div className="onboarding-flow">
              <div>
                <strong>1. Ljudje in licence</strong>
                <span>Limit ljudi je neobvezen; uporabiš lahko samo odstotke FL, APS in ACS.</span>
              </div>
              <div>
                <strong>2. Želena odprtost</strong>
                <span>Po urah označi, koliko sektorjev želiš imeti odprtih.</span>
              </div>
              <div>
                <strong>3. Poženi solver</strong>
                <span>Klikni »Izračunaj potrebno zasedbo« in ATCConfMaker pripravi sestavo.</span>
              </div>
            </div>
          </div>
        ) : (
          <div className="onboarding-content">
            <span className="onboarding-number">2</span>
            <h2 id="konfmaker-onboarding-title">Dodelaj obstoječo ročno konfiguracijo</h2>
            <p id="konfmaker-onboarding-description">
              Izberi obstoječo ročno konfiguracijo, jo prenesi v ATCConfMaker in jo nato popravi, izboljšaj ali dodatno optimiziraj.
            </p>
            <div className="onboarding-flow">
              <div>
                <strong>1. Izberi konfiguracijo</strong>
                <span>Odpri zavihek »Ročne konfiguracije« in izberi želeno sestavo.</span>
              </div>
              <div>
                <strong>2. Prenesi cilj v ATCConfMaker</strong>
                <span>Klikni »Prenesi sektorske ure in št. ljudi v ATCConfMaker«; konkretne izmene se ne prenesejo.</span>
              </div>
              <div>
                <strong>3. Izračunaj novo sestavo</strong>
                <span>Preveri licence, odprtost in prioritete, nato naj solver izdela novo sestavo izmen.</span>
              </div>
            </div>
          </div>
        )}

        <div className="onboarding-actions">
          <button className="secondary-button compact-button" onClick={onDismiss} type="button">Preskoči vodič</button>
          <div>
            {!isFirstStep ? (
              <button className="secondary-button compact-button" onClick={() => setStep(0)} type="button">Nazaj</button>
            ) : null}
            {isFirstStep ? (
              <button autoFocus className="primary-button compact-button" onClick={() => setStep(1)} type="button">Naprej</button>
            ) : (
              <>
                <button className="secondary-button compact-button" onClick={() => onStartTour('manual-configuration')} type="button">
                  Vodi me: ročna konfiguracija
                </button>
                <button autoFocus className="primary-button compact-button" onClick={() => onStartTour('new-configuration')} type="button">
                  Vodi me: nova konfiguracija
                </button>
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

const guidedTourSteps: Record<GuidedTourKind, GuidedTourStep[]> = {
  'new-configuration': [
    {
      tab: 'calculator',
      target: '[data-tour="calculation-mode"]',
      title: '1. Izberi način izračuna',
      description: 'Za novo konfiguracijo izberi »Odprtost sektorjev«. ATCConfMaker bo iz želene odprtosti izračunal potrebno zasedbo.',
    },
    {
      tab: 'calculator',
      target: '[data-tour="license-people"]',
      title: '2. Določi ljudi in licence',
      description: 'Limit ljudi lahko vključiš ali izpustiš. Nato nastavi ciljne odstotke licenc FL, APS in ACS.',
    },
    {
      tab: 'calculator',
      target: '[data-tour="fixed-shifts"]',
      title: '3. Preveri posebne vhodne izmene',
      description: 'Tu po potrebi vpišeš fiksne izmene. Pod tem so še office, officerji in omejitve vodij; prazne možnosti lahko preprosto preskočiš.',
    },
    {
      tab: 'calculator',
      target: '[data-tour="optimization-priorities"]',
      title: '4. Izberi, kaj je najpomembnejše',
      description: 'Z drsniki določiš prednost polni pokritosti SH, razmerju licenc in krajšim 7-urnim izmenam.',
    },
    {
      tab: 'calculator',
      target: '[data-tour="sector-demand"]',
      title: '5. Vnesi želeno odprtost',
      description: 'Klikni celice po urah in označi, koliko sektorjev želiš imeti odprtih. To je cilj, ki ga bo solver poskušal doseči.',
    },
    {
      tab: 'calculator',
      target: '[data-tour="calculate-button"]',
      title: '6. Zaženi solver',
      description: 'Klikni osvetljeni gumb »Izračunaj potrebno zasedbo«. Vodič se bo zaprl, izračun pa bo normalno stekel.',
      finishOnTargetClick: true,
    },
  ],
  'manual-configuration': [
    {
      tab: 'manual-configs',
      target: '[data-tour="manual-config-list"]',
      title: '1. Izberi ročno konfiguracijo',
      description: 'V seznamu klikni konfiguracijo, ki jo želiš dodelati. Na desni se bodo prikazale njene podrobnosti in dejanja.',
    },
    {
      tab: 'manual-configs',
      target: '[data-tour="manual-transfer-demand"]',
      title: '2. Prenesi cilj v ATCConfMaker',
      description: 'Klikni »Prenesi sektorske ure in št. ljudi v ATCConfMaker«. Preneseta se ciljna odprtost po urah in število ljudi, ne konkretna sestava izmen.',
      advanceOnTargetClick: true,
    },
    {
      tab: 'calculator',
      target: '[data-tour="license-people"]',
      title: '3. Preveri število ljudi in licence',
      description: 'ATCConfMaker je vključil limit s prenesenim številom ljudi. Preveri še ciljno razmerje licenc FL, APS in ACS.',
    },
    {
      tab: 'calculator',
      target: '[data-tour="sector-demand"]',
      title: '4. Preveri prenesene sektorske ure',
      description: 'Želena odprtost po posameznih urah je prenesena iz ročne konfiguracije. Po potrebi jo lahko še spremeniš, konkretne izmene pa niso bile prenesene.',
    },
    {
      tab: 'calculator',
      target: '[data-tour="optimization-priorities"]',
      title: '5. Nastavi cilj izboljšave',
      description: 'Določi, ali naj solver predvsem ohrani SH, drži razmerje licenc ali pogosteje zamenja 8-urne izmene s 7-urnimi.',
    },
    {
      tab: 'calculator',
      target: '[data-tour="calculate-button"]',
      title: '6. Ponovno optimiziraj',
      description: 'Klikni osvetljeni gumb za izračun. Solver bo iz prenesenega cilja SH in limita ljudi izdelal novo sestavo izmen.',
      finishOnTargetClick: true,
    },
  ],
};

function GuidedTour({
  kind,
  onClose,
  onRequestTab,
}: {
  kind: GuidedTourKind;
  onClose: () => void;
  onRequestTab: (tab: Tab) => void;
}) {
  const [stepIndex, setStepIndex] = useState(0);
  const [targetAvailable, setTargetAvailable] = useState(false);
  const steps = guidedTourSteps[kind];
  const step = steps[stepIndex];

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  useEffect(() => {
    onRequestTab(step.tab);
    let highlightedTarget: HTMLElement | null = null;
    let targetClickHandler: (() => void) | null = null;
    let scrollTimer: ReturnType<typeof window.setTimeout> | null = null;

    const connectTarget = () => {
      if (highlightedTarget) {
        return true;
      }
      const nextTarget = document.querySelector<HTMLElement>(step.target);
      if (!nextTarget) {
        return false;
      }
      highlightedTarget = nextTarget;
      highlightedTarget.classList.add('guided-tour-target');
      setTargetAvailable(true);
      scrollTimer = window.setTimeout(() => {
        highlightedTarget?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
      }, 80);

      if (step.advanceOnTargetClick || step.finishOnTargetClick) {
        targetClickHandler = () => {
          window.setTimeout(() => {
            if (step.finishOnTargetClick) {
              onClose();
            } else {
              setTargetAvailable(false);
              setStepIndex((current) => Math.min(current + 1, steps.length - 1));
            }
          }, 0);
        };
        highlightedTarget.addEventListener('click', targetClickHandler);
      }
      return true;
    };

    const observer = new MutationObserver(() => {
      if (connectTarget()) {
        observer.disconnect();
      }
    });
    if (!connectTarget()) {
      observer.observe(document.body, { childList: true, subtree: true });
    }

    return () => {
      observer.disconnect();
      if (scrollTimer !== null) {
        window.clearTimeout(scrollTimer);
      }
      if (highlightedTarget) {
        highlightedTarget.classList.remove('guided-tour-target');
        if (targetClickHandler) {
          highlightedTarget.removeEventListener('click', targetClickHandler);
        }
      }
    };
  }, [onClose, onRequestTab, step, steps.length]);

  const isLastStep = stepIndex === steps.length - 1;
  const unavailableHint = kind === 'manual-configuration' && step.target === '[data-tour="manual-transfer-demand"]'
    ? 'Najprej klikni konfiguracijo v seznamu. Ko se podrobnosti odprejo, se bo označil pravi gumb.'
    : 'Ta del strani se še nalaga. Vodič bo nadaljeval takoj, ko bo pripravljen.';

  return (
    <>
      <div className="guided-tour-dimmer" aria-hidden="true" />
      <aside aria-live="polite" className="guided-tour-popover" role="dialog" aria-label="Interaktivni vodič po ATCConfMakerju">
        <div className="guided-tour-popover-top">
          <div>
            <p className="eyebrow">Interaktivni vodič</p>
            <span>{stepIndex + 1} od {steps.length}</span>
          </div>
          <button aria-label="Zapri interaktivni vodič" className="onboarding-close" onClick={onClose} type="button">×</button>
        </div>
        <div className="guided-tour-progress" aria-hidden="true">
          <span style={{ width: `${((stepIndex + 1) / steps.length) * 100}%` }} />
        </div>
        <h3>{step.title}</h3>
        <p>{step.description}</p>
        {!targetAvailable ? <div className="guided-tour-hint">{unavailableHint}</div> : null}
        <div className="guided-tour-actions">
          <button className="secondary-button compact-button" onClick={onClose} type="button">
            Končaj vodič
          </button>
          <div>
            {stepIndex > 0 ? (
              <button
                className="secondary-button compact-button"
                onClick={() => {
                  setTargetAvailable(false);
                  setStepIndex((current) => current - 1);
                }}
                type="button"
              >
                Nazaj
              </button>
            ) : null}
            {isLastStep ? (
              <span className="guided-tour-click-prompt">Klikni osvetljeni gumb</span>
            ) : step.advanceOnTargetClick ? (
              <span className="guided-tour-click-prompt">Klikni osvetljeni gumb</span>
            ) : (
              <button
                className="primary-button compact-button"
                disabled={!targetAvailable}
                onClick={() => {
                  setTargetAvailable(false);
                  setStepIndex((current) => current + 1);
                }}
                type="button"
              >
                Naprej
              </button>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}

function ConfigurationSimilaritySummary({
  comparison,
  isLoading,
  error,
  onOpenComparison,
}: {
  comparison: ConfigurationComparisonResult | null;
  isLoading: boolean;
  error: string | null;
  onOpenComparison: () => void;
}) {
  if (isLoading && !comparison) {
    return (
      <section className="panel similarity-panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Primerjava</p>
            <h2>Iščem najbližje konfiguracije ...</h2>
          </div>
        </div>
      </section>
    );
  }

  if (error && !comparison) {
    return <div className="error-box">{error}</div>;
  }

  if (!comparison || comparison.matches.length === 0) {
    return null;
  }

  return (
    <section className="panel similarity-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Primerjava z bazo</p>
          <h2>Najbolj podobne konfiguracije</h2>
        </div>
        <button className="secondary-button compact-button" onClick={onOpenComparison} type="button">
          Odpri primerjalnik
        </button>
      </div>
      {comparison.duplicate_warning ? <div className="warning-box">{comparison.duplicate_warning}</div> : null}
      {error ? <div className="error-box">{error}</div> : null}
      <div className="similarity-card-grid">
        {comparison.matches.slice(0, 4).map((match) => (
          <div className="similarity-card" key={match.id}>
            <span>{formatComparisonSource(match.source_type, match.source_label)}</span>
            <strong>{match.name} · {match.similarity}%</strong>
            <small>
              SH {formatSignedValue(match.sh_diff)} · ljudje {formatSignedValue(match.people_diff)}
              {' · '}sektorji Δ{match.sector_profile_diff}
            </small>
          </div>
        ))}
      </div>
    </section>
  );
}

function PairOverview({ left, right }: { left: PairConfigMetrics; right: PairConfigMetrics }) {
  const rows = [
    { label: 'SH', left: left.sectorHours, right: right.sectorHours, suffix: '' },
    { label: 'Obveznih FL', left: left.requiredFl, right: right.requiredFl, suffix: '' },
    { label: 'Izkoriščenost', left: left.utilizationPercent, right: right.utilizationPercent, suffix: '%' },
    { label: 'Kontrolorske ure', left: left.controllerHours, right: right.controllerHours, suffix: '' },
    { label: 'Ljudi', left: left.people, right: right.people, suffix: '' },
    { label: 'Povp. ure/osebo', left: left.averageHours, right: right.averageHours, suffix: ' h' },
  ];

  return (
    <div className="pair-overview-grid">
      {rows.map((row) => {
        const delta = row.right - row.left;
        return (
          <div key={row.label}>
            <span>{row.label}</span>
            <strong>{row.left}{row.suffix} / {row.right}{row.suffix}</strong>
            <small className={pairDeltaClass(delta)}>Desno-levo {pairDeltaLabel(delta, row.suffix)}</small>
          </div>
        );
      })}
      <div className="wide-explain-cell">
        <span>Licence</span>
        <strong>{left.name}: {left.licenseText}</strong>
        <small>{right.name}: {right.licenseText}</small>
      </div>
    </div>
  );
}

function PairCardHeader({ metrics, side }: { metrics: PairConfigMetrics; side: 'Levo' | 'Desno' }) {
  return (
    <div className="pair-card-header">
      <div>
        <span>{side}</span>
        <strong>{metrics.name}</strong>
        <small>{metrics.source}</small>
      </div>
      <div className="pair-card-score">
        <strong>{metrics.sectorHours} SH</strong>
        <span>{metrics.people} ljudi</span>
      </div>
    </div>
  );
}

function PairShiftList({ metrics, other }: { metrics: PairConfigMetrics; other: PairConfigMetrics }) {
  const ownRows = mapShiftRows(metrics.shiftRows);
  const otherRows = mapShiftRows(other.shiftRows);
  const rows = [...new Map([...metrics.shiftRows, ...other.shiftRows].map((row) => [row.key, row])).values()]
    .sort((first, second) => first.sort - second.sort || first.label.localeCompare(second.label));

  return (
    <div className="pair-list">
      {rows.map((row) => {
        const own = ownRows.get(row.key);
        const counterpart = otherRows.get(row.key);
        const delta = (own?.total ?? 0) - (counterpart?.total ?? 0);
        return (
          <div className={`pair-list-row ${delta === 0 ? '' : 'has-diff'}`} key={row.key}>
            <div>
              <strong>{row.label}</strong>
              <span>
                {own ? `FL ${own.fl} · APS ${own.aps} · ACS ${own.acs}` : 'ni v konfiguraciji'}
              </span>
            </div>
            <div>
              <b>{own?.total ?? 0}</b>
              <small className={pairDeltaClass(delta)}>{pairDeltaLabel(delta)}</small>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PairHourlyList({ metrics, other }: { metrics: PairConfigMetrics; other: PairConfigMetrics }) {
  const otherRows = mapHourRows(other.hourlyRows);
  const maxOpen = Math.max(
    1,
    ...metrics.hourlyRows.map((row) => row.open),
    ...other.hourlyRows.map((row) => row.open),
  );

  return (
    <div className="pair-hour-list">
      {metrics.hourlyRows.map((row) => {
        const counterpart = otherRows.get(row.hour);
        const delta = row.open - (counterpart?.open ?? 0);
        return (
          <div className={`pair-hour-row ${delta === 0 ? '' : 'has-diff'}`} key={row.hour}>
            <span>{row.hour}</span>
            <div className="pair-hour-track">
              <div style={{ width: `${(row.open / maxOpen) * 100}%` }} />
            </div>
            <strong>{row.open}</strong>
            <small className={pairDeltaClass(delta)}>{pairDeltaLabel(delta)}</small>
          </div>
        );
      })}
    </div>
  );
}

function PairScheduleWorkerChip({
  workerId,
  peopleById,
}: {
  workerId: string;
  peopleById: Map<string, VirtualPerson>;
}) {
  const person = peopleById.get(workerId);
  const color = workerColor(workerId);
  const label = person ? personDisplayId(person) : workerId;
  const meta = person ? `${person.license} · ${person.shift}` : '';
  return (
    <span
      className="pair-schedule-worker"
      style={{ backgroundColor: color.background, borderColor: color.border }}
      title={meta ? `${label} / ${meta}` : label}
    >
      <strong>{label}</strong>
      {meta ? <small>{meta}</small> : null}
    </span>
  );
}

function PairSectorFlow({ left, right }: { left: PairConfigMetrics; right: PairConfigMetrics }) {
  const leftResult = left.calculatorResult;
  const rightResult = right.calculatorResult;
  const leftPeopleById = useMemo(
    () => new Map((leftResult?.people ?? []).map((person) => [person.id, person])),
    [leftResult],
  );
  const rightPeopleById = useMemo(
    () => new Map((rightResult?.people ?? []).map((person) => [person.id, person])),
    [rightResult],
  );
  const maxSectors = Math.max(
    1,
    ...(leftResult?.hourly_coverage.map((hour) => hour.sector_workers.length) ?? []),
    ...(rightResult?.hourly_coverage.map((hour) => hour.sector_workers.length) ?? []),
  );
  const leftBreakPeopleBySlot = useMemo(() => {
    if (!leftResult) {
      return [];
    }
    return leftResult.hourly_coverage.map((hour, slot) => {
      const assignedWorkerIds = new Set(hour.sector_workers.flatMap(workerIdsForSector));
      return leftResult.people.filter((person) => (
        personIsActiveInSlot(person, slot, fallbackSettings.shifts)
        && !assignedWorkerIds.has(person.id)
      ));
    });
  }, [leftResult]);
  const rightBreakPeopleBySlot = useMemo(() => {
    if (!rightResult) {
      return [];
    }
    return rightResult.hourly_coverage.map((hour, slot) => {
      const assignedWorkerIds = new Set(hour.sector_workers.flatMap(workerIdsForSector));
      return rightResult.people.filter((person) => (
        personIsActiveInSlot(person, slot, fallbackSettings.shifts)
        && !assignedWorkerIds.has(person.id)
      ));
    });
  }, [rightResult]);

  if (!leftResult || !rightResult) {
    return <div className="manual-audit-empty">Podroben sektorski razpored za izbrani par ni na voljo.</div>;
  }

  const visibleHours = Array.from(
    { length: Math.max(leftResult.hourly_coverage.length, rightResult.hourly_coverage.length) },
    (_, slot) => ({
      slot,
      hour: leftResult.hourly_coverage[slot]?.hour
        ?? rightResult.hourly_coverage[slot]?.hour
        ?? hourLabels[slot]
        ?? String(slot),
    }),
  );
  const sectorHeaders = Array.from(
    { length: maxSectors },
    (_, index) => sectorColumnLabels[index] ?? `S${index + 1}`,
  );
  const gridTemplateColumns = `38px repeat(${maxSectors}, minmax(0, 1fr)) 56px repeat(${maxSectors}, minmax(0, 1fr)) 56px`;
  const sideColumnCount = maxSectors + 1;

  const renderSideCells = (
    side: 'left' | 'right',
    result: CalculatorResponse,
    peopleById: Map<string, VirtualPerson>,
    breakPeopleBySlot: VirtualPerson[][],
    slot: number,
    hourKey: string,
  ) => {
    const hour = result.hourly_coverage[slot];
    return [
      ...Array.from({ length: maxSectors }, (_, sectorIndex) => {
        const sector = hour?.sector_workers[sectorIndex] ?? null;
        const workers = sector ? workerIdsForSector(sector) : [];
        const dividerClass = side === 'right' && sectorIndex === 0 ? ' pair-schedule-divider' : '';
        return (
          <div
            className={`pair-schedule-cell pair-schedule-sector ${sector ? 'assigned' : 'closed'}${dividerClass}`}
            key={`${hourKey}-${side}-${sectorIndex}`}
            title={sector?.sector_name ?? 'Zaprto'}
          >
            {workers.length > 0 ? (
              <span className="pair-schedule-worker-pair">
                {workers.map((workerId) => (
                  <PairScheduleWorkerChip
                    key={`${sectorIndex}-${workerId}`}
                    workerId={workerId}
                    peopleById={peopleById}
                  />
                ))}
              </span>
            ) : (
              <span className="pair-schedule-closed">—</span>
            )}
          </div>
        );
      }),
      <div className="pair-schedule-cell pair-schedule-break" key={`${hourKey}-${side}-break`}>
        {breakPeopleBySlot[slot]?.length ? (
          <span className="pair-schedule-break-list">
            {breakPeopleBySlot[slot].map((person) => {
              const color = workerColor(person.id);
              return (
                <span
                  key={person.id}
                  style={{ backgroundColor: color.background, borderColor: color.border }}
                  title={`${personDisplayId(person)} / ${person.license} / ${person.shift}`}
                >
                  {personDisplayId(person)}
                </span>
              );
            })}
          </span>
        ) : (
          <span className="pair-schedule-closed">—</span>
        )}
      </div>,
    ];
  };

  return (
    <div className="pair-schedule pair-combined-schedule">
      <div
        className="pair-combined-schedule-headings"
        style={{ gridTemplateColumns }}
      >
        <div className="pair-combined-hour-gutter" />
        <div className="pair-combined-config-header" style={{ gridColumn: `span ${sideColumnCount}` }}>
          <PairCardHeader metrics={left} side="Levo" />
        </div>
        <div
          className="pair-combined-config-header pair-combined-config-header-right"
          style={{ gridColumn: `span ${sideColumnCount}` }}
        >
          <PairCardHeader metrics={right} side="Desno" />
        </div>
      </div>
      <div className="pair-schedule-frame">
        <div
          className="pair-schedule-grid"
          style={{ gridTemplateColumns }}
        >
          <div className="pair-schedule-cell pair-schedule-head">Ura</div>
          {sectorHeaders.map((sector) => (
            <div className="pair-schedule-cell pair-schedule-head" key={`left-${sector}`}>{sector}</div>
          ))}
          <div className="pair-schedule-cell pair-schedule-head">Pavza</div>
          {sectorHeaders.map((sector, index) => (
            <div
              className={`pair-schedule-cell pair-schedule-head${index === 0 ? ' pair-schedule-divider' : ''}`}
              key={`right-${sector}`}
            >
              {sector}
            </div>
          ))}
          <div className="pair-schedule-cell pair-schedule-head">Pavza</div>

          {visibleHours.flatMap(({ hour, slot }) => [
            <div className="pair-schedule-cell pair-schedule-hour" key={`${hour}-hour`}>
              <ScheduleHourLabel hour={hour} />
            </div>,
            ...renderSideCells('left', leftResult, leftPeopleById, leftBreakPeopleBySlot, slot, hour),
            ...renderSideCells('right', rightResult, rightPeopleById, rightBreakPeopleBySlot, slot, hour),
          ])}
        </div>
      </div>
    </div>
  );
}

function PairWorkload({ metrics, other }: { metrics: PairConfigMetrics; other: PairConfigMetrics }) {
  const result = metrics.calculatorResult;
  if (result) {
    return (
      <div className="pair-people-table-wrap">
        <table className="pair-people-table">
          <thead>
            <tr>
              <th>Oseba</th>
              <th>Vloga</th>
              <th>Vir</th>
              <th>Izmena</th>
              <th>Lic.</th>
              <th>SH</th>
              <th>Izkoriščenost</th>
            </tr>
          </thead>
          <tbody>
            {result.people.map((person) => (
              <tr key={person.id}>
                <td className="strong">{personDisplayId(person)}</td>
                <td>{person.role ?? '—'}</td>
                <td title={personSourceLabel(person.source)}>{personSourceLabel(person.source)}</td>
                <td>{person.shift}</td>
                <td><span className={`pair-license pair-license-${person.license.toLowerCase()}`}>{person.license}</span></td>
                <td>{person.sector_hours}/{person.max_sector_hours}</td>
                <td>
                  <div className="pair-utilization-cell">
                    <div className="pair-utilization-track">
                      <div style={{ width: `${clamp(person.utilization_percent, 0, 100)}%` }} />
                    </div>
                    <strong>{person.utilization_percent}%</strong>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  const ownBuckets = mapWorkloadBuckets(metrics.workloadBuckets);
  const otherBuckets = mapWorkloadBuckets(other.workloadBuckets);
  const bucketHours = [...new Set([...metrics.workloadBuckets, ...other.workloadBuckets].map((bucket) => bucket.hours))]
    .sort((first, second) => second - first);

  return (
    <div className="pair-workload">
      <div className="pair-workload-summary">
        <strong>{metrics.utilizationPercent}%</strong>
        <span>{metrics.averageHours} h/osebo · {metrics.controllerHours} kontrolorskih ur</span>
      </div>
      <div className="pair-bucket-list">
        {bucketHours.map((hours) => {
          const own = ownBuckets.get(hours)?.count ?? 0;
          const counterpart = otherBuckets.get(hours)?.count ?? 0;
          const delta = own - counterpart;
          return (
            <div className="pair-bucket-row" key={hours}>
              <span>{hours} h</span>
              <strong>{own}</strong>
              <small className={pairDeltaClass(delta)}>{pairDeltaLabel(delta)}</small>
            </div>
          );
        })}
      </div>
      <div className="pair-person-list">
        {metrics.workloadPeople.map((person) => (
          <div key={`${person.label}-${person.meta}`}>
            <span>{person.label}</span>
            <strong>{person.hours} h</strong>
            <small>{person.meta}</small>
          </div>
        ))}
      </div>
    </div>
  );
}

function ManualConfigurationPairComparison({
  left,
  right,
}: {
  left: PairConfigMetrics;
  right: PairConfigMetrics;
}) {
  return (
    <div className="manual-pair-comparison">
      <section className="panel pair-overview-panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Splošno</p>
            <h2>{left.name} proti {right.name}</h2>
          </div>
        </div>
        <PairOverview left={left} right={right} />
      </section>

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Sestava</p>
            <h2>Predlagana sestava izmen</h2>
          </div>
        </div>
        <div className="config-pair-grid">
          <div className="config-compare-card">
            <PairCardHeader metrics={left} side="Levo" />
            <PairShiftList metrics={left} other={right} />
          </div>
          <div className="config-compare-card">
            <PairCardHeader metrics={right} side="Desno" />
            <PairShiftList metrics={right} other={left} />
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Odprtost</p>
            <h2>Odprtost po urah</h2>
          </div>
        </div>
        <div className="config-pair-grid">
          <div className="config-compare-card">
            <PairCardHeader metrics={left} side="Levo" />
            <PairHourlyList metrics={left} other={right} />
          </div>
          <div className="config-compare-card">
            <PairCardHeader metrics={right} side="Desno" />
            <PairHourlyList metrics={right} other={left} />
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Sektorji</p>
            <h2>Razpored po sektorjih</h2>
          </div>
        </div>
        <PairSectorFlow left={left} right={right} />
      </section>

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Ljudje</p>
            <h2>Izkoriščenost ljudi</h2>
          </div>
        </div>
        <div className="config-pair-grid">
          <div className="config-compare-card">
            <PairCardHeader metrics={left} side="Levo" />
            <PairWorkload metrics={left} other={right} />
          </div>
          <div className="config-compare-card">
            <PairCardHeader metrics={right} side="Desno" />
            <PairWorkload metrics={right} other={left} />
          </div>
        </div>
      </section>
    </div>
  );
}

const CURRENT_RESULT_COMPARISON_ID = '__current_calculator_result__';

function ConfigurationComparisonPanel({
  result,
  comparison,
  isLoading,
  error,
  onOpenMatch,
  manualLibrary,
}: {
  result: CalculatorResponse | null;
  comparison: ConfigurationComparisonResult | null;
  isLoading: boolean;
  error: string | null;
  onOpenMatch: (id: string) => void;
  manualLibrary: ManualConfigurationLibrary | null;
}) {
  const [pairLibrary, setPairLibrary] = useState<ManualConfigurationLibrary | null>(null);
  const [pairLibraryError, setPairLibraryError] = useState<string | null>(null);
  const [leftSelection, setLeftSelection] = useState('');
  const [rightSelection, setRightSelection] = useState('');
  const [leftMetrics, setLeftMetrics] = useState<PairConfigMetrics | null>(null);
  const [rightMetrics, setRightMetrics] = useState<PairConfigMetrics | null>(null);
  const [isPairLoading, setIsPairLoading] = useState(false);
  const [pairError, setPairError] = useState<string | null>(null);
  const activeLibrary = manualLibrary ?? pairLibrary;
  const isPairLibraryLoading = !manualLibrary && !pairLibrary && !pairLibraryError;
  const comparableConfigs = useMemo(() => (
    [...(activeLibrary?.configurations ?? [])]
      .filter((configuration) => configuration.has_manual_schedule)
      .sort((first, second) => {
        const firstHours = numericMetric(first.model_max_sector_hours) ?? 0;
        const secondHours = numericMetric(second.model_max_sector_hours) ?? 0;
        return first.parsed_total - second.parsed_total || firstHours - secondHours || first.name.localeCompare(second.name);
      })
  ), [activeLibrary]);
  const comparisonOptions = useMemo(() => {
    const currentOption = result
      ? [{
        id: CURRENT_RESULT_COMPARISON_ID,
        label: `Trenutni izračun · ${result.planned_people} ljudi · ${result.max_sector_hours}/${result.requested_sector_hours} SH`,
      }]
      : [];
    const manualOptions = comparableConfigs.map((configuration) => ({
      id: configuration.id,
      label: `${configuration.name} · ${configuration.parsed_total} ljudi · ${configuration.model_max_sector_hours ?? '—'} SH`,
    }));
    return [...currentOption, ...manualOptions];
  }, [comparableConfigs, result]);
  const validComparisonIds = useMemo(
    () => new Set(comparisonOptions.map((option) => option.id)),
    [comparisonOptions],
  );
  const defaultLeftSelection = result
    ? CURRENT_RESULT_COMPARISON_ID
    : comparableConfigs[0]?.id ?? '';
  const defaultRightSelection = result
    ? comparableConfigs[0]?.id ?? ''
    : comparableConfigs[1]?.id ?? '';
  const resolvedLeftSelection = validComparisonIds.has(leftSelection)
    ? leftSelection
    : defaultLeftSelection;
  const resolvedRightSelection = validComparisonIds.has(rightSelection)
    ? rightSelection
    : defaultRightSelection;

  useEffect(() => {
    if (manualLibrary || pairLibrary) {
      return;
    }
    let cancelled = false;
    getManualConfigurations()
      .then((library) => {
        if (!cancelled) {
          setPairLibrary(library);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setPairLibraryError(caught instanceof Error ? caught.message : 'Baze konfiguracij ni bilo mogoče naložiti.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [manualLibrary, pairLibrary]);

  const compareSelectedPair = useCallback(async () => {
    if (!resolvedLeftSelection || !resolvedRightSelection) {
      setPairError('Izberi dve konfiguraciji.');
      return;
    }
    if (resolvedLeftSelection === resolvedRightSelection) {
      setPairError('Za primerjavo izberi dve različni konfiguraciji.');
      return;
    }
    setIsPairLoading(true);
    setPairError(null);
    try {
      const loadMetrics = async (selection: string): Promise<PairConfigMetrics> => {
        if (selection === CURRENT_RESULT_COMPARISON_ID) {
          if (!result) {
            throw new Error('Trenutni rezultat kalkulatorja ni več na voljo.');
          }
          return metricsFromCalculatorResult(result);
        }
        return metricsFromManualConfiguration(await getManualConfiguration(selection));
      };
      const [left, right] = await Promise.all([
        loadMetrics(resolvedLeftSelection),
        loadMetrics(resolvedRightSelection),
      ]);
      setLeftMetrics(left);
      setRightMetrics(right);
    } catch (caught) {
      setPairError(caught instanceof Error ? caught.message : 'Konfiguracij ni bilo mogoče naložiti.');
    } finally {
      setIsPairLoading(false);
    }
  }, [resolvedLeftSelection, resolvedRightSelection, result]);

  const useCurrentResultInPair = useCallback(() => {
    if (!result) {
      return;
    }
    setLeftSelection(CURRENT_RESULT_COMPARISON_ID);
    if (!resolvedRightSelection || resolvedRightSelection === CURRENT_RESULT_COMPARISON_ID) {
      setRightSelection(comparableConfigs[0]?.id ?? '');
    }
  }, [comparableConfigs, resolvedRightSelection, result]);

  const licenseCounts = comparison?.result.license_counts ?? { FL: 0, APS: 0, ACS: 0 };
  const roleHours = comparison?.result.role_hours ?? { V1: 0, V2: 0, V3: 0, FMP: 0 };

  return (
    <div className="comparison-page">
      <section className="panel comparison-hero">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Primerjevalnik konfiguracij</p>
            <h2>Primerjaj konfiguraciji</h2>
          </div>
          {result ? (
            <button className="secondary-button compact-button" onClick={useCurrentResultInPair} type="button">
              Uporabi zadnji izračun
            </button>
          ) : null}
        </div>
        <div className="pair-selector-grid">
          <label>
            <span>Leva konfiguracija</span>
            <select value={resolvedLeftSelection} onChange={(event) => setLeftSelection(event.target.value)}>
              <option value="">Izberi konfiguracijo</option>
              {comparisonOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Desna konfiguracija</span>
            <select value={resolvedRightSelection} onChange={(event) => setRightSelection(event.target.value)}>
              <option value="">Izberi konfiguracijo</option>
              {comparisonOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            className="primary-button compact-run-button"
            disabled={isPairLoading || comparisonOptions.length < 2}
            onClick={() => void compareSelectedPair()}
            type="button"
          >
            {isPairLoading ? 'Nalagam ...' : 'Daj v primerjevalnik'}
          </button>
        </div>
        {isPairLibraryLoading ? <div className="comparison-loading">Nalagam bazo konfiguracij ...</div> : null}
        {pairLibraryError ? <div className="error-box">{pairLibraryError}</div> : null}
        {pairError ? <div className="error-box">{pairError}</div> : null}
        {!isPairLibraryLoading && comparisonOptions.length < 2 ? (
          <div className="manual-audit-empty">Za primerjavo sta potrebni vsaj dve konfiguraciji z urnim prikazom ali zadnji rezultat kalkulatorja.</div>
        ) : null}
      </section>

      {leftMetrics && rightMetrics ? (
        <ManualConfigurationPairComparison left={leftMetrics} right={rightMetrics} />
      ) : (
        <section className="panel empty-state">
          <div className="empty-icon">⌁</div>
          <h2>Izberi dve konfiguraciji</h2>
          <p>Primerjava bo prikazala sestavo izmen, odprtost po urah, sektorski razpored in izkoriščenost ljudi.</p>
        </section>
      )}

      {result ? (
        <section className="panel comparison-hero">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Zadnji rezultat</p>
              <h2>Zadnji rezultat kalkulatorja proti bazi</h2>
            </div>
            {isLoading ? <span className="comparison-loading">Primerjam ...</span> : null}
          </div>
          <div className="comparison-summary-grid">
            <div>
              <span>SH</span>
              <strong>{result.max_sector_hours}/{result.requested_sector_hours}</strong>
            </div>
            <div>
              <span>Ljudje</span>
              <strong>{result.planned_people}</strong>
            </div>
            <div>
              <span>Licence</span>
              <strong>FL {licenseCounts.FL} · APS {licenseCounts.APS} · ACS {licenseCounts.ACS}</strong>
            </div>
            <div>
              <span>Posebne ure</span>
              <strong>V1 {roleHours.V1} · V2 {roleHours.V2} · V3 {roleHours.V3} · FMP {roleHours.FMP}</strong>
            </div>
          </div>
          {comparison?.duplicate_warning ? <div className="warning-box">{comparison.duplicate_warning}</div> : null}
          {error ? <div className="error-box">{error}</div> : null}
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Podobnost</p>
            <h2>Najbližje obstoječe konfiguracije zadnjemu rezultatu</h2>
          </div>
        </div>
        {result && comparison && comparison.matches.length > 0 ? (
          <div className="comparison-match-grid">
            {comparison.matches.map((match) => (
              <div className="comparison-match-card" key={match.id}>
                <div className="comparison-match-title">
                  <div>
                    <span>{formatComparisonSource(match.source_type, match.source_label)}</span>
                    <strong>{match.name}</strong>
                  </div>
                  <b>{match.similarity}%</b>
                </div>
                <div className="similarity-meter">
                  <div className="similarity-meter-track">
                    <div style={{ width: `${match.similarity}%` }} />
                  </div>
                  <strong>{match.similarity}%</strong>
                </div>
                <div className="comparison-match-metrics">
                  <span>SH {formatSignedValue(match.sh_diff)}</span>
                  <span>Ljudje {formatSignedValue(match.people_diff)}</span>
                  <span title={formatLicenseDiff(match.license_diff)}>Licence Δ{totalAbsLicenseDiff(match.license_diff)}</span>
                  <span title={formatRoleHoursDiff(match.role_hours_diff)}>Vloge Δ{totalAbsRoleDiff(match.role_hours_diff)}</span>
                  <span>Ure Δ{match.sector_profile_diff}</span>
                  <span>Delo Δ{match.workload_diff}</span>
                </div>
                <button className="secondary-button compact-button" onClick={() => onOpenMatch(match.id)} type="button">
                  Odpri ročno
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="manual-audit-empty">
            {result ? (isLoading ? 'Primerjava je v teku.' : 'Ni najdenih primerljivih konfiguracij.') : 'Za ta del najprej zaženi kalkulator.'}
          </div>
        )}
      </section>
    </div>
  );
}

function Results({
  result,
  paretoResult,
  shifts,
  officerShifts,
  isCalculationBusy,
  isCompletingConfiguration,
  isSavingUserConfiguration,
  configurationComparison,
  isConfigurationComparisonLoading,
  configurationComparisonError,
  targetDemand,
  targetDemandLabel,
  whatIfSummary,
  onRunShiftWhatIf,
  onImportResult,
  onEditResult,
  onCompleteConfiguration,
  onSaveUserConfiguration,
  onOptimizeLockedRoster,
  onOpenComparison,
}: {
  result: CalculatorResponse | null;
  paretoResult: ParetoResponse | null;
  shifts: ShiftRule[];
  officerShifts: ShiftRule[];
  isCalculationBusy: boolean;
  isCompletingConfiguration: boolean;
  isSavingUserConfiguration: boolean;
  configurationComparison: ConfigurationComparisonResult | null;
  isConfigurationComparisonLoading: boolean;
  configurationComparisonError: string | null;
  targetDemand: number[];
  targetDemandLabel: string | null;
  whatIfSummary: string | null;
  onRunShiftWhatIf: (changes: ShiftWhatIfChange[]) => void;
  onImportResult: (result: CalculatorResponse) => void;
  onEditResult: (updater: (current: CalculatorResponse) => CalculatorResponse) => void;
  onCompleteConfiguration: (result: CalculatorResponse) => Promise<void>;
  onSaveUserConfiguration: (result: CalculatorResponse) => Promise<void>;
  onOptimizeLockedRoster: (result: CalculatorResponse) => Promise<void>;
  onOpenComparison: () => void;
}) {
  const [whatIfShiftByPerson, setWhatIfShiftByPerson] = useState<Record<string, string>>({});
  const [isExportingExcel, setIsExportingExcel] = useState(false);
  const [excelExportError, setExcelExportError] = useState<string | null>(null);
  const paretoPoints = paretoResult?.points ?? result?.pareto_points ?? [];

  if (!result) {
    if (paretoPoints.length > 0) {
      return (
        <div className="results-stack">
          <ParetoAnalysis points={paretoPoints} />
          {[...(paretoResult?.warnings ?? []), ...(paretoResult?.notes ?? [])].length > 0 ? (
            <section className="panel note-list">
              <div className="panel-header compact">
                <div>
                  <p className="eyebrow">Diagnostika</p>
                  <h2>Opombe Pareto analize</h2>
                </div>
              </div>
              {(paretoResult?.warnings ?? []).map((warning) => (
                <div className="warning" key={warning}>⚠️ {warning}</div>
              ))}
              {(paretoResult?.notes ?? []).map((note) => (
                <div key={note}>ℹ️ {note}</div>
              ))}
            </section>
          ) : null}
        </div>
      );
    }
    return (
      <section className="panel empty-state">
        <div className="empty-icon">⌁</div>
        <h2>Vnesi podatke in zaženi kalkulator</h2>
        <p>Rezultat bo pokazal predlagano zasedbo, sestavo izmen in urni prikaz odprtosti.</p>
        <div className="panel-actions empty-actions">
          <ResultCsvImportButton onImport={onImportResult} />
        </div>
      </section>
    );
  }

  if (!result.feasible && result.people.length === 0) {
    return (
      <section className="panel error-state">
        <p className="eyebrow">Konfiguracija ni izvedljiva</p>
        <h2>Premalo obveznih FL vlog</h2>
        <p>Obvezne FL vloge: {result.minimum_required_fl}</p>
        {result.notes.map((note) => (
          <p key={note}>{note}</p>
        ))}
      </section>
    );
  }

  const targetBySlot = result.hourly_coverage.map((hour, index) => targetDemand[index] ?? hour.open_sectors);
  const maxCoverage = Math.max(
    ...result.hourly_coverage.map((hour, index) => Math.max(hour.open_sectors, targetBySlot[index] ?? 0)),
    1,
  );
  const shiftOptions = shifts.filter((shift) => shift.enabled !== false).map((shift) => shift.code);
  const officerShiftOptions = officerShifts.filter((shift) => shift.enabled !== false).map((shift) => shift.code);
  const scheduleShiftRules = [...shifts, ...officerShifts];
  const upperBoundLabel = result.solver_upper_bound_sector_hours == null
    ? '—'
    : result.solver_upper_bound_sector_hours;
  const upperBoundHelp = result.solver_gap_to_upper_bound == null
    ? 'Ni podatka o SH meji.'
    : result.solver_gap_to_upper_bound === 0
      ? 'SH meja je enaka najdeni pokritosti.'
      : `SH meja dopušča +${result.solver_gap_to_upper_bound} SH.`;
  const isPatternCoreResult = result.people.some((person) => person.source === 'pattern-core');
  const baselineLabel = isPatternCoreResult ? 'Dokazan minimum' : 'Grobo izhodišče';
  const baselineHelp = isPatternCoreResult
    ? result.baseline_min_people_formula ?? 'Pattern search je dokazal manjše limite kot neizvedljive.'
    : result.baseline_min_people_formula
      ? `${result.baseline_min_people_formula} Ni dokazni minimum.`
      : 'Ni dokazni minimum.';
  const proofStatus = isPatternCoreResult
    ? result.solver_gap_to_upper_bound === 0
      ? {
          className: 'proof-badge proven',
          label: 'Minimum dokazan',
          detail: 'Manjši limiti so dokazano neizvedljivi.',
        }
      : {
          className: 'proof-badge unknown',
          label: 'Minimum ni dokazan',
          detail: 'Rezultat je najboljša najdena izvedljiva rešitev.',
        }
    : {
        className: 'proof-badge neutral',
        label: 'Brez dokaznega minimuma',
        detail: 'Ta način ne dokazuje najmanjšega števila ljudi.',
      };
  const canRunPersonWhatIf = (person: VirtualPerson) => (
    ['regular', 'fixed', 'what-if', 'pattern-core'].includes(person.source) || isOfficeSource(person.source)
  );
  const selectedWhatIfChanges = selectedShiftWhatIfChanges(result.people, whatIfShiftByPerson)
    .filter(({ person }) => canRunPersonWhatIf(person));
  const applyManualResultEdit = (mutator: (current: CalculatorResponse) => CalculatorResponse) => {
    onEditResult((current) => recomputeEditedResult(mutator(current), scheduleShiftRules));
  };
  const exportExcel = async () => {
    const exportLabel = targetDemandLabel ?? `Konfiguracija ${result.max_sector_hours} SH`;
    setIsExportingExcel(true);
    setExcelExportError(null);
    try {
      const blob = await exportCalculatorWorkbook(
        paretoPoints === result.pareto_points
          ? result
          : { ...result, pareto_points: paretoPoints },
        exportLabel,
        targetBySlot,
        scheduleShiftRules,
      );
      const fileLabel = safeFilenamePart(exportLabel.replace(/^Ročna konfiguracija\s+/i, ''));
      downloadBlobFile(`atcconfmaker-${fileLabel || 'konfiguracija'}.xlsx`, blob);
    } catch (caught) {
      setExcelExportError(caught instanceof Error ? caught.message : 'Excel izvoza ni bilo mogoče pripraviti.');
    } finally {
      setIsExportingExcel(false);
    }
  };
  const changePersonLicense = (personId: string, license: License) => {
    applyManualResultEdit((current) => ({
      ...current,
      people: current.people.map((person) => (
        person.id === personId ? { ...person, license: leaderRoleDisplayId(person.role) ? 'FL' : license } : person
      )),
    }));
  };
  const changePersonShift = (personId: string, shift: string) => {
    applyManualResultEdit((current) => ({
      ...current,
      people: current.people.map((person) => (
        person.id === personId
          ? {
              ...person,
              shift,
              max_sector_hours: estimatedMaxSectorHoursForShift(shift, scheduleShiftRules),
            }
          : person
      )),
    }));
    setWhatIfShiftByPerson((current) => {
      const next = { ...current };
      delete next[personId];
      return next;
    });
  };
  const removePerson = (person: VirtualPerson) => {
    const label = personDisplayId(person);
    const confirmed = window.confirm(`Odstranim ${label} iz rezultata in iz vseh urnih sektorjev?`);
    if (!confirmed) {
      return;
    }
    applyManualResultEdit((current) => removePersonFromResult(current, person.id));
    setWhatIfShiftByPerson((current) => {
      const next = { ...current };
      delete next[person.id];
      return next;
    });
  };

  return (
    <div className="results-stack">
      <section className="panel result-actions-panel">
        <div>
          <p className="eyebrow">Izvoz</p>
          <h2>Kopiranje rezultatov</h2>
        </div>
        <div className={proofStatus.className}>
          <strong>{proofStatus.label}</strong>
          <span>{proofStatus.detail}</span>
        </div>
        <div className="panel-actions">
          <CopyButton label="Kopiraj vse" textFactory={() => formatAllResultForCopy(result)} />
          <CopyButton label="Kopiraj povzetek" textFactory={() => formatMetricsForCopy(result)} />
          <button
            className="secondary-button compact-button copy-button"
            onClick={() => downloadTextFile('atcconfmaker-result.csv', formatResultForCsv(result), 'text/csv;charset=utf-8')}
            type="button"
          >
            Shrani CSV
          </button>
          <button
            className="secondary-button compact-button copy-button"
            disabled={isExportingExcel}
            onClick={() => void exportExcel()}
            type="button"
          >
            {isExportingExcel ? 'Pripravljam Excel ...' : 'Izvozi Excel'}
          </button>
          <button
            className="secondary-button compact-button"
            disabled={isCalculationBusy || isSavingUserConfiguration}
            onClick={() => void onSaveUserConfiguration(result)}
            type="button"
          >
            {isSavingUserConfiguration
              ? 'Shranjujem ...'
              : result.missing_sector_hours > 0
                ? 'Shrani delni rezultat'
                : 'Shrani konfiguracijo'}
          </button>
          <span className="result-action-with-help">
            <button
              className="secondary-button compact-button"
              disabled={isCalculationBusy}
              onClick={() => void onOptimizeLockedRoster(result)}
              type="button"
            >
              Optimiziraj z zaklenjeno sestavo
            </button>
            <MetricInfo text="Zaklene iste osebe, licence, vloge in izmene. Solver lahko spremeni samo razpored po sektorjih in urah; izmen ne zamenja." />
          </span>
          {result.missing_sector_hours > 0 ? (
            <span className="result-action-with-help">
              <button
                className="secondary-button compact-button"
                disabled={isCalculationBusy || isCompletingConfiguration}
                onClick={() => void onCompleteConfiguration(result)}
                type="button"
              >
                {isCompletingConfiguration ? 'Dopolnjujem ...' : 'Dopolni do polne konfiguracije'}
              </button>
              <MetricInfo text="Do 180 sekund išče polno pokritost z enakim številom ljudi. Lahko spremeni izmene in razmerje licenc ter preizkusi višje sektorske omejitve VI/FMP." />
            </span>
          ) : null}
          <ResultCsvImportButton onImport={onImportResult} />
        </div>
        {excelExportError ? <div className="error-box">{excelExportError}</div> : null}
      </section>

      {whatIfSummary ? (
        <section className="panel what-if-banner">
          <p className="eyebrow">What-if</p>
          <h2>{whatIfSummary}</h2>
        </section>
      ) : null}

      <ConfigurationSimilaritySummary
        comparison={configurationComparison}
        isLoading={isConfigurationComparisonLoading}
        error={configurationComparisonError}
        onOpenComparison={onOpenComparison}
      />

      <section className="metrics-grid">
        <div className="metric-card accent">
          <span className="metric-label">
            Najdeno max SH <MetricInfo text="Dejansko sestavljena rešitev." />
          </span>
          <strong>{result.max_sector_hours}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">
            Željene sektorske ure <MetricInfo text="Cilj iz urne odprtosti." />
          </span>
          <strong>{result.requested_sector_hours}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Splaniranih ljudi</span>
          <strong>{result.planned_people}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Aktivnih na sektorju</span>
          <strong>{result.active_people}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Manjkajoče ure</span>
          <strong>{result.missing_sector_hours}</strong>
        </div>
        <div className={`metric-card${result.crisis_exception_hours > 0 ? ' warning-metric' : ''}`}>
          <span className="metric-label">
            Krizne VI/FMP ure <MetricInfo text={`VI robne ure ${result.leader_edge_exception_hours}; VI/FMP prekrivanje ${result.fmp_vi_overlap_hours}.`} />
          </span>
          <strong>{result.crisis_exception_hours}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Neuporabljeni ljudje</span>
          <strong>{result.unused_people}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Izkoriščenost ljudi</span>
          <strong>{result.utilization_percent}%</strong>
        </div>
        <div className="metric-card compact-metric">
          <span className="metric-label">
            Kontrolorske ure <MetricInfo text="Opravljene / možne kontrolorske ure." />
          </span>
          <strong>{result.scheduled_person_hours}/{result.total_person_capacity_hours}</strong>
        </div>
        <div className="metric-card compact-metric">
          <span className="metric-label">
            {baselineLabel} <MetricInfo text={baselineHelp} />
          </span>
          <strong>{result.baseline_min_people || '—'}</strong>
        </div>
        <div className="metric-card compact-metric">
          <span className="metric-label">
            CP-SAT SH meja <MetricInfo text={upperBoundHelp} />
          </span>
          <strong>{upperBoundLabel}</strong>
        </div>
      </section>

      <ParetoAnalysis points={paretoPoints} currentMaxSectorHours={result.max_sector_hours} />

      {[...result.warnings, ...result.notes].length > 0 ? (
        <section className="panel note-list">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Diagnostika</p>
              <h2>Opombe</h2>
            </div>
            <div className="panel-actions">
              <CopyButton textFactory={() => formatNotesForCopy(result)} />
            </div>
          </div>
          {result.warnings.map((warning) => (
            <div className="warning" key={warning}>⚠️ {warning}</div>
          ))}
          {result.notes.map((note) => (
            <div key={note}>ℹ️ {note}</div>
          ))}
        </section>
      ) : null}

      <div className="result-overview-pair">
        <section className="panel result-compact-panel result-shift-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Generator</p>
              <h2>Predlagana sestava izmen</h2>
            </div>
            <div className="panel-actions">
              <CopyButton textFactory={() => formatShiftSummaryForCopy(result)} />
            </div>
          </div>
          <div className="responsive-table compact-result-table">
            <table>
              <thead>
                <tr>
                  <th>Izmena / vloga</th>
                  <th>FL</th>
                  <th>APS</th>
                  <th>ACS</th>
                  <th>Skupaj</th>
                </tr>
              </thead>
              <tbody>
                {result.shift_summary.map((row) => (
                  <tr key={row.shift}>
                    <td className="strong">{shiftSummaryLabel(row.shift)}</td>
                    <td>{row.fl}</td>
                    <td>{row.aps}</td>
                    <td>{row.acs}</td>
                    <td>{row.total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel result-compact-panel result-coverage-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Odprtost po urah</p>
              <h2>Želja proti realnosti</h2>
              {targetDemandLabel ? <small className="coverage-subtitle">Želja: {targetDemandLabel}</small> : null}
            </div>
            <div className="panel-actions">
              <CopyButton textFactory={() => formatCoverageForCopy(result, targetBySlot)} />
            </div>
          </div>
          <div className="coverage-list">
            {result.hourly_coverage.map((hour, index) => {
              const target = targetBySlot[index] ?? hour.open_sectors;
              const diff = hour.open_sectors - target;
              const targetWidth = (target / maxCoverage) * 100;
              const actualWidth = (hour.open_sectors / maxCoverage) * 100;
              return (
              <div
                className={`coverage-row ${diff < 0 ? 'coverage-shortfall' : diff > 0 ? 'coverage-surplus' : 'coverage-hit'}`}
                key={hour.hour}
                title={`Želja ${target}, realnost ${hour.open_sectors}`}
              >
                <span className="coverage-hour">{hour.hour}</span>
                <div className="coverage-bar-track">
                  <div className="coverage-target-bar" style={{ width: `${targetWidth}%` }} />
                  <div className="coverage-bar" style={{ width: `${actualWidth}%` }} />
                </div>
                <strong className="coverage-value">
                  {hour.open_sectors}/{target}
                  {diff !== 0 ? <small>{diff > 0 ? `+${diff}` : diff}</small> : null}
                </strong>
              </div>
              );
            })}
          </div>
        </section>
      </div>

      <SectorSchedule
        result={result}
        shifts={scheduleShiftRules}
        onEditResult={onEditResult}
      />

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Navidezni ljudje</p>
            <h2>Izkoriščenost ljudi</h2>
          </div>
          <div className="panel-actions">
            <CopyButton textFactory={() => formatPeopleForCopy(result)} />
          </div>
        </div>
        <div className="what-if-batch-actions">
          <div>
            <strong>Skupni what-if</strong>
            <span>
              V stolpcu What-if izberi nove izmene pri eni ali več osebah, nato zaženi en nov 600-sekundni izračun.
              Izbrane spremembe so obvezne; preostala trenutna konfiguracija je samo mehki start.
            </span>
          </div>
          <button
            className="secondary-button compact-button"
            disabled={isCalculationBusy || selectedWhatIfChanges.length === 0}
            onClick={() => {
              onRunShiftWhatIf(selectedWhatIfChanges);
              setWhatIfShiftByPerson({});
            }}
            type="button"
          >
            Zaženi {selectedWhatIfChanges.length || ''} what-if
          </button>
          <button
            className="secondary-button compact-button"
            disabled={isCalculationBusy || selectedWhatIfChanges.length === 0}
            onClick={() => setWhatIfShiftByPerson({})}
            type="button"
          >
            Počisti izbor
          </button>
        </div>
        <div className="responsive-table">
          <table className="people-table">
            <thead>
              <tr>
                <th>Oseba</th>
                <th>Vloga</th>
                <th>Vir</th>
                <th>Izmena</th>
                <th>Licenca</th>
                <th>Sektorske ure</th>
                <th>Izkoriščenost</th>
                <th>What-if</th>
                <th aria-label="Odstrani" />
              </tr>
            </thead>
            <tbody>
              {result.people.map((person) => {
                const availablePersonShifts = isOfficeSource(person.source) ? officerShiftOptions : shiftOptions;
                const currentShiftOptions = availablePersonShifts.includes(person.shift)
                  ? availablePersonShifts
                  : [person.shift, ...availablePersonShifts];
                const selectedWhatIfShift = whatIfShiftByPerson[person.id] ?? person.shift;
                const canRunWhatIf = canRunPersonWhatIf(person);
                return (
                  <tr key={person.id}>
                    <td className="strong">{personDisplayId(person)}</td>
                    <td>{person.role ?? '—'}</td>
                    <td>{personSourceLabel(person.source)}</td>
                    <td>
                      <select
                        className="shift-edit-select"
                        value={person.shift}
                        disabled={isCalculationBusy}
                        onChange={(event) => changePersonShift(person.id, event.target.value)}
                        aria-label={`Izmena za ${personDisplayId(person)}`}
                        title="Spremeni dejansko izmeno v rezultatu. Razpored zgoraj se takoj ponovno validira."
                      >
                        {currentShiftOptions.map((shiftCode) => (
                          <option value={shiftCode} key={shiftCode}>
                            {shiftCode}{shiftCode === person.shift && !availablePersonShifts.includes(shiftCode) ? ' (izklopljena)' : ''}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        className="license-edit-select"
                        value={person.license}
                        disabled={isCalculationBusy || Boolean(leaderRoleDisplayId(person.role))}
                        onChange={(event) => changePersonLicense(person.id, event.target.value as License)}
                        aria-label={`Licenca za ${personDisplayId(person)}`}
                        title={leaderRoleDisplayId(person.role) ? 'Vodje izmen Vi1, Vi2 in Vi3 so vedno FL.' : undefined}
                      >
                        <option value="FL">FL</option>
                        <option value="APS">APS</option>
                        <option value="ACS">ACS</option>
                      </select>
                    </td>
                    <td>{person.sector_hours}/{person.max_sector_hours}</td>
                    <td>
                      <div className="utilization-cell">
                        <div className="utilization-track">
                          <div className="utilization-fill" style={{ width: `${person.utilization_percent}%` }} />
                        </div>
                        <strong>{person.utilization_percent}%</strong>
                      </div>
                    </td>
                    <td>
                      {canRunWhatIf ? (
                        <div className="what-if-cell">
                          <select
                            value={selectedWhatIfShift}
                            disabled={isCalculationBusy}
                            onChange={(event) => setWhatIfShiftByPerson((current) => {
                              const next = { ...current };
                              if (event.target.value === person.shift) {
                                delete next[person.id];
                              } else {
                                next[person.id] = event.target.value;
                              }
                              return next;
                            })}
                            aria-label={`What-if izmena za ${person.id}`}
                          >
                            {currentShiftOptions.map((shiftCode) => (
                              <option value={shiftCode} key={shiftCode}>
                                {shiftCode}{shiftCode === person.shift && !availablePersonShifts.includes(shiftCode) ? ' (izklopljena)' : ''}
                              </option>
                            ))}
                          </select>
                          {selectedWhatIfShift !== person.shift ? (
                            <span className="what-if-selected">Izbrano</span>
                          ) : null}
                        </div>
                      ) : (
                        <span className="muted-cell">—</span>
                      )}
                    </td>
                    <td className="person-remove-cell">
                      <button
                        aria-label={`Odstrani ${personDisplayId(person)}`}
                        className="trash-button person-trash-button"
                        disabled={isCalculationBusy}
                        onClick={() => removePerson(person)}
                        title={`Odstrani ${personDisplayId(person)}`}
                        type="button"
                      >
                        <TrashIcon />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('calculator');
  const [isOnboardingOpen, setIsOnboardingOpen] = useState(shouldShowOnboarding);
  const [guidedTourKind, setGuidedTourKind] = useState<GuidedTourKind | null>(null);
  const closeGuidedTour = useCallback(() => setGuidedTourKind(null), []);
  const [settings, setSettings] = useState<CalculatorSettings>(fallbackSettings);
  const [defaultSettings, setDefaultSettings] = useState<CalculatorSettings>(fallbackSettings);
  const [calculationMode, setCalculationMode] = useState<CalculationMode>('demand_to_staff');
  const [totalPeople, setTotalPeople] = useState(28);
  const [flCount, setFlCount] = useState(12);
  const [apsCount, setApsCount] = useState(0);
  const [preferMinimalFl, setPreferMinimalFl] = useState(false);
  const [usePeopleLimit, setUsePeopleLimit] = useState(false);
  const [minimumLicenseRatio, setMinimumLicenseRatio] = useState({ fl: 50, aps: 0, acs: 50 });
  const [sectorDemand, setSectorDemand] = useState(() => createDefaultSectorDemand(fallbackSettings.max_sectors_per_hour));
  const [staffSectorLimits, setStaffSectorLimits] = useState(
    createUnlimitedSectorLimits,
  );
  const [sectorDemandQueue, setSectorDemandQueue] = useState<SectorDemandQueueItem[]>([]);
  const [activeDemandLabel, setActiveDemandLabel] = useState<string | null>(null);
  const [baseSectors, setBaseSectors] = useState(3);
  const [sectorIntervals, setSectorIntervals] = useState<SectorDemandInterval[]>(createDefaultDemandIntervals);
  const [fixedStaff, setFixedStaff] = useState<FixedStaffRow[]>([]);
  const [officerStaff, setOfficerStaff] = useState<OfficerStaffRow[]>(() => createOfficerRows(fallbackSettings.officer_shifts));
  const [officePool, setOfficePool] = useState<OfficePool>({ fl: 0, aps: 0, acs: 0 });
  const [includeFmp, setIncludeFmp] = useState(true);
  const [fmpShiftMode, setFmpShiftMode] = useState<FmpShiftMode>('auto');
  const [fmpShift, setFmpShift] = useState(DEFAULT_FMP_SHIFT);
  const [result, setResult] = useState<CalculatorResponse | null>(null);
  const [paretoResult, setParetoResult] = useState<ParetoResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isParetoLoading, setIsParetoLoading] = useState(false);
  const [calculationProgress, setCalculationProgress] = useState(0);
  const [paretoProgress, setParetoProgress] = useState(0);
  const [jobStatus, setJobStatus] = useState<CalculationJobStatus | null>(null);
  const [cancelAction, setCancelAction] = useState<CalculationCancelAction>(null);
  const [calculationNotice, setCalculationNotice] = useState<string | null>(null);
  const [paretoJobStatus, setParetoJobStatus] = useState<CalculationJobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paretoError, setParetoError] = useState<string | null>(null);
  const [patternProfile, setPatternProfile] = useState<PatternLibraryProfile | null>(null);
  const [patternProfileError, setPatternProfileError] = useState<string | null>(null);
  const [isPatternProfileLoading, setIsPatternProfileLoading] = useState(false);
  const [manualLibrary, setManualLibrary] = useState<ManualConfigurationLibrary | null>(null);
  const [manualDetail, setManualDetail] = useState<ManualConfigurationDetail | null>(null);
  const [selectedManualConfigId, setSelectedManualConfigId] = useState<string | null>(null);
  const [manualResultConfigId, setManualResultConfigId] = useState<string | null>(null);
  const [manualSeedConfigId, setManualSeedConfigId] = useState<string | null>(null);
  const [manualLibraryError, setManualLibraryError] = useState<string | null>(null);
  const [manualExcelExportError, setManualExcelExportError] = useState<string | null>(null);
  const [exportingManualExcelConfigId, setExportingManualExcelConfigId] = useState<string | null>(null);
  const [isManualLibraryLoading, setIsManualLibraryLoading] = useState(false);
  const [isSavingUserConfiguration, setIsSavingUserConfiguration] = useState(false);
  const [pendingSaveResult, setPendingSaveResult] = useState<CalculatorResponse | null>(null);
  const [saveConfigurationName, setSaveConfigurationName] = useState('');
  const [isCompletingConfiguration, setIsCompletingConfiguration] = useState(false);
  const [configurationComparison, setConfigurationComparison] = useState<ConfigurationComparisonResult | null>(null);
  const [configurationComparisonError, setConfigurationComparisonError] = useState<string | null>(null);
  const [isConfigurationComparisonLoading, setIsConfigurationComparisonLoading] = useState(false);
  const [deletingManualConfigId, setDeletingManualConfigId] = useState<string | null>(null);
  const [updatingManualConfigId, setUpdatingManualConfigId] = useState<string | null>(null);
  const [oneDownConfigId, setOneDownConfigId] = useState<string | null>(null);
  const [settingsSaveState, setSettingsSaveState] = useState<'idle' | 'saved' | 'failed'>('idle');
  const [calculatorInputsSaveState, setCalculatorInputsSaveState] = useState<'idle' | 'saved' | 'failed'>('idle');
  const [whatIfSummary, setWhatIfSummary] = useState<string | null>(null);
  const [pausedPayload, setPausedPayload] = useState<CalculatorRequest | null>(null);
  const [timeLimitDecision, setTimeLimitDecision] = useState<TimeLimitDecision | null>(null);
  const [continuationComparison, setContinuationComparison] = useState<ContinuationComparison | null>(null);
  const pollingTimerRef = useRef<PollingTimer | null>(null);
  const paretoPollingTimerRef = useRef<PollingTimer | null>(null);
  const activeJobIdRef = useRef<string | null>(null);
  const activeParetoJobIdRef = useRef<string | null>(null);
  const pollingRequestInFlightRef = useRef(false);
  const paretoPollingRequestInFlightRef = useRef(false);
  const bestResultVersionRef = useRef(0);
  const paretoResultVersionRef = useRef(0);
  const lastCalculationPayloadRef = useRef<CalculatorRequest | null>(null);
  const lastRestartPlanRef = useRef<JobRestartPlan | null>(null);
  const showDecisionAfterCancelRef = useRef(false);

  useEffect(() => {
    getDefaultSettings()
      .then((defaultSettings) => {
        const normalizedDefaults = normalizeSettings({ ...fallbackSettings, ...defaultSettings });
        setDefaultSettings(normalizedDefaults);
        const loadedSettings = loadSavedCalculatorSettings(normalizedDefaults);
        const savedInputs = loadSavedCalculatorInputs(loadedSettings);
        setSettings(loadedSettings);
        if (savedInputs) {
          setCalculationMode(savedInputs.calculationMode ?? 'demand_to_staff');
          setTotalPeople(savedInputs.totalPeople ?? 28);
          setFlCount(savedInputs.flCount ?? 12);
          setApsCount(savedInputs.apsCount ?? 0);
          setPreferMinimalFl(savedInputs.preferMinimalFl === true);
          setUsePeopleLimit(savedInputs.usePeopleLimit === true);
          setMinimumLicenseRatio(savedInputs.minimumLicenseRatio ?? { fl: 50, aps: 0, acs: 50 });
          setSectorDemand(savedInputs.sectorDemand ?? createDefaultSectorDemand(loadedSettings.max_sectors_per_hour));
          setStaffSectorLimits(
            savedInputs.staffSectorLimits ?? createUnlimitedSectorLimits(),
          );
          setBaseSectors(savedInputs.baseSectors ?? 3);
          setSectorIntervals(savedInputs.sectorIntervals ?? createDefaultDemandIntervals());
          setFixedStaff(savedInputs.fixedStaff ?? []);
          setOfficerStaff(savedInputs.officerStaff ?? createOfficerRows(loadedSettings.officer_shifts));
          setOfficePool(savedInputs.officePool ?? { fl: 0, aps: 0, acs: 0 });
          setIncludeFmp(savedInputs.includeFmp !== false);
          setFmpShiftMode(savedInputs.fmpShiftMode ?? 'auto');
          setFmpShift(savedInputs.fmpShift ?? DEFAULT_FMP_SHIFT);
        } else {
          setOfficerStaff((rows) => mergeOfficerRows(rows, loadedSettings.officer_shifts));
        }
      })
      .catch(() => {
        // Fallback keeps the app usable while the backend is not running.
        setSettings(fallbackSettings);
      });
  }, []);

  const stopPolling = useCallback(() => {
    if (pollingTimerRef.current !== null) {
      window.clearInterval(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
  }, []);

  const stopParetoPolling = useCallback(() => {
    if (paretoPollingTimerRef.current !== null) {
      window.clearInterval(paretoPollingTimerRef.current);
      paretoPollingTimerRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);
  useEffect(() => stopParetoPolling, [stopParetoPolling]);

  useEffect(() => {
    if (!result) {
      return;
    }

    let cancelled = false;
    void Promise.resolve().then(async () => {
      if (cancelled) {
        return;
      }
      setIsConfigurationComparisonLoading(true);
      setConfigurationComparisonError(null);
      try {
        const comparison = await compareResultToConfigurations(result, 8);
        if (!cancelled) {
          setConfigurationComparison(comparison);
        }
      } catch (caught) {
        if (!cancelled) {
          setConfigurationComparisonError(
            caught instanceof Error ? caught.message : 'Napaka pri primerjavi konfiguracij.',
          );
        }
      } finally {
        if (!cancelled) {
          setIsConfigurationComparisonLoading(false);
        }
      }
    });

    return () => {
      cancelled = true;
    };
  }, [result]);

  const loadJobResult = useCallback(async (jobId: string, resultVersion: number): Promise<CalculatorResponse | null> => {
    const response = await getCalculationJobResult(jobId);
    if (activeJobIdRef.current !== jobId) {
      return null;
    }
    setResult(response);
    bestResultVersionRef.current = Math.max(bestResultVersionRef.current, resultVersion);
    return response;
  }, []);

  const loadParetoJobResult = useCallback(async (jobId: string, resultVersion: number) => {
    const response = await getParetoJobResult(jobId);
    if (activeParetoJobIdRef.current !== jobId) {
      return;
    }
    setParetoResult(response);
    paretoResultVersionRef.current = Math.max(paretoResultVersionRef.current, resultVersion);
  }, []);

  const effectiveSectorDemand = useMemo(
    () => calculationMode === 'staff_to_coverage'
      ? resolveStaffSectorLimits(staffSectorLimits, settings.max_sectors_per_hour)
      : clampSectorDemand(sectorDemand, settings.max_sectors_per_hour),
    [calculationMode, sectorDemand, settings.max_sectors_per_hour, staffSectorLimits],
  );

  const acsCount = useMemo(() => Math.max(0, totalPeople - flCount - apsCount), [apsCount, flCount, totalPeople]);
  const fmpShiftOptions = useMemo(() => {
    const candidates = settings.shifts
      .filter((shift) => shift.enabled !== false && !FMP_BLOCKED_SHIFT_CODES.has(shift.code))
      .map((shift) => shift.code);
    if (
      fmpShift
      && !FMP_BLOCKED_SHIFT_CODES.has(fmpShift)
      && !candidates.includes(fmpShift)
      && settings.shifts.some((shift) => shift.code === fmpShift)
    ) {
      candidates.push(fmpShift);
    }
    return candidates.length > 0 ? candidates : [DEFAULT_FMP_SHIFT];
  }, [fmpShift, settings.shifts]);
  const selectedFmpShift = fmpShiftOptions.includes(fmpShift) ? fmpShift : DEFAULT_FMP_SHIFT;
  const minimumLicenseRatioTotal = minimumLicenseRatio.fl + minimumLicenseRatio.aps + minimumLicenseRatio.acs;

  const updateCounts = (nextTotal: number, nextFl: number, nextAps: number) => {
    const safeTotal = clamp(nextTotal, 1, 80);
    const safeFl = clamp(nextFl, 0, safeTotal);
    const safeAps = clamp(nextAps, 0, safeTotal - safeFl);
    setTotalPeople(safeTotal);
    setFlCount(safeFl);
    setApsCount(safeAps);
  };

  const updateMinimumLicenseRatio = (nextRatio: Partial<typeof minimumLicenseRatio>) => {
    setMinimumLicenseRatio((currentRatio) => ({
      fl: clamp(nextRatio.fl ?? currentRatio.fl, 0, 100),
      aps: clamp(nextRatio.aps ?? currentRatio.aps, 0, 100),
      acs: clamp(nextRatio.acs ?? currentRatio.acs, 0, 100),
    }));
  };

  const saveSettings = () => {
    try {
      window.localStorage.setItem(savedSettingsStorageKey, JSON.stringify(settings));
      setSettingsSaveState('saved');
      window.setTimeout(() => setSettingsSaveState('idle'), 1800);
    } catch {
      setSettingsSaveState('failed');
      window.setTimeout(() => setSettingsSaveState('idle'), 2200);
    }
  };

  const resetSettings = () => {
    setSettings(defaultSettings);
    setOfficerStaff(createOfficerRows(defaultSettings.officer_shifts));
    try {
      window.localStorage.removeItem(savedSettingsStorageKey);
      setSettingsSaveState('saved');
      window.setTimeout(() => setSettingsSaveState('idle'), 1800);
    } catch {
      setSettingsSaveState('failed');
      window.setTimeout(() => setSettingsSaveState('idle'), 2200);
    }
  };

  const saveCalculatorInputsAsDefault = () => {
    try {
      const payload: SavedCalculatorInputs = {
        calculationMode,
        totalPeople,
        flCount,
        apsCount,
        preferMinimalFl,
        usePeopleLimit,
        minimumLicenseRatio,
        sectorDemand,
        staffSectorLimits,
        baseSectors,
        sectorIntervals,
        fixedStaff,
        officerStaff: mergeOfficerRows(officerStaff, settings.officer_shifts),
        officePool,
        includeFmp,
        fmpShiftMode,
        fmpShift: selectedFmpShift,
      };
      window.localStorage.setItem(savedCalculatorInputsStorageKey, JSON.stringify(payload));
      window.localStorage.setItem(savedSettingsStorageKey, JSON.stringify(settings));
      setCalculatorInputsSaveState('saved');
      window.setTimeout(() => setCalculatorInputsSaveState('idle'), 1800);
    } catch {
      setCalculatorInputsSaveState('failed');
      window.setTimeout(() => setCalculatorInputsSaveState('idle'), 2200);
    }
  };

  const clearCalculatorResults = () => {
    setResult(null);
    setParetoResult(null);
    setWhatIfSummary(null);
    setPausedPayload(null);
    setTimeLimitDecision(null);
    setContinuationComparison(null);
    setManualResultConfigId(null);
    setCalculationNotice(null);
    setConfigurationComparison(null);
    setConfigurationComparisonError(null);
    setIsConfigurationComparisonLoading(false);
  };

  const applySectorDemand = (values: number[], label?: string) => {
    setSectorDemand(clampSectorDemand(values, settings.max_sectors_per_hour));
    setCalculationMode('demand_to_staff');
    setUsePeopleLimit(false);
    clearCalculatorResults();
    setActiveTab('calculator');
    setActiveDemandLabel(label ?? null);
    setManualSeedConfigId(null);
  };

  const enqueueSectorDemands = (items: Array<{ label: string; values: number[] }>) => {
    setSectorDemandQueue((currentQueue) => {
      const additions = items.map((item, index) => {
        const values = clampSectorDemand(item.values, settings.max_sectors_per_hour);
        return {
          id: Date.now() + index + Math.floor(Math.random() * 100_000),
          label: item.label,
          values,
        };
      });
      return [...currentQueue, ...additions].slice(0, 240);
    });
    setActiveTab('calculator');
  };

  const loadQueuedSectorDemand = (item: SectorDemandQueueItem) => {
    applySectorDemand(item.values, item.label);
    setSectorDemandQueue((queue) => queue.filter((queuedItem) => queuedItem.id !== item.id));
  };

  const pollJobStatus = useCallback(async (jobId: string) => {
    if (pollingRequestInFlightRef.current) {
      return;
    }

    pollingRequestInFlightRef.current = true;
    try {
      const status = await getCalculationJob(jobId);
      if (activeJobIdRef.current !== jobId) {
        return;
      }

      setJobStatus(status);
      setCalculationProgress(status.progress);

      if (status.status === 'finished') {
        stopPolling();
        setCancelAction(null);
        const currentResult = await loadJobResult(jobId, status.best_result_version);
        setCalculationProgress(100);
        setIsLoading(false);
        if (showDecisionAfterCancelRef.current && lastRestartPlanRef.current && status.best_result_available) {
          setTimeLimitDecision({
            status,
            restartPlan: lastRestartPlanRef.current,
            currentResult,
            reason: 'manual-stop',
          });
        } else if (statusNeedsTimeLimitDecision(status, lastRestartPlanRef.current)) {
          setTimeLimitDecision({
            status,
            restartPlan: lastRestartPlanRef.current as JobRestartPlan,
            currentResult,
            reason: 'time-limit',
          });
        } else if (status.cancel_requested) {
          setCalculationNotice('Izračun je prekinjen. Prikazana je zadnja najdena rešitev.');
        }
        showDecisionAfterCancelRef.current = false;
        activeJobIdRef.current = null;
        return;
      }

      if (status.status === 'failed') {
        stopPolling();
        setCancelAction(null);
        if (status.best_result_available) {
          await loadJobResult(jobId, status.best_result_version);
        }
        if (status.cancel_requested) {
          setError(null);
          setCalculationNotice(
            status.best_result_available
              ? 'Izračun je prekinjen. Prikazana je zadnja najdena rešitev.'
              : 'Izračun je prekinjen pred prvo najdeno rešitvijo.',
          );
        } else {
          setError(status.error ?? status.message ?? 'Izračun se je ustavil.');
        }
        setIsLoading(false);
        setTimeLimitDecision(null);
        showDecisionAfterCancelRef.current = false;
        activeJobIdRef.current = null;
        return;
      }

      if (status.best_result_available && status.best_result_version > bestResultVersionRef.current) {
        await loadJobResult(jobId, status.best_result_version);
      }
    } catch (caught) {
      if (activeJobIdRef.current === jobId) {
        stopPolling();
        setCancelAction(null);
        setError(caught instanceof Error ? caught.message : 'Neznana napaka pri preverjanju statusa.');
        setIsLoading(false);
        setTimeLimitDecision(null);
        showDecisionAfterCancelRef.current = false;
        activeJobIdRef.current = null;
      }
    } finally {
      pollingRequestInFlightRef.current = false;
    }
  }, [loadJobResult, stopPolling]);

  const pollParetoJobStatus = useCallback(async (jobId: string) => {
    if (paretoPollingRequestInFlightRef.current) {
      return;
    }

    paretoPollingRequestInFlightRef.current = true;
    try {
      const status = await getCalculationJob(jobId);
      if (activeParetoJobIdRef.current !== jobId) {
        return;
      }

      setParetoJobStatus(status);
      setParetoProgress(status.progress);

      if (status.status === 'finished') {
        stopParetoPolling();
        await loadParetoJobResult(jobId, status.best_result_version);
        setParetoProgress(100);
        setIsParetoLoading(false);
        activeParetoJobIdRef.current = null;
        return;
      }

      if (status.status === 'failed') {
        stopParetoPolling();
        if (status.best_result_available) {
          await loadParetoJobResult(jobId, status.best_result_version);
        }
        setParetoError(status.error ?? status.message ?? 'Pareto analiza se je ustavila.');
        setIsParetoLoading(false);
        activeParetoJobIdRef.current = null;
        return;
      }

      if (status.best_result_available && status.best_result_version > paretoResultVersionRef.current) {
        await loadParetoJobResult(jobId, status.best_result_version);
      }
    } catch (caught) {
      if (activeParetoJobIdRef.current === jobId) {
        stopParetoPolling();
        setParetoError(caught instanceof Error ? caught.message : 'Neznana napaka pri Pareto analizi.');
        setIsParetoLoading(false);
        activeParetoJobIdRef.current = null;
      }
    } finally {
      paretoPollingRequestInFlightRef.current = false;
    }
  }, [loadParetoJobResult, stopParetoPolling]);

  useEffect(() => {
    let cancelled = false;

    getCalculationJobs()
      .then((jobs) => {
        if (cancelled) {
          return;
        }
        const activeCalculatorJob = jobs.find(
          (job) => job.kind !== 'pareto' && (job.status === 'running' || job.status === 'queued'),
        );
        if (activeCalculatorJob && !activeJobIdRef.current) {
          activeJobIdRef.current = activeCalculatorJob.job_id;
          setIsLoading(true);
          setJobStatus(activeCalculatorJob);
          setCalculationProgress(activeCalculatorJob.progress);
          bestResultVersionRef.current = Math.max(bestResultVersionRef.current, activeCalculatorJob.best_result_version);
          if (pollingTimerRef.current === null) {
            pollingTimerRef.current = window.setInterval(() => {
              void pollJobStatus(activeCalculatorJob.job_id);
            }, 2000);
          }
          void pollJobStatus(activeCalculatorJob.job_id);
        }

        const activeParetoJob = jobs.find(
          (job) => job.kind === 'pareto' && (job.status === 'running' || job.status === 'queued'),
        );
        if (activeParetoJob && !activeParetoJobIdRef.current) {
          activeParetoJobIdRef.current = activeParetoJob.job_id;
          setIsParetoLoading(true);
          setParetoJobStatus(activeParetoJob);
          setParetoProgress(activeParetoJob.progress);
          paretoResultVersionRef.current = Math.max(paretoResultVersionRef.current, activeParetoJob.best_result_version);
          if (paretoPollingTimerRef.current === null) {
            paretoPollingTimerRef.current = window.setInterval(() => {
              void pollParetoJobStatus(activeParetoJob.job_id);
            }, 2000);
          }
          void pollParetoJobStatus(activeParetoJob.job_id);
        }
      })
      .catch(() => {
        // If the local engine is still starting, the normal explicit calculation flow remains available.
      });

    return () => {
      cancelled = true;
    };
  }, [pollJobStatus, pollParetoJobStatus]);

  const buildRequestPayload = useCallback((): CalculatorRequest => {
    const activeRegularShiftCodes = activeShiftCodes(settings.shifts);
    const activeOfficerShiftCodes = activeShiftCodes(settings.officer_shifts);
    const fixedStaffPayload = fixedStaff.map((row) => ({
      count: clamp(row.count, 1, 80),
      license: row.license,
      shift: row.shift,
      role: row.role.trim() || null,
    })).filter((row) => (
      activeRegularShiftCodes.has(row.shift)
      && (includeFmp || (row.role ?? '').toUpperCase() !== 'FMP')
    ));
    const officerStaffPayload: OfficerStaffRule[] = mergeOfficerRows(officerStaff, settings.officer_shifts).flatMap((row) => [
      { count: clamp(row.fl, 0, 80), license: 'FL', shift: row.shift },
      { count: clamp(row.aps, 0, 80), license: 'APS', shift: row.shift },
      { count: clamp(row.acs, 0, 80), license: 'ACS', shift: row.shift },
    ]).filter((row) => row.count > 0 && activeOfficerShiftCodes.has(row.shift)) as OfficerStaffRule[];
    const officePoolPayload: OfficePoolRule[] = [
      { count: clamp(officePool.fl, 0, 80), license: 'FL' },
      { count: clamp(officePool.aps, 0, 80), license: 'APS' },
      { count: clamp(officePool.acs, 0, 80), license: 'ACS' },
    ].filter((row) => row.count > 0) as OfficePoolRule[];
    const isDemandMode = calculationMode === 'demand_to_staff';
    const payload: CalculatorRequest = {
      calculation_mode: calculationMode === 'staff_to_coverage' ? 'staff_to_coverage' : 'demand_to_staff',
      total_people: isDemandMode ? (usePeopleLimit ? totalPeople : 0) : totalPeople,
      fl_count: isDemandMode ? 0 : flCount,
      aps_count: isDemandMode ? 0 : apsCount,
      acs_count: isDemandMode ? 0 : acsCount,
      include_fmp: includeFmp,
      fmp_shift_mode: fmpShiftMode,
      fmp_shift: selectedFmpShift,
      settings,
      requested_sector_counts: effectiveSectorDemand,
      fixed_staff: fixedStaffPayload,
      locked_staff: [],
      officer_staff: officerStaffPayload,
      office_pool: officePoolPayload,
      license_mix_percent: isDemandMode ? minimumLicenseRatio : null,
      include_pareto: false,
      prefer_minimal_fl: preferMinimalFl,
      office_fallback_mode: 'auto',
      leader_exception_mode: 'forbid',
      max_leader_exception_hours: 0,
      continuation_min_sector_hours: null,
      warm_start_roster_priority: 0,
      solver_random_seed: 1,
      preferred_manual_configuration_id: manualSeedConfigId,
      warm_start: null,
      warm_start_snapshot_id: null,
    };
    return payload;
  }, [acsCount, apsCount, calculationMode, effectiveSectorDemand, fixedStaff, flCount, fmpShiftMode, includeFmp, manualSeedConfigId, minimumLicenseRatio, officePool, officerStaff, preferMinimalFl, selectedFmpShift, settings, totalPeople, usePeopleLimit]);

  const startJobWithProgress = async ({
    startJob,
    clearResult,
    payload,
    restartPlan,
    queuedMessage,
    errorMessage,
  }: {
    startJob: () => Promise<CalculationJobStart>;
    clearResult: boolean;
    payload?: CalculatorRequest | null;
    restartPlan?: JobRestartPlan | null;
    queuedMessage?: string;
    errorMessage: string;
  }) => {
    stopPolling();
    activeJobIdRef.current = null;
    if (payload !== undefined) {
      lastCalculationPayloadRef.current = payload;
    }
    if (restartPlan !== undefined) {
      lastRestartPlanRef.current = restartPlan;
    } else if (payload) {
      lastRestartPlanRef.current = { kind: 'calculator', payload };
    } else {
      lastRestartPlanRef.current = null;
    }
    bestResultVersionRef.current = 0;
    showDecisionAfterCancelRef.current = false;
    setCancelAction(null);
    setIsLoading(true);
    setCalculationProgress(0);
    setJobStatus(null);
    setTimeLimitDecision(null);
    setCalculationNotice(null);
    if (clearResult) {
      setResult(null);
      setConfigurationComparison(null);
      setConfigurationComparisonError(null);
      setIsConfigurationComparisonLoading(false);
    }
    setError(null);

    try {
      const job = await startJob();
      activeJobIdRef.current = job.job_id;
      setJobStatus({
        ...createQueuedJobStatus(job.job_id),
        message: queuedMessage ?? 'Čaka v vrsti za izračun.',
      });
      pollingTimerRef.current = window.setInterval(() => {
        void pollJobStatus(job.job_id);
      }, 2000);
      void pollJobStatus(job.job_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : errorMessage);
      setCancelAction(null);
      setIsLoading(false);
    }
  };

  const startCalculationFromPayload = async (payload: CalculatorRequest, clearResult: boolean) => {
    await startJobWithProgress({
      startJob: () => startCalculationJob(payload),
      clearResult,
      payload,
      errorMessage: 'Neznana napaka pri izračunu.',
    });
  };

  const runCalculation = async () => {
    setWhatIfSummary(null);
    setPausedPayload(null);
    setContinuationComparison(null);
    setManualResultConfigId(null);
    const basePayload = buildRequestPayload();
    const previousPayload = lastCalculationPayloadRef.current;
    const preservePreviousCoverage = Boolean(
      result
      && previousPayload
      && enablesOnlyAdditionalRegularShifts(previousPayload, basePayload),
    );
    const payload = result
      ? {
          ...basePayload,
          ...warmStartContinuationFields(result),
          continuation_min_sector_hours: preservePreviousCoverage
            ? Math.max(basePayload.continuation_min_sector_hours ?? 0, result.max_sector_hours)
            : basePayload.continuation_min_sector_hours,
        }
      : basePayload;
    await startCalculationFromPayload(payload, true);
  };

  const runLockedRosterOptimization = async (currentResult: CalculatorResponse) => {
    setContinuationComparison(null);
    const basePayload = lastCalculationPayloadRef.current ?? buildRequestPayload();
    const payload = clonePayloadForLockedRoster(basePayload, currentResult);
    const lockedPeople = payload.locked_staff.reduce((sum, row) => sum + row.count, 0);
    const officerPeople = payload.officer_staff.reduce((sum, row) => sum + row.count, 0);
    if (lockedPeople + officerPeople <= 0) {
      setError('Ni sestave ljudi, ki bi jo lahko zaklenil za nov izračun.');
      return;
    }
    setWhatIfSummary(`Zaklenjena sestava: ${lockedPeople + officerPeople} ljudi, solver ponovno optimizira razpored.`);
    setPausedPayload(null);
    setManualResultConfigId(null);
    await startCalculationFromPayload(payload, false);
  };

  const loadPatternProfile = async (regenerate = false) => {
    setIsPatternProfileLoading(true);
    setPatternProfileError(null);
    try {
      const payload = buildRequestPayload();
      const profile = regenerate
        ? await regeneratePatternLibrary(payload)
        : await inspectPatternLibrary(payload);
      setPatternProfile(profile);
    } catch (caught) {
      setPatternProfileError(caught instanceof Error ? caught.message : 'Napaka pri pripravi baze vzorcev.');
    } finally {
      setIsPatternProfileLoading(false);
    }
  };

  const loadManualConfigurationDetail = useCallback(async (id: string) => {
    setIsManualLibraryLoading(true);
    setManualLibraryError(null);
    try {
      const response = await getManualConfiguration(id);
      setManualDetail(response);
      setSelectedManualConfigId(response.id);
    } catch (caught) {
      setManualLibraryError(caught instanceof Error ? caught.message : 'Napaka pri nalaganju ročne konfiguracije.');
    } finally {
      setIsManualLibraryLoading(false);
    }
  }, []);

  const loadManualConfigurations = useCallback(async () => {
    setIsManualLibraryLoading(true);
    setManualLibraryError(null);
    try {
      const response = await getManualConfigurations();
      setManualLibrary(response);
      const currentStillExists = selectedManualConfigId
        ? response.configurations.some((configuration) => configuration.id === selectedManualConfigId)
        : false;
      const nextId = currentStillExists ? selectedManualConfigId : response.configurations[0]?.id ?? null;
      if (nextId) {
        await loadManualConfigurationDetail(nextId);
      } else {
        setManualDetail(null);
        setSelectedManualConfigId(null);
      }
    } catch (caught) {
      setManualLibraryError(caught instanceof Error ? caught.message : 'Napaka pri nalaganju baze ročnih konfiguracij.');
    } finally {
      setIsManualLibraryLoading(false);
    }
  }, [loadManualConfigurationDetail, selectedManualConfigId]);

  const deleteUserManualConfiguration = async (configuration: ManualConfigurationSummary | ManualConfigurationDetail) => {
    if (configuration.source_type !== 'user') {
      setManualLibraryError('Iz appa lahko izbrišeš samo konfiguracije, shranjene s strani uporabnika.');
      return;
    }
    const confirmed = window.confirm(`Izbrišem uporabniško konfiguracijo "${configuration.name}"?`);
    if (!confirmed) {
      return;
    }

    setDeletingManualConfigId(configuration.id);
    setManualLibraryError(null);
    try {
      await deleteManualConfiguration(configuration.id);
      const response = await getManualConfigurations();
      setManualLibrary(response);

      if (manualResultConfigId === configuration.id) {
        setManualResultConfigId(null);
      }

      if (selectedManualConfigId === configuration.id) {
        const nextId = response.configurations[0]?.id ?? null;
        if (nextId) {
          await loadManualConfigurationDetail(nextId);
        } else {
          setManualDetail(null);
          setSelectedManualConfigId(null);
        }
      }
    } catch (caught) {
      setManualLibraryError(caught instanceof Error ? caught.message : 'Napaka pri brisanju uporabniške konfiguracije.');
    } finally {
      setDeletingManualConfigId(null);
    }
  };

  const updateUserManualConfiguration = async (
    configuration: ManualConfigurationSummary | ManualConfigurationDetail,
    updates: { name: string; note: string | null },
  ): Promise<boolean> => {
    if (configuration.source_type !== 'user') {
      setManualLibraryError('Urejaš lahko samo konfiguracije, shranjene s strani uporabnika.');
      return false;
    }

    setUpdatingManualConfigId(configuration.id);
    setManualLibraryError(null);
    try {
      const updated = await updateUserConfiguration(configuration.id, updates);
      setManualLibrary((current) => current ? {
        ...current,
        configurations: current.configurations.map((item) => (
          item.id === updated.id ? { ...item, ...updated } : item
        )),
      } : current);
      setManualDetail((current) => current?.id === updated.id ? updated : current);
      return true;
    } catch (caught) {
      setManualLibraryError(caught instanceof Error ? caught.message : 'Napaka pri urejanju uporabniške konfiguracije.');
      return false;
    } finally {
      setUpdatingManualConfigId(null);
    }
  };

  const saveCurrentResultAsUserConfiguration = async (currentResult: CalculatorResponse) => {
    setSaveConfigurationName(defaultUserConfigurationName(currentResult));
    setPendingSaveResult(currentResult);
    setError(null);
    setManualLibraryError(null);
  };

  const cancelSaveUserConfiguration = () => {
    if (isSavingUserConfiguration) {
      return;
    }
    setPendingSaveResult(null);
    setSaveConfigurationName('');
  };

  const confirmSaveUserConfiguration = async () => {
    if (!pendingSaveResult) {
      return;
    }
    const currentResult = pendingSaveResult;
    const defaultName = defaultUserConfigurationName(currentResult);
    const name = saveConfigurationName.trim() || defaultName;
    setIsSavingUserConfiguration(true);
    setError(null);
    setManualLibraryError(null);
    try {
      const saved = await saveUserConfiguration({ name, result: currentResult });
      if (manualLibrary) {
        setManualLibrary({
          ...manualLibrary,
          configurations: [saved, ...manualLibrary.configurations.filter((item) => item.id !== saved.id)],
        });
      } else {
        setManualLibrary(await getManualConfigurations());
      }
      setManualDetail(saved);
      setSelectedManualConfigId(saved.id);
      setManualResultConfigId(saved.id);
      setPendingSaveResult(null);
      setSaveConfigurationName('');
      setActiveTab('manual-configs');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Napaka pri shranjevanju uporabniške konfiguracije.');
    } finally {
      setIsSavingUserConfiguration(false);
    }
  };

  const completeCurrentResult = async (currentResult: CalculatorResponse) => {
    if (currentResult.missing_sector_hours <= 0) {
      return;
    }
    stopParetoPolling();
    activeParetoJobIdRef.current = null;
    setIsCompletingConfiguration(true);
    setError(null);
    setParetoError(null);
    setPausedPayload(null);
    setParetoResult(null);
    setManualResultConfigId(null);

    try {
      const basePayload = lastCalculationPayloadRef.current ?? buildRequestPayload();
      setWhatIfSummary(
        `Dopolnitev do polne konfiguracije: ${currentResult.max_sector_hours}/${currentResult.requested_sector_hours} SH, `
        + `${currentResult.planned_people} ljudi`,
      );
      await startJobWithProgress({
        startJob: () => startCompleteConfigurationJob({
          request: basePayload,
          current_result: currentResult,
          time_limit_seconds: COMPLETE_CONFIGURATION_TIME_LIMIT_SECONDS,
        }),
        clearResult: false,
        payload: basePayload,
        restartPlan: {
          kind: 'complete',
          payload: basePayload,
          currentResult,
          timeLimitSeconds: COMPLETE_CONFIGURATION_TIME_LIMIT_SECONDS,
        },
        queuedMessage: 'Čaka v vrsti za dopolnitev konfiguracije.',
        errorMessage: 'Napaka pri dopolnjevanju konfiguracije.',
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Napaka pri dopolnjevanju konfiguracije.');
    } finally {
      setIsCompletingConfiguration(false);
    }
  };

  const settingsForManualConfiguration = useCallback((configuration?: ManualConfigurationDetail): CalculatorSettings => {
    const usedShiftCodes = new Set([
      ...(configuration?.fixed_staff.map((row) => row.shift) ?? []),
      ...(configuration?.officer_staff.map((row) => row.shift) ?? []),
      ...(configuration?.calculator_result?.people.map((person) => person.shift) ?? []),
    ]);
    const manualRulesByCode = new Map(
      configuration?.staff_rows
        .filter((row) => row.start_hour !== null && row.duration_hours !== null)
        .map((row) => [row.shift, row]) ?? [],
    );
    const activateManualShifts = (rules: ShiftRule[]): ShiftRule[] => rules.map((rule) => {
      if (!usedShiftCodes.has(rule.code)) {
        return rule;
      }
      const manualRule = manualRulesByCode.get(rule.code);
      return {
        ...rule,
        start_hour: manualRule?.start_hour ?? rule.start_hour,
        duration_hours: manualRule?.duration_hours ?? rule.duration_hours,
        enabled: true,
      };
    });
    const baseSettings: CalculatorSettings = {
      ...settings,
      include_required_shift_leaders: true,
      shifts: activateManualShifts(settings.shifts),
      officer_shifts: activateManualShifts(settings.officer_shifts),
    };
    if (!configuration?.manual_schedule) {
      return baseSettings;
    }
    return {
      ...baseSettings,
      v1_sector_limit: manualRoleSectorLimit(configuration, 'V1', baseSettings.v1_sector_limit),
      v2_sector_limit: manualRoleSectorLimit(configuration, 'V2', baseSettings.v2_sector_limit),
      v3_sector_limit: manualRoleSectorLimit(configuration, 'V3', baseSettings.v3_sector_limit),
      fmp_sector_limit: manualRoleSectorLimit(configuration, 'FMP', baseSettings.fmp_sector_limit),
    };
  }, [settings]);

  const sectorDemandFromManualConfiguration = useCallback((configuration: ManualConfigurationDetail): number[] | null => {
    const hourlyCoverage = configuration.manual_schedule?.hourly_coverage;
    if (!hourlyCoverage || hourlyCoverage.length === 0) {
      return null;
    }
    return clampSectorDemand(
      hourlyCoverage.map((hour) => hour.open_sectors),
      settings.max_sectors_per_hour,
    );
  }, [settings.max_sectors_per_hour]);

  const exportManualConfigurationExcel = async (configuration: ManualConfigurationDetail) => {
    const exportResult = (
      manualResultConfigId === configuration.id ? result : null
    ) ?? configuration.calculator_result;
    if (!exportResult) {
      setManualExcelExportError('Izbrana konfiguracija nima urnega razporeda za Excel izvoz.');
      return;
    }

    setExportingManualExcelConfigId(configuration.id);
    setManualExcelExportError(null);
    try {
      const manualDemand = sectorDemandFromManualConfiguration(configuration);
      const targetDemand = Array.from(
        { length: HOURS_IN_DAY },
        (_, index) => manualDemand?.[index] ?? exportResult.hourly_coverage[index]?.open_sectors ?? 0,
      );
      const manualSettings = settingsForManualConfiguration(configuration);
      const blob = await exportCalculatorWorkbook(
        exportResult,
        `Ročna konfiguracija ${configuration.name}`,
        targetDemand,
        [...manualSettings.shifts, ...manualSettings.officer_shifts],
      );
      const fileLabel = safeFilenamePart(configuration.name);
      downloadBlobFile(`atcconfmaker-${fileLabel || 'rocna-konfiguracija'}.xlsx`, blob);
    } catch (caught) {
      setManualExcelExportError(
        caught instanceof Error ? caught.message : 'Excel izvoza ni bilo mogoče pripraviti.',
      );
    } finally {
      setExportingManualExcelConfigId(null);
    }
  };

  const applyManualConfigurationSectorDemand = useCallback((configuration: ManualConfigurationDetail): number[] | null => {
    const demand = sectorDemandFromManualConfiguration(configuration);
    if (!demand) {
      return null;
    }
    const nextBaseSectors = bestBaseSectorCount(demand, baseSectors, settings.max_sectors_per_hour);
    setBaseSectors(nextBaseSectors);
    setSectorIntervals(intervalsFromSectorDemand(demand, nextBaseSectors, settings.max_sectors_per_hour));
    setSectorDemand(demand);
    setStaffSectorLimits(demand);
    setActiveDemandLabel(`Ročna konfiguracija ${configuration.name}`);
    setManualSeedConfigId(configuration.id);
    return demand;
  }, [baseSectors, sectorDemandFromManualConfiguration, settings.max_sectors_per_hour]);

  const openManualConfigurationInCalculator = (configuration: ManualConfigurationDetail) => {
    stopPolling();
    activeJobIdRef.current = null;
    lastCalculationPayloadRef.current = null;
    clearCalculatorResults();
    setCalculationMode('staff_to_coverage');
    setTotalPeople(clamp(configuration.parsed_total, 1, 80));
    setFlCount(clamp(configuration.license_counts.FL, 0, 80));
    setApsCount(clamp(configuration.license_counts.APS, 0, 80));
    const manualSettings = settingsForManualConfiguration(configuration);
    setFixedStaff(filterFixedStaffForActiveShifts(configuration.fixed_staff, manualSettings).map((row, index) => ({
      ...row,
      id: index + 1,
      role: row.role ?? '',
    })));
    setOfficerStaff(officerRowsFromRules(
      filterOfficerStaffForActiveShifts(configuration.officer_staff, manualSettings),
      manualSettings.officer_shifts,
    ));
    setOfficePool({ fl: 0, aps: 0, acs: 0 });
    setIncludeFmp(false);
    setSettings(manualSettings);
    applyManualConfigurationSectorDemand(configuration);
    if (configuration.calculator_result) {
      setResult(configuration.calculator_result);
      setManualResultConfigId(configuration.id);
    }
    setActiveTab('calculator');
  };

  const transferManualDemandInputToCalculator = (configuration: ManualConfigurationDetail) => {
    stopPolling();
    activeJobIdRef.current = null;
    clearCalculatorResults();
    const manualSettings = settingsForManualConfiguration(configuration);
    const transferredOfficePool = officePoolFromOfficerRules(configuration.officer_staff, manualSettings);
    const transferredOfficeCount = transferredOfficePool.fl + transferredOfficePool.aps + transferredOfficePool.acs;
    const regularPeopleLimit = Math.max(1, configuration.parsed_total - transferredOfficeCount);
    setCalculationMode('demand_to_staff');
    setUsePeopleLimit(true);
    setTotalPeople(clamp(regularPeopleLimit, 1, 80));
    setFlCount(0);
    setApsCount(0);
    setMinimumLicenseRatio({ fl: 50, aps: 0, acs: 50 });
    setFixedStaff([]);
    setOfficerStaff(createOfficerRows(manualSettings.officer_shifts));
    setOfficePool(transferredOfficePool);
    setIncludeFmp(false);
    setSettings(manualSettings);
    applyManualConfigurationSectorDemand(configuration);
    setActiveTab('calculator');
  };

  const runManualConfigurationOneDownCheck = async (configuration: ManualConfigurationDetail) => {
    applyManualConfigurationSectorDemand(configuration);
    setOneDownConfigId(configuration.id);
    setManualLibraryError(null);
    setError(null);
    setParetoError(null);
    setParetoResult(null);
    setPausedPayload(null);
    setManualResultConfigId(configuration.id);
    setWhatIfSummary(`One-down ${configuration.name}: ${configuration.parsed_total} -> ${configuration.parsed_total - 1} ljudi`);
    setActiveTab('calculator');
    try {
      await startJobWithProgress({
        startJob: () => startManualConfigurationOneDownJob(configuration.id, 8, settings),
        clearResult: true,
        payload: null,
        restartPlan: {
          kind: 'one-down',
          configurationId: configuration.id,
          configurationName: configuration.name,
          timeLimitSeconds: 8,
          settings,
          peopleBefore: configuration.parsed_total,
          peopleAfter: Math.max(0, configuration.parsed_total - 1),
        },
        queuedMessage: 'Čaka v vrsti za one-down preverjanje.',
        errorMessage: 'Napaka pri one-down izračunu.',
      });
    } catch (caught) {
      setManualLibraryError(caught instanceof Error ? caught.message : 'Napaka pri one-down izračunu.');
    } finally {
      setOneDownConfigId(null);
    }
  };

  const runShiftWhatIf = async (changes: ShiftWhatIfChange[]) => {
    setContinuationComparison(null);
    if (!result) {
      setError('What-if je na voljo šele po izračunu.');
      return;
    }
    if (changes.length === 0) {
      return;
    }

    const basePayload = lastCalculationPayloadRef.current ?? buildRequestPayload();
    const activeRegularShiftCodes = activeShiftCodes(basePayload.settings.shifts);
    const activeOfficerShiftCodes = activeShiftCodes(basePayload.settings.officer_shifts);
    for (const { person, shift } of changes) {
      if (!isOfficeSource(person.source) && !activeRegularShiftCodes.has(shift)) {
        setError(`Izmena ${shift} je izključena v nastavitvah pravil.`);
        return;
      }
      if (isOfficeSource(person.source) && !activeOfficerShiftCodes.has(shift)) {
        setError(`Office izmena ${shift} je izključena v nastavitvah pravil.`);
        return;
      }
    }

    const shiftByPersonId = new Map(changes.map(({ person, shift }) => [person.id, shift]));
    const selectedRegularPeople = changes.filter(({ person }) => !isOfficeSource(person.source));
    const selectedLockedStaff = selectedRegularPeople
      .map(({ person, shift }): LockedStaffRule => ({
        count: 1,
        license: leaderRoleDisplayId(person.role) ? 'FL' : person.license,
        shift,
        role: person.role,
        label: person.id,
      }))
      .filter((row) => activeRegularShiftCodes.has(row.shift));
    const selectedOfficePeople = changes.filter(({ person }) => isOfficeSource(person.source));
    const officerStaffForWhatIf = selectedOfficePeople.length > 0
      ? result.people
        .filter((person) => isOfficeSource(person.source))
        .reduce<OfficerStaffRule[]>((rows, person) => {
          const shift = shiftByPersonId.get(person.id) ?? person.shift;
          const existing = rows.find((row) => row.license === person.license && row.shift === shift);
          if (existing) {
            existing.count += 1;
          } else {
            rows.push({ count: 1, license: person.license, shift });
          }
          return rows;
        }, [])
        .filter((row) => activeOfficerShiftCodes.has(row.shift))
      : basePayload.officer_staff;
    const payload: CalculatorRequest = {
      ...basePayload,
      settings: {
        ...fullWhatIfSolverSettings(basePayload.settings),
      },
      locked_staff: selectedLockedStaff,
      officer_staff: officerStaffForWhatIf,
      continuation_min_sector_hours: null,
      solver_random_seed: Math.min(2_147_483_647, (basePayload.solver_random_seed ?? 1) + 1),
      ...warmStartContinuationFields(result),
    };

    const changeSummary = changes
      .map(({ person, shift }) => `${personDisplayId(person)} ${person.shift}→${shift}`)
      .join(', ');
    setContinuationComparison({
      actionLabel: `Skupni what-if: ${changeSummary}`,
      baseline: result,
      restartPlan: { kind: 'calculator', payload: basePayload },
    });
    setWhatIfSummary(
      `Skupni what-if (${changes.length}): ${changeSummary}. Solver računa na novo 600 s; stara konfiguracija je mehki start.`,
    );
    setPausedPayload(null);
    setManualResultConfigId(null);
    await startCalculationFromPayload(payload, false);
  };

  const runParetoAnalysis = async () => {
    stopParetoPolling();
    activeParetoJobIdRef.current = null;
    paretoResultVersionRef.current = 0;
    setIsParetoLoading(true);
    setParetoProgress(0);
    setParetoJobStatus(null);
    setParetoResult(null);
    setParetoError(null);
    setTimeLimitDecision(null);
    const payload = buildRequestPayload();

    try {
      const job = await startParetoJob(payload);
      activeParetoJobIdRef.current = job.job_id;
      setParetoJobStatus(createQueuedJobStatus(job.job_id, 'pareto'));
      paretoPollingTimerRef.current = window.setInterval(() => {
        void pollParetoJobStatus(job.job_id);
      }, 2000);
      void pollParetoJobStatus(job.job_id);
    } catch (caught) {
      setParetoError(caught instanceof Error ? caught.message : 'Neznana napaka pri Pareto analizi.');
      setIsParetoLoading(false);
    }
  };

  const cancelCurrentCalculation = async (pauseForReview = false) => {
    const jobId = activeJobIdRef.current;
    if (!jobId || cancelAction !== null || jobStatus?.cancel_requested) {
      return;
    }
    setCancelAction(pauseForReview ? 'review' : 'current');
    showDecisionAfterCancelRef.current = pauseForReview;

    try {
      const status = await cancelCalculationJob(jobId);
      if (pauseForReview) {
        setPausedPayload(null);
      }
      setJobStatus(status);
      setCalculationProgress(status.progress);
      if (status.status === 'finished') {
        stopPolling();
        setCancelAction(null);
        const currentResult = await loadJobResult(jobId, status.best_result_version);
        setCalculationProgress(100);
        setIsLoading(false);
        if (pauseForReview && lastRestartPlanRef.current && status.best_result_available) {
          setTimeLimitDecision({
            status,
            restartPlan: lastRestartPlanRef.current,
            currentResult,
            reason: 'manual-stop',
          });
        } else if (statusNeedsTimeLimitDecision(status, lastRestartPlanRef.current)) {
          setTimeLimitDecision({
            status,
            restartPlan: lastRestartPlanRef.current as JobRestartPlan,
            currentResult,
            reason: 'time-limit',
          });
        } else if (status.cancel_requested) {
          setCalculationNotice('Izračun je prekinjen. Prikazana je zadnja najdena rešitev.');
        }
        showDecisionAfterCancelRef.current = false;
        activeJobIdRef.current = null;
        return;
      }

      if (status.status === 'failed') {
        stopPolling();
        setCancelAction(null);
        if (status.best_result_available) {
          await loadJobResult(jobId, status.best_result_version);
        }
        if (status.cancel_requested) {
          setError(null);
          setCalculationNotice(
            status.best_result_available
              ? 'Izračun je prekinjen. Prikazana je zadnja najdena rešitev.'
              : 'Izračun je prekinjen pred prvo najdeno rešitvijo.',
          );
        } else {
          setError(status.error ?? status.message ?? 'Izračun se je ustavil.');
        }
        setIsLoading(false);
        showDecisionAfterCancelRef.current = false;
        activeJobIdRef.current = null;
        return;
      }

      if (status.best_result_available && status.best_result_version > bestResultVersionRef.current) {
        await loadJobResult(jobId, status.best_result_version);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Napaka pri preklicu izračuna.');
      setCancelAction(null);
      showDecisionAfterCancelRef.current = false;
    }
  };

  const resumePausedCalculation = async () => {
    if (!pausedPayload) {
      return;
    }
    const payload = pausedPayload;
    setPausedPayload(null);
    await startCalculationFromPayload(payload, false);
  };

  const preserveContinuationBaseline = (
    decision: TimeLimitDecision,
    actionLabel: string,
  ): CalculatorResponse | null => {
    const baseline = decision.currentResult ?? result;
    if (baseline) {
      setContinuationComparison({
        actionLabel,
        baseline,
        restartPlan: decision.restartPlan,
      });
    }
    return baseline;
  };

  const continueAfterTimeLimit = async () => {
    if (!timeLimitDecision) {
      return;
    }
    const decision = timeLimitDecision;
    const plan = decision.restartPlan;
    const extraSeconds = 300;
    const warmStartResult = preserveContinuationBaseline(decision, 'Nadaljevanje istega modela') ?? result;
    const warmStartSnapshotId = decision.status.warm_start_snapshot_id;
    setTimeLimitDecision(null);

    if (plan.kind === 'calculator') {
      const payload = clonePayloadForRegularContinuation(plan.payload, extraSeconds, warmStartResult, warmStartSnapshotId);
      setWhatIfSummary(
        warmStartSnapshotId || warmStartResult
          ? `Nadaljevanje redne faze +${extraSeconds} s iz začasne najboljše rešitve`
          : `Nadaljevanje redne faze +${extraSeconds} s brez operativnega office fallbacka`,
      );
      await startCalculationFromPayload(payload, false);
      return;
    }

    if (plan.kind === 'complete') {
      const completeWarmStart = warmStartResult ?? plan.currentResult;
      const payload = clonePayloadForRegularContinuation(plan.payload, extraSeconds, completeWarmStart, warmStartSnapshotId);
      const nextTimeLimit = Math.min(600, plan.timeLimitSeconds + extraSeconds);
      setWhatIfSummary(
        warmStartSnapshotId || completeWarmStart
          ? `Nadaljevanje dopolnitve +${extraSeconds} s iz začasne najboljše rešitve`
          : `Nadaljevanje dopolnitve +${extraSeconds} s brez operativnega office fallbacka`,
      );
      await startJobWithProgress({
        startJob: () => startCompleteConfigurationJob({
          request: payload,
          current_result: completeWarmStart,
          time_limit_seconds: nextTimeLimit,
        }),
        clearResult: false,
        payload,
        restartPlan: {
          ...plan,
          payload,
          timeLimitSeconds: nextTimeLimit,
        },
        queuedMessage: 'Čaka v vrsti za nadaljevanje dopolnitve.',
        errorMessage: 'Napaka pri nadaljevanju dopolnitve.',
      });
      return;
    }

    const nextTimeLimit = Math.min(120, plan.timeLimitSeconds + Math.max(8, plan.timeLimitSeconds));
    setWhatIfSummary(`Nadaljevanje one-down ${plan.configurationName}: ${plan.peopleBefore} -> ${plan.peopleAfter} ljudi`);
    await startJobWithProgress({
      startJob: () => startManualConfigurationOneDownJob(plan.configurationId, nextTimeLimit, plan.settings),
      clearResult: false,
      payload: null,
      restartPlan: {
        ...plan,
        timeLimitSeconds: nextTimeLimit,
      },
      queuedMessage: 'Čaka v vrsti za nadaljevanje one-down preverjanja.',
      errorMessage: 'Napaka pri nadaljevanju one-down izračuna.',
    });
  };

  const tryLeaderCrisisAfterTimeLimit = async (maxExceptionHours: number) => {
    if (!timeLimitDecision) {
      return;
    }
    const decision = timeLimitDecision;
    const plan = decision.restartPlan;
    if (plan.kind === 'one-down') {
      return;
    }
    const baseline = preserveContinuationBaseline(
      decision,
      `Krizni VI/FMP poskus: omejitev ${maxExceptionHours} ${maxExceptionHours === 1 ? 'ura' : 'ur'}`,
    );
    const warmStartSnapshotId = decision.status.warm_start_snapshot_id;
    const payload = clonePayloadForLeaderCrisis(
      plan.payload,
      maxExceptionHours,
      baseline,
      warmStartSnapshotId,
    );
    setTimeLimitDecision(null);
    setWhatIfSummary(`Krizni VI/FMP poskus; omejitev kriznih ur je ${maxExceptionHours}, rezultat pa mora ohraniti doseženi SH.`);

    if (plan.kind === 'complete') {
      await startJobWithProgress({
        startJob: () => startCompleteConfigurationJob({
          request: payload,
          current_result: baseline ?? plan.currentResult,
          time_limit_seconds: Math.min(600, Math.max(8, plan.timeLimitSeconds)),
        }),
        clearResult: false,
        payload,
        restartPlan: { ...plan, payload },
        queuedMessage: 'Čaka v vrsti za omejen krizni VI/FMP poskus.',
        errorMessage: 'Napaka pri kriznem VI/FMP poskusu.',
      });
      return;
    }
    await startCalculationFromPayload(payload, false);
  };

  const tryExtraPersonAfterTimeLimit = async () => {
    if (!timeLimitDecision) {
      return;
    }
    const decision = timeLimitDecision;
    const plan = decision.restartPlan;
    if (plan.kind === 'one-down') {
      return;
    }
    const baseline = preserveContinuationBaseline(decision, 'Poskus z +1 osebo') ?? result;
    if (!baseline) {
      setTimeLimitDecision(null);
      setError('Trenutna rešitev ni na voljo za poskus z dodatno osebo.');
      return;
    }
    const payload = clonePayloadForExtraPerson(
      plan.payload,
      baseline,
      decision.status.warm_start_snapshot_id,
    );
    if (!payload) {
      setTimeLimitDecision(null);
      setError('Dodatno osebo je mogoče preizkusiti samo v načinu Odprtost sektorjev do limita 80 ljudi.');
      return;
    }
    setTimeLimitDecision(null);
    setWhatIfSummary(
      `Nov 600-sekundni poskus z +1 osebo; novi limit je ${payload.total_people} ljudi, prejšnja konfiguracija pa je mehki start.`,
    );

    if (plan.kind === 'complete') {
      await startJobWithProgress({
        startJob: () => startCompleteConfigurationJob({
          request: payload,
          current_result: baseline,
          time_limit_seconds: FULL_WHAT_IF_TIME_LIMIT_SECONDS,
        }),
        clearResult: false,
        payload,
        restartPlan: { ...plan, payload, timeLimitSeconds: FULL_WHAT_IF_TIME_LIMIT_SECONDS },
        queuedMessage: 'Čaka v vrsti za poskus z dodatno osebo.',
        errorMessage: 'Napaka pri poskusu z dodatno osebo.',
      });
      return;
    }
    await startCalculationFromPayload(payload, false);
  };

  const tryLeaderSectorHoursAfterTimeLimit = async (
    v1SectorLimit: number,
    v2SectorLimit: number,
  ) => {
    if (!timeLimitDecision) {
      return;
    }
    const decision = timeLimitDecision;
    const plan = decision.restartPlan;
    if (plan.kind === 'one-down') {
      return;
    }
    const baseline = preserveContinuationBaseline(
      decision,
      `VI ure: VI1 do ${v1SectorLimit}, VI2 do ${v2SectorLimit}`,
    ) ?? result;
    const payload = clonePayloadForLeaderSectorHours(
      plan.payload,
      v1SectorLimit,
      v2SectorLimit,
      baseline,
      decision.status.warm_start_snapshot_id,
    );
    setTimeLimitDecision(null);
    setWhatIfSummary(
      `Nov 600-sekundni izračun: VI1 do ${payload.settings.v1_sector_limit} ur, VI2 do ${payload.settings.v2_sector_limit} ur; prejšnja konfiguracija je mehki start.`,
    );

    if (plan.kind === 'complete') {
      await startJobWithProgress({
        startJob: () => startCompleteConfigurationJob({
          request: payload,
          current_result: baseline ?? plan.currentResult,
          time_limit_seconds: FULL_WHAT_IF_TIME_LIMIT_SECONDS,
        }),
        clearResult: false,
        payload,
        restartPlan: {
          ...plan,
          payload,
          timeLimitSeconds: FULL_WHAT_IF_TIME_LIMIT_SECONDS,
        },
        queuedMessage: 'Čaka v vrsti za nov poskus z več sektorskimi urami VI1/VI2.',
        errorMessage: 'Napaka pri poskusu z več sektorskimi urami VI1/VI2.',
      });
      return;
    }
    await startCalculationFromPayload(payload, false);
  };

  const tryEmergencyShiftAfterTimeLimit = async (shiftCode: string) => {
    if (!timeLimitDecision) {
      return;
    }
    const decision = timeLimitDecision;
    const plan = decision.restartPlan;
    if (plan.kind === 'one-down') {
      return;
    }
    const baseline = preserveContinuationBaseline(decision, `Krizni poskus z izmeno ${shiftCode}`);
    const payload = clonePayloadForEmergencyShift(
      plan.payload,
      shiftCode,
      baseline,
      decision.status.warm_start_snapshot_id,
    );
    setTimeLimitDecision(null);
    setWhatIfSummary(`Izmena ${shiftCode} je začasno omogočena samo za ta nadaljevalni poskus.`);

    if (plan.kind === 'complete') {
      await startJobWithProgress({
        startJob: () => startCompleteConfigurationJob({
          request: payload,
          current_result: baseline ?? plan.currentResult,
          time_limit_seconds: Math.min(600, Math.max(8, plan.timeLimitSeconds)),
        }),
        clearResult: false,
        payload,
        restartPlan: { ...plan, payload },
        queuedMessage: `Čaka v vrsti za krizni poskus z izmeno ${shiftCode}.`,
        errorMessage: `Napaka pri kriznem poskusu z izmeno ${shiftCode}.`,
      });
      return;
    }
    await startCalculationFromPayload(payload, false);
  };

  const tryOfficeFallbackAfterTimeLimit = async (officeSelection: OfficeFallbackSelection) => {
    if (!timeLimitDecision) {
      return;
    }
    const decision = timeLimitDecision;
    const plan = decision.restartPlan;
    if (plan.kind === 'one-down') {
      return;
    }
    const warmStartResult = preserveContinuationBaseline(decision, 'Preizkus z operativnim office') ?? result;
    const warmStartSnapshotId = decision.status.warm_start_snapshot_id;
    const payload = clonePayloadForOfficeFallback(
      plan.payload,
      includeFmp,
      officeSelection,
      warmStartResult,
      warmStartSnapshotId,
    );
    if (!hasOfficeFallbackSelection(officeSelection)) {
      setTimeLimitDecision(null);
      setError('Office fallback ni na voljo, ker ni vpisan noben operativni office.');
      return;
    }
    setTimeLimitDecision(null);

    setWhatIfSummary(
      `${officeSelection.mode === 'fixed'
        ? `Office nadaljevanje z izmeno ${officeSelection.shift}`
        : warmStartSnapshotId || warmStartResult
          ? 'Office fallback iz začasne najboljše rešitve'
          : 'Takojšnji preizkus operativnega office fallbacka'}; `
      + `600 s, FMP ${includeFmp ? 'vključen' : 'izključen'}, krajše izmene ostanejo optimizacijski cilj. `
      + 'Obstoječa sestava ima prednost; spremembe izmen se uporabijo le, kadar izboljšajo izvedljivost.',
    );
    await startCalculationFromPayload(payload, false);
  };

  const keepCurrentTimeLimitResult = () => {
    setCalculationNotice(
      timeLimitDecision?.reason === 'manual-stop'
        ? 'Izračun je prekinjen. Obdržana je zadnja najdena rešitev.'
        : 'Obdržana je trenutno najboljša rešitev.',
    );
    setContinuationComparison(null);
    setTimeLimitDecision(null);
  };

  const acceptContinuationResult = () => {
    setContinuationComparison(null);
    setCalculationNotice('Uporabljen je rezultat nadaljevalnega poskusa.');
  };

  const restoreContinuationBaseline = () => {
    if (!continuationComparison) {
      return;
    }
    setResult(continuationComparison.baseline);
    lastRestartPlanRef.current = continuationComparison.restartPlan;
    lastCalculationPayloadRef.current = continuationComparison.restartPlan.kind === 'one-down'
      ? null
      : continuationComparison.restartPlan.payload;
    setWhatIfSummary(null);
    setTimeLimitDecision(null);
    setContinuationComparison(null);
    setCalculationNotice('Obdržana je prejšnja rešitev pred nadaljevalnim poskusom.');
  };

  const rememberOnboardingCompletion = () => {
    try {
      window.localStorage.setItem(onboardingCompletedStorageKey, 'true');
    } catch {
      // Vodič se zapre tudi, kadar lokalna shramba brskalnika ni na voljo.
    }
    setIsOnboardingOpen(false);
  };

  const startGuidedTour = (kind: GuidedTourKind) => {
    rememberOnboardingCompletion();
    setGuidedTourKind(kind);
    if (kind === 'new-configuration') {
      setCalculationMode('demand_to_staff');
      setActiveTab('calculator');
    } else {
      setActiveTab('manual-configs');
    }
  };

  const cancelCurrentParetoAnalysis = async () => {
    const jobId = activeParetoJobIdRef.current;
    if (!jobId) {
      return;
    }

    try {
      const status = await cancelCalculationJob(jobId);
      setParetoJobStatus(status);
      setParetoProgress(status.progress);
      if (status.status === 'finished') {
        stopParetoPolling();
        await loadParetoJobResult(jobId, status.best_result_version);
        setParetoProgress(100);
        setIsParetoLoading(false);
        activeParetoJobIdRef.current = null;
        return;
      }

      if (status.status === 'failed') {
        stopParetoPolling();
        if (status.best_result_available) {
          await loadParetoJobResult(jobId, status.best_result_version);
        }
        setParetoError(status.error ?? status.message ?? 'Pareto analiza je bila preklicana.');
        setIsParetoLoading(false);
        activeParetoJobIdRef.current = null;
        return;
      }

      if (status.best_result_available && status.best_result_version > paretoResultVersionRef.current) {
        await loadParetoJobResult(jobId, status.best_result_version);
      }
    } catch (caught) {
      setParetoError(caught instanceof Error ? caught.message : 'Napaka pri preklicu Pareto analize.');
    }
  };

  const openComparisonMatch = async (id: string) => {
    setActiveTab('manual-configs');
    await loadManualConfigurationDetail(id);
  };

  const isProgramThree = activeTab === 'analysis' || activeTab === 'theory';
  const programLabel = activeTab === 'airport'
    ? '2 / LKZP odprtost'
    : isProgramThree
      ? '3 / Analiza in teorija'
      : '1 / ATCConfMaker';
  const heroTitle = activeTab === 'airport'
    ? 'LKZP odprtost'
    : isProgramThree
      ? 'Analiza odprtosti sektorjev'
      : 'ATCConfMaker';
  const heroDescription = activeTab === 'airport'
    ? 'Izbira dovoljenih izmen ter razpored dela, pavz in prisotnega asistenta za letališke kontrole.'
    : isProgramThree
      ? 'Analiza napovedane odprtosti sektorjev in razlaga teoretičnega ozadja modela.'
      : 'Program za izračun maksimalnih sektorskih ur, obveznih FL vlog, primerjavo konfiguracij in predlagano sestavo izmen.';
  const cancellationInProgress = isCancellationInProgress(
    cancelAction,
    jobStatus?.cancel_requested === true,
  );
  const cancellationDetail = cancellationDetailFor(
    cancelAction,
    jobStatus?.best_result_available === true,
  );

  return (
    <main className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">ATCConfMaker</p>
          <h1>{heroTitle}</h1>
          <p>{heroDescription}</p>
        </div>
        <div className="hero-card">
          <span>Program</span>
          <strong>{programLabel}</strong>
          <button
            className="hero-guide-button"
            onClick={() => {
              setActiveTab('calculator');
              setIsOnboardingOpen(true);
            }}
            type="button"
          >
            Zaženi vodič
          </button>
        </div>
      </header>

      <nav className="tabs" aria-label="Glavna navigacija">
        <button className={activeTab === 'calculator' ? 'active' : ''} onClick={() => setActiveTab('calculator')} type="button">
          ATCConfMaker
        </button>
        <button className={activeTab === 'settings' ? 'active' : ''} onClick={() => setActiveTab('settings')} type="button">
          Pravila
        </button>
        <button className={activeTab === 'manual-configs' ? 'active' : ''} onClick={() => setActiveTab('manual-configs')} type="button">
          Ročne konfiguracije
        </button>
        <button className={activeTab === 'comparison' ? 'active' : ''} onClick={() => setActiveTab('comparison')} type="button">
          Primerjevalnik konfiguracij
        </button>
        <button className={activeTab === 'future' ? 'active' : ''} onClick={() => setActiveTab('future')} type="button">
          Futuristični ATCConfMaker*
        </button>
        <button className={activeTab === 'airport' ? 'active' : ''} onClick={() => setActiveTab('airport')} type="button">
          LKZP odprtost
        </button>
        <button className={activeTab === 'analysis' ? 'active' : ''} onClick={() => setActiveTab('analysis')} type="button">
          Analiza odprtosti sektorjev
        </button>
        <button className={activeTab === 'theory' ? 'active' : ''} onClick={() => setActiveTab('theory')} type="button">
          Teorija modela
        </button>
      </nav>

      <div hidden={activeTab !== 'analysis'}>
        <ModelAnalysis
          onUseSectorDemand={(values, label) => applySectorDemand(values, label)}
          onQueueSectorDemand={enqueueSectorDemands}
        />
      </div>

      {activeTab === 'future' ? (
        <FutureCalculator settings={settings} hourlyDemand={effectiveSectorDemand} />
      ) : activeTab === 'airport' ? (
        <AirportCalculator />
      ) : activeTab === 'settings' ? (
        <SettingsPanel
          settings={settings}
          defaultExtraNightA21FlCount={Math.max(0, defaultSettings.required_night_fl_count - 1)}
          saveState={settingsSaveState}
          onChange={(nextSettings) => {
            setSettings(nextSettings);
            setSettingsSaveState('idle');
          }}
          onSave={saveSettings}
          onReset={resetSettings}
        />
      ) : activeTab === 'manual-configs' ? (
        <ManualConfigurationsPanel
          library={manualLibrary}
          detail={manualDetail}
          currentDemand={effectiveSectorDemand}
          currentResult={result}
          manualResultConfigId={manualResultConfigId}
          error={manualLibraryError}
          isLoading={isManualLibraryLoading}
          oneDownConfigId={oneDownConfigId}
          exportingExcelConfigId={exportingManualExcelConfigId}
          excelExportError={manualExcelExportError}
          deletingConfigId={deletingManualConfigId}
          updatingConfigId={updatingManualConfigId}
          selectedId={selectedManualConfigId}
          onLoadLibrary={loadManualConfigurations}
          onSelect={loadManualConfigurationDetail}
          onOpenInCalculator={openManualConfigurationInCalculator}
          onTransferDemandInput={transferManualDemandInputToCalculator}
          onRunOneDown={runManualConfigurationOneDownCheck}
          onExportExcel={exportManualConfigurationExcel}
          onDeleteUserConfiguration={deleteUserManualConfiguration}
          onUpdateUserConfiguration={updateUserManualConfiguration}
        />
      ) : activeTab === 'comparison' ? (
        <ConfigurationComparisonPanel
          result={result}
          comparison={configurationComparison}
          isLoading={isConfigurationComparisonLoading}
          error={configurationComparisonError}
          onOpenMatch={(id) => void openComparisonMatch(id)}
          manualLibrary={manualLibrary}
        />
      ) : activeTab === 'theory' ? (
        <TheoryPanel />
      ) : activeTab === 'calculator' ? (
        <div className={`workspace ${result || paretoResult ? 'workspace-has-output' : ''}`}>
          <section className="panel form-panel">
            <div className="form-panel-top">
              <h2>Vhodni podatki</h2>
              <div className="mode-switch" data-tour="calculation-mode">
                <button
                  className={calculationMode === 'demand_to_staff' ? 'active' : ''}
                  onClick={() => setCalculationMode('demand_to_staff')}
                  type="button"
                >
                  <strong>Odprtost sektorjev</strong>
                  <small>Koliko ljudi potrebujemo?</small>
                </button>
                <button
                  className={calculationMode === 'staff_to_coverage' ? 'active' : ''}
                  onClick={() => setCalculationMode('staff_to_coverage')}
                  type="button"
                >
                  <strong>Določeno število ljudi</strong>
                  <small>Kaj lahko naredimo?</small>
                </button>
              </div>
              <div className="calculator-default-row">
                <button className="secondary-button compact-button" onClick={saveCalculatorInputsAsDefault} type="button">
                  Shrani parametre kot default
                </button>
                <span className={`save-state ${calculatorInputsSaveState}`}>
                  {calculatorInputsSaveState === 'saved'
                    ? 'Shranjeno'
                    : calculatorInputsSaveState === 'failed'
                      ? 'Shranjevanje ni uspelo'
                      : 'Za naslednji zagon'}
                </span>
              </div>
            </div>
            {calculationMode === 'staff_to_coverage' ? (
            <div className="form-grid" data-tour="staff-counts">
              <NumberField
                label="Skupaj ljudi"
                min={1}
                max={80}
                value={totalPeople}
                onChange={(value) => updateCounts(value, flCount, apsCount)}
                helper="Točno število rednih ljudi, ki bo prikazano v planu. Neuporabljeni ostanejo v rezultatu z 0 sektorskimi urami."
              />
              <NumberField
                label="FL licence"
                min={0}
                max={totalPeople}
                value={flCount}
                onChange={(value) => updateCounts(totalPeople, value, apsCount)}
              />
              <NumberField
                label="APS licence"
                min={0}
                max={totalPeople - flCount}
                value={apsCount}
                onChange={(value) => updateCounts(totalPeople, flCount, value)}
                helper="LOWER: APS ali FL."
              />
              <NumberField
                label="ACS licence"
                min={0}
                value={acsCount}
                onChange={(value) => updateCounts(totalPeople, flCount, totalPeople - flCount - value)}
                helper="ACS = skupaj − FL − APS. Nad LOWER: ACS ali FL. ALL: FL + FL."
              />
            </div>
            ) : null}
            {calculationMode === 'demand_to_staff' ? (
              <section className="demand-card license-availability-card" data-tour="license-people">
                <div className="demand-header">
                  <div>
                    <p className="eyebrow">Ciljno razmerje</p>
                    <h3>Licence in limit ljudi</h3>
                  </div>
                </div>
                <p className="demand-help compact-help">
                  Vpiši ciljno razmerje licenc; limit ljudi je opcijski.
                  <MetricInfo text="Če limit ni vklopljen, solver išče najmanjšo zasedbo. Če je vklopljen, išče najboljšo rešitev znotraj limita." />
                </p>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={usePeopleLimit}
                    onChange={(event) => setUsePeopleLimit(event.target.checked)}
                  />
                  <span>Vnesi limit ljudi</span>
                </label>
                <div className="form-grid license-grid">
                  {usePeopleLimit ? (
                    <NumberField
                      label="Limit ljudi"
                      min={1}
                      max={80}
                      value={totalPeople}
                      onChange={(value) => updateCounts(value, flCount, apsCount)}
                      helper="Zgornja meja ljudi, ki jih sme predlagati."
                    />
                  ) : null}
                  <NumberField
                    label="FL %"
                    min={0}
                    max={100}
                    value={minimumLicenseRatio.fl}
                    onChange={(value) => updateMinimumLicenseRatio({ fl: value })}
                    helper={formatLicenseRatioShare(minimumLicenseRatio.fl, minimumLicenseRatioTotal)}
                  />
                  <NumberField
                    label="APS %"
                    min={0}
                    max={100}
                    value={minimumLicenseRatio.aps}
                    onChange={(value) => updateMinimumLicenseRatio({ aps: value })}
                    helper={formatLicenseRatioShare(minimumLicenseRatio.aps, minimumLicenseRatioTotal)}
                  />
                  <NumberField
                    label="ACS %"
                    min={0}
                    max={100}
                    value={minimumLicenseRatio.acs}
                    onChange={(value) => updateMinimumLicenseRatio({ acs: value })}
                    helper={formatLicenseRatioShare(minimumLicenseRatio.acs, minimumLicenseRatioTotal)}
                  />
                </div>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={preferMinimalFl}
                    onChange={(event) => setPreferMinimalFl(event.target.checked)}
                  />
                  <span>Pri sicer enaki rešitvi uporabi čim več ACS (čim manj FL/APS)</span>
                </label>
              </section>
            ) : null}

            <section className="fmp-row fmp-control">
              <label className="check-row">
                <input type="checkbox" checked={includeFmp} onChange={(event) => setIncludeFmp(event.target.checked)} />
                <span>
                  Vključi FMP
                  <MetricInfo text="Konfiguracija z vključenim FMP" />
                </span>
              </label>
              {includeFmp ? (
                <div className="fmp-options">
                  <div className="mode-switch fmp-mode-switch" role="group" aria-label="Način FMP izmene">
                    <button
                      className={fmpShiftMode === 'auto' ? 'active' : ''}
                      onClick={() => setFmpShiftMode('auto')}
                      type="button"
                    >
                      Poišči najboljšo
                    </button>
                    <button
                      className={fmpShiftMode === 'fixed' ? 'active' : ''}
                      onClick={() => setFmpShiftMode('fixed')}
                      type="button"
                    >
                      Fiksna izmena
                    </button>
                  </div>
                  {fmpShiftMode === 'fixed' ? (
                    <label className="field fmp-shift-field">
                      FMP izmena
                      <select value={selectedFmpShift} onChange={(event) => setFmpShift(event.target.value)}>
                        {fmpShiftOptions.map((shift) => (
                          <option key={shift} value={shift}>{shift}</option>
                        ))}
                      </select>
                      <small>Ročno določena FMP izmena.</small>
                    </label>
                  ) : (
                    <p className="fmp-help">
                      Auto izbere najboljšo aktivno FMP izmeno med {FMP_AUTO_SHIFT_CODES.join(', ')}.
                    </p>
                  )}
                </div>
              ) : null}
            </section>
            <FixedStaffEditor rows={fixedStaff} shifts={settings.shifts} onChange={setFixedStaff} />
            <OfficePoolEditor pool={officePool} onChange={setOfficePool} />
            <OfficerStaffEditor
              rows={officerStaff}
              shifts={settings.officer_shifts}
              onChange={setOfficerStaff}
            />
            <RequiredRoleLimitsEditor settings={settings} onChange={setSettings} />
            <OptimizationPrioritiesEditor settings={settings} onChange={setSettings} />

            <details className="demand-card pattern-library-card">
              <summary>Napredno: baza vzorcev</summary>
              <div className="pattern-library-body">
                <div className="pattern-actions">
                  <button
                    className="secondary-button compact-button"
                    disabled={isPatternProfileLoading}
                    onClick={() => void loadPatternProfile(false)}
                    type="button"
                  >
                    {isPatternProfileLoading ? 'Pripravljam ...' : 'Osveži profil'}
                  </button>
                  <button
                    className="secondary-button compact-button"
                    disabled={isPatternProfileLoading}
                    onClick={() => void loadPatternProfile(true)}
                    type="button"
                  >
                    Regeneriraj bazo
                  </button>
                </div>
                {patternProfileError ? <div className="error-box">{patternProfileError}</div> : null}
                {patternProfile ? (
                  <div className="pattern-profile-grid">
                    <div>
                      <span>Vzorcev</span>
                      <strong>{patternProfile.pattern_count}</strong>
                    </div>
                    <div>
                      <span>Status cache</span>
                      <strong>{patternProfile.cache_status}</strong>
                    </div>
                    <div>
                      <span>Čas generiranja</span>
                      <strong>{patternProfile.generated_at_seconds.toFixed(3)} s</strong>
                    </div>
                    <div>
                      <span>Cache pot</span>
                      <strong>{patternProfile.cache_path ?? '—'}</strong>
                    </div>
                    <div className="wide-pattern-cell">
                      <span>Rule hash</span>
                      <strong>{patternProfile.rule_signature}</strong>
                    </div>
                    <div className="wide-pattern-cell">
                      <span>Po izmenah</span>
                      <strong>{formatPatternBreakdown(patternProfile.patterns_by_shift, 12)}</strong>
                    </div>
                    <div>
                      <span>Po licencah</span>
                      <strong>{formatPatternBreakdown(patternProfile.patterns_by_license)}</strong>
                    </div>
                    <div>
                      <span>Po vlogah</span>
                      <strong>{formatPatternBreakdown(patternProfile.patterns_by_role)}</strong>
                    </div>
                  </div>
                ) : (
                  <p className="demand-help">Profil baze se pripravi na zahtevo za trenutne izmene in pravila.</p>
                )}
              </div>
            </details>

            {calculationMode === 'staff_to_coverage' ? (
              <SectorLimitInput
                maxSectors={settings.max_sectors_per_hour}
                values={staffSectorLimits}
                onChange={(values) => {
                  setStaffSectorLimits(normalizeStaffSectorLimits(values, settings.max_sectors_per_hour));
                  setActiveDemandLabel(null);
                  setManualSeedConfigId(null);
                }}
              />
            ) : (
              <>
                <section className="demand-card queue-card">
                  <div className="demand-header">
                    <div>
                      <p className="eyebrow">Queue</p>
                      <h3>Profili iz analize</h3>
                    </div>
                    {sectorDemandQueue.length > 0 ? (
                      <button className="secondary-button compact-button" onClick={() => setSectorDemandQueue([])} type="button">
                        Počisti
                      </button>
                    ) : null}
                  </div>
                  {activeDemandLabel ? <div className="queue-active">Aktivno: {activeDemandLabel}</div> : null}
                  {isLoading && jobStatus ? (
                    <div className="queue-active">
                      Trenutni izračun: {jobStatusLabels[jobStatus.status]} · {jobStatus.progress}% · {formatProgressMessage(jobStatus)}
                    </div>
                  ) : null}
                  {sectorDemandQueue.length > 0 ? (
                    <div className="queue-list">
                      {sectorDemandQueue.map((item) => (
                        <div className="queue-item" key={item.id}>
                          <div>
                            <strong>{item.label}</strong>
                            <small>{summarizeSectorDemand(item.values)}</small>
                          </div>
                          <button className="secondary-button compact-button" onClick={() => loadQueuedSectorDemand(item)} type="button">
                            Naloži
                          </button>
                          <button
                            className="secondary-button compact-button"
                            onClick={() => setSectorDemandQueue((queue) => queue.filter((queuedItem) => queuedItem.id !== item.id))}
                            type="button"
                          >
                            Odstrani
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="queue-empty">Queue je prazen.</div>
                  )}
                </section>
                <SectorDemandInput
                  maxSectors={settings.max_sectors_per_hour}
                  values={effectiveSectorDemand}
                  onChange={(values) => {
                    setSectorDemand(clampSectorDemand(values, settings.max_sectors_per_hour));
                    setActiveDemandLabel(null);
                    setManualSeedConfigId(null);
                  }}
                />
              </>
            )}
            {isLoading ? (
              <div className="calculation-progress" role="status" aria-live="polite">
                <div className="progress-row">
                  <span>{cancellationInProgress ? 'Prekinitev v teku' : jobStatus ? jobStatusLabels[jobStatus.status] : 'Oddajam izračun ...'}</span>
                  <strong>{calculationProgress}%</strong>
                </div>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${calculationProgress}%` }} />
                </div>
                <div className="progress-meta">
                  <span>{cancellationInProgress ? 'Čakam na varen zaključek solverja.' : jobStatus ? formatProgressMessage(jobStatus) : 'Pripravljam job v ozadju.'}</span>
                  <span>{jobStatus ? `${jobStatus.elapsed_seconds.toFixed(1)} s` : '0.0 s'}</span>
                </div>
                {jobStatus ? (
                  <div className="progress-best">
                    {formatBestResultSummary(jobStatus) ?? 'Najboljša rešitev še ni najdena.'}
                  </div>
                ) : null}
                {cancellationInProgress ? (
                  <div className="progress-cancel-state">
                    <strong>Zahteva za prekinitev je poslana</strong>
                    <span>{cancellationDetail}</span>
                  </div>
                ) : (
                  <>
                    {jobStatus ? <ProgressPhase status={jobStatus} /> : null}
                    {jobStatus && formatSolverSummary(jobStatus) ? (
                      <div className="progress-solver">{formatSolverSummary(jobStatus)}</div>
                    ) : null}
                    {jobStatus && formatPatternCacheStatus(jobStatus) ? (
                      <div className="progress-pattern">{formatPatternCacheStatus(jobStatus)}</div>
                    ) : null}
                    {jobStatus && formatPatternLimitStatus(jobStatus) ? (
                      <div className="progress-pattern">{formatPatternLimitStatus(jobStatus)}</div>
                    ) : null}
                    {jobStatus && formatPatternCheckedLimits(jobStatus) ? (
                      <div className="progress-pattern checked-limits">{formatPatternCheckedLimits(jobStatus)}</div>
                    ) : null}
                  </>
                )}
                <div className="progress-actions">
                  {cancellationInProgress ? (
                    <button className="secondary-button compact-button cancel-button" disabled type="button">
                      Prekinitev v teku ...
                    </button>
                  ) : jobStatus?.best_result_available ? (
                    <>
                      <button className="secondary-button compact-button cancel-button" onClick={() => void cancelCurrentCalculation(true)} type="button">
                        Ustavi in pokaži možnosti
                      </button>
                      <button className="secondary-button compact-button cancel-button" onClick={() => void cancelCurrentCalculation(false)} type="button">
                        Prekini in uporabi trenutno
                      </button>
                    </>
                  ) : (
                    <button className="secondary-button compact-button cancel-button" onClick={() => void cancelCurrentCalculation(false)} type="button">
                      Prekini izračun
                    </button>
                  )}
                </div>
              </div>
            ) : null}
            <div className="action-row">
              <button
                className="primary-button"
                data-tour="calculate-button"
                disabled={isLoading || isParetoLoading}
                onClick={() => void runCalculation()}
                type="button"
              >
                {isLoading
                  ? 'Računam ...'
                  : calculationMode === 'demand_to_staff'
                    ? 'Izračunaj potrebno zasedbo'
                    : 'Izračunaj, kaj lahko naredimo'}
              </button>
              {calculationMode === 'staff_to_coverage' ? (
                <button
                  className="secondary-button"
                  disabled={isLoading || isParetoLoading}
                  onClick={() => void runParetoAnalysis()}
                  type="button"
                >
                  {isParetoLoading ? 'Računam Pareto ...' : 'Primerjaj po številu ljudi (Pareto)'}
                </button>
              ) : null}
              {pausedPayload ? (
                <button className="secondary-button" disabled={isLoading || isParetoLoading} onClick={() => void resumePausedCalculation()} type="button">
                  Nadaljuj izračun
                </button>
              ) : null}
            </div>
            {timeLimitDecision ? (
              <TimeLimitDecisionPanel
                key={`${timeLimitDecision.status.job_id}:${timeLimitDecision.reason}`}
                decision={timeLimitDecision}
                isBusy={isLoading || isParetoLoading}
                onContinue={() => void continueAfterTimeLimit()}
                onTryExtraPerson={() => void tryExtraPersonAfterTimeLimit()}
                onTryLeaderSectorHours={(v1SectorLimit, v2SectorLimit) => (
                  void tryLeaderSectorHoursAfterTimeLimit(v1SectorLimit, v2SectorLimit)
                )}
                onTryLeaderCrisis={(maxExceptionHours) => void tryLeaderCrisisAfterTimeLimit(maxExceptionHours)}
                onTryOfficeFallback={(selectedOfficePool) => void tryOfficeFallbackAfterTimeLimit(selectedOfficePool)}
                onTryEmergencyShift={(shiftCode) => void tryEmergencyShiftAfterTimeLimit(shiftCode)}
                onKeepCurrent={keepCurrentTimeLimitResult}
              />
            ) : null}
            {continuationComparison ? (
              <ContinuationComparisonPanel
                comparison={continuationComparison}
                candidate={result}
                isBusy={isLoading}
                onAcceptCandidate={acceptContinuationResult}
                onRestoreBaseline={restoreContinuationBaseline}
              />
            ) : null}
            {calculationNotice && !isLoading ? (
              <div className="calculation-notice" role="status" aria-live="polite">{calculationNotice}</div>
            ) : null}
            {isParetoLoading ? (
              <div className="calculation-progress pareto-progress" role="status" aria-live="polite">
                <div className="progress-row">
                  <span>{paretoJobStatus ? jobStatusLabels[paretoJobStatus.status] : 'Oddajam Pareto analizo ...'}</span>
                  <strong>{paretoProgress}%</strong>
                </div>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${paretoProgress}%` }} />
                </div>
                <div className="progress-meta">
                  <span>{paretoJobStatus?.message ?? 'Pripravljam Pareto job v ozadju.'}</span>
                  <span>{paretoJobStatus ? `${paretoJobStatus.elapsed_seconds.toFixed(1)} s` : '0.0 s'}</span>
                </div>
                {paretoJobStatus ? (
                  <div className="progress-best">
                    {formatBestResultSummary(paretoJobStatus) ?? 'Pareto točke še niso najdene.'}
                  </div>
                ) : null}
                <button className="secondary-button compact-button cancel-button" onClick={cancelCurrentParetoAnalysis} type="button">
                  Prekini Pareto analizo
                </button>
              </div>
            ) : null}
            {error ? <div className="error-box">{error}</div> : null}
            {paretoError ? <div className="error-box">{paretoError}</div> : null}
          </section>

          <Results
            result={result}
            paretoResult={paretoResult}
            shifts={settings.shifts}
            officerShifts={settings.officer_shifts}
            isCalculationBusy={isLoading || isParetoLoading}
            isCompletingConfiguration={isCompletingConfiguration}
            isSavingUserConfiguration={isSavingUserConfiguration}
            configurationComparison={configurationComparison}
            isConfigurationComparisonLoading={isConfigurationComparisonLoading}
            configurationComparisonError={configurationComparisonError}
            targetDemand={effectiveSectorDemand}
            targetDemandLabel={activeDemandLabel}
            whatIfSummary={whatIfSummary}
            onRunShiftWhatIf={(changes) => void runShiftWhatIf(changes)}
            onImportResult={(importedResult) => {
              setResult(importedResult);
              setParetoResult(null);
              setWhatIfSummary(null);
              setPausedPayload(null);
              setTimeLimitDecision(null);
              setContinuationComparison(null);
            }}
            onEditResult={(updater) => {
              setResult((current) => (current ? updater(current) : current));
              setParetoResult(null);
            }}
            onCompleteConfiguration={completeCurrentResult}
            onSaveUserConfiguration={saveCurrentResultAsUserConfiguration}
            onOptimizeLockedRoster={runLockedRosterOptimization}
            onOpenComparison={() => setActiveTab('comparison')}
          />
        </div>
      ) : null}
      {pendingSaveResult ? (
        <SaveConfigurationDialog
          duplicateWarning={configurationComparison?.duplicate_warning ?? null}
          isSaving={isSavingUserConfiguration}
          name={saveConfigurationName}
          result={pendingSaveResult}
          onCancel={cancelSaveUserConfiguration}
          onConfirm={() => void confirmSaveUserConfiguration()}
          onNameChange={setSaveConfigurationName}
        />
      ) : null}
      {isOnboardingOpen ? (
        <ATCConfMakerOnboarding
          onDismiss={rememberOnboardingCompletion}
          onStartTour={startGuidedTour}
        />
      ) : null}
      {guidedTourKind ? (
        <GuidedTour
          kind={guidedTourKind}
          onClose={closeGuidedTour}
          onRequestTab={setActiveTab}
        />
      ) : null}
    </main>
  );
}

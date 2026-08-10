import { useMemo, useState } from 'react';

import {
  cancelCalculationJob,
  getCalculationJob,
  getFutureCalculationJobResult,
  startFutureCalculationJob,
} from './api/calculator';
import {
  groupFutureCoverage,
  quarterIntervalLabel,
  scheduleRowsForGroups,
} from './futureScheduleGrouping';
import { preventNumberInputArrowStep } from './numberInput';
import type { CalculationJobStatus, CalculatorSettings } from './types/calculator';
import type { FutureCalculatorResponse, FutureWorkBlock } from './types/futureCalculator';


const DAY_START_HOUR = 7;
const QUARTERS_PER_HOUR = 4;
const SLOTS_PER_DAY = 96;
const SECTOR_COLUMN_LABELS = ['ALL', 'LOWER', 'UPPER', 'MID', 'HIGH', 'TOP'];
const WORKER_COLORS = [
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
type FutureCalculationMode = 'staff_to_coverage' | 'demand_to_staff';

function clamp(value: number, minimum: number, maximum: number): number {
  if (!Number.isFinite(value)) {
    return minimum;
  }
  return Math.min(maximum, Math.max(minimum, Math.round(value)));
}

function expandHourlyDemand(hourlyDemand: number[]): number[] {
  return Array.from({ length: SLOTS_PER_DAY }, (_, slot) => (
    clamp(hourlyDemand[Math.floor(slot / QUARTERS_PER_HOUR)] ?? 0, 0, 8)
  ));
}

function hourLabel(hourIndex: number): string {
  const hour = (DAY_START_HOUR + hourIndex) % 24;
  return `${String(hour).padStart(2, '0')}:00`;
}

function formatMinutes(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (hours === 0) {
    return `${remainder} min`;
  }
  return remainder === 0 ? `${hours} h` : `${hours} h ${remainder} min`;
}

function blockLabel(block: FutureWorkBlock): string {
  return `${block.start}-${block.end} (${formatMinutes(block.duration_minutes)} / ${formatMinutes(block.required_rest_minutes)} počitka)`;
}

function formatSectorHours(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0$/, '');
}

function workerColor(workerId: string) {
  const index = workerId.split('').reduce((total, character) => total * 26 + character.charCodeAt(0) - 64, 0) - 1;
  return WORKER_COLORS[Math.max(0, index) % WORKER_COLORS.length];
}

function WorkerTag({
  workerId,
  result,
}: {
  workerId: string;
  result: FutureCalculatorResponse;
}) {
  const person = result.people.find((item) => item.id === workerId);
  const color = workerColor(workerId);
  return (
    <span
      className="worker-chip"
      style={{ backgroundColor: color.background, borderColor: color.border, color: color.text }}
      title={person ? `${person.id} · ${person.license} · ${person.shift}` : workerId}
    >
      <span className="worker-letter">{workerId}</span>
      {person ? <span className="worker-meta">{person.license} | {person.shift}</span> : null}
    </span>
  );
}

function isDefaultHiddenQuarter(slot: number): boolean {
  const startHour = (DAY_START_HOUR + Math.floor(slot / QUARTERS_PER_HOUR)) % 24;
  return startHour >= 1 && startHour < 5;
}

function sectorHeadersForMax(maxSectorCount: number): string[] {
  return [
    ...SECTOR_COLUMN_LABELS,
    ...Array.from({ length: Math.max(0, maxSectorCount - 5) }, (_, index) => `EXTRA ${index + 6}`),
  ];
}

function formatFutureScheduleForCopy(result: FutureCalculatorResponse, sectorHeaders: string[]): string {
  return [
    ['Termin', ...sectorHeaders, 'Pavza'].join('\t'),
    ...result.coverage.map((slot) => {
      const sectorsByName = new Map(slot.sectors.map((sector) => [sector.sector_name, sector]));
      return [
        quarterIntervalLabel(slot.slot),
        ...sectorHeaders.map((sectorName) => {
          const sector = sectorsByName.get(sectorName);
          return sector ? `${sector.lower_worker} / ${sector.upper_worker}` : '';
        }),
        slot.resting_workers.join(', '),
      ].join('\t');
    }),
  ].join('\n');
}

function FutureSectorSchedule({
  result,
  maxSectorCount,
}: {
  result: FutureCalculatorResponse;
  maxSectorCount: number;
}) {
  const [showNightHours, setShowNightHours] = useState(false);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set());
  const sectorHeaders = sectorHeadersForMax(maxSectorCount);
  const hiddenNightSlotCount = result.coverage.filter((slot) => isDefaultHiddenQuarter(slot.slot)).length;
  const visibleCoverage = result.coverage.filter((slot) => showNightHours || !isDefaultHiddenQuarter(slot.slot));
  const groupedCoverage = groupFutureCoverage(visibleCoverage);
  const scheduleRows = scheduleRowsForGroups(groupedCoverage, expandedGroups);

  const toggleGroup = (groupKey: string) => {
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(groupKey)) {
        next.delete(groupKey);
      } else {
        next.add(groupKey);
      }
      return next;
    });
  };

  const copySchedule = async () => {
    try {
      await navigator.clipboard.writeText(formatFutureScheduleForCopy(result, sectorHeaders));
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
    window.setTimeout(() => setCopyState('idle'), 1600);
  };

  return (
    <section className="future-schedule-section">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Razpored po sektorjih</p>
          <h2>Kdo dela v kateri četrtini</h2>
        </div>
        <div className="panel-actions">
          {hiddenNightSlotCount > 0 ? (
            <button
              className="secondary-button compact-button schedule-toggle-button"
              onClick={() => setShowNightHours((current) => !current)}
              type="button"
            >
              {showNightHours ? 'Skrij 01:00-05:00' : 'Prikaži 01:00-05:00'}
            </button>
          ) : null}
          <button className="secondary-button compact-button copy-button" onClick={() => void copySchedule()} type="button">
            {copyState === 'copied' ? 'Kopirano' : copyState === 'failed' ? 'Napaka' : 'Kopiraj'}
          </button>
        </div>
      </div>
      <div className="schedule-scroll future-quarter-schedule-scroll" aria-label="15-minutni razpored ljudi po sektorjih">
        <div
          className="schedule-grid"
          style={{ gridTemplateColumns: `76px repeat(${sectorHeaders.length}, minmax(0, 1fr)) minmax(108px, 1.05fr)` }}
        >
          <div className="schedule-cell schedule-head sticky-col">Termin</div>
          {sectorHeaders.map((sectorName) => (
            <div className="schedule-cell schedule-head" key={sectorName}>{sectorName}</div>
          ))}
          <div className="schedule-cell schedule-head break-head">Pavza</div>

          {scheduleRows.flatMap((row) => {
            const slot = row.slot;
            const sectorsByName = new Map(slot.sectors.map((sector) => [sector.sector_name, sector]));
            return [
              <div
                className={`schedule-cell schedule-hour sticky-col${row.groupSize > 1 ? ' future-grouped-time' : ''}${row.expanded ? ' future-expanded-time' : ''}`}
                key={`${row.key}-time`}
              >
                <span className="future-time-content">
                  {row.showToggle ? (
                    <button
                      aria-expanded={row.expanded}
                      aria-label={`${row.expanded ? 'Strni' : 'Razširi'} interval ${row.groupLabel}`}
                      className="future-group-toggle"
                      onClick={() => toggleGroup(row.groupKey)}
                      title={`${row.expanded ? 'Strni' : 'Prikaži'} ${row.groupSize} četrtine`}
                      type="button"
                    >
                      {row.expanded ? '▾' : '▸'}
                    </button>
                  ) : row.expanded ? <span className="future-group-toggle-spacer" /> : null}
                  <span>{row.label}</span>
                </span>
              </div>,
              ...sectorHeaders.map((sectorName) => {
                const sector = sectorsByName.get(sectorName);
                return (
                  <div
                    className={`schedule-cell ${sector ? 'assigned' : 'closed'}`}
                    key={`${row.key}-${sectorName}`}
                  >
                    {sector ? (
                      <span className="worker-pair">
                        <span className="schedule-seat filled-seat"><WorkerTag workerId={sector.lower_worker} result={result} /></span>
                        <span className="schedule-seat filled-seat"><WorkerTag workerId={sector.upper_worker} result={result} /></span>
                      </span>
                    ) : <span className="closed-label">Zaprto</span>}
                  </div>
                );
              }),
              <div className="schedule-cell break-cell" key={`${row.key}-break`}>
                {slot.resting_workers.length > 0 ? (
                  <span className="break-worker-list">
                    {slot.resting_workers.map((workerId) => (
                      <span
                        className="break-person-pill"
                        key={workerId}
                        style={{ backgroundColor: workerColor(workerId).background, borderColor: workerColor(workerId).border }}
                        title={workerId}
                      >
                        {workerId}
                      </span>
                    ))}
                  </span>
                ) : <span className="muted-cell">—</span>}
              </div>,
            ];
          })}
        </div>
      </div>
    </section>
  );
}

function FutureShiftSummary({ result }: { result: FutureCalculatorResponse }) {
  const rows = useMemo(() => {
    const summary = new Map<string, { fl: number; aps: number; acs: number }>();
    result.people.forEach((person) => {
      const row = summary.get(person.shift) ?? { fl: 0, aps: 0, acs: 0 };
      row[person.license.toLowerCase() as 'fl' | 'aps' | 'acs'] += 1;
      summary.set(person.shift, row);
    });
    return Array.from(summary.entries()).sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }));
  }, [result.people]);

  return (
    <div className="future-shift-table-wrap">
      <table className="future-shift-table">
        <thead><tr><th>Izmena</th><th>FL</th><th>APS</th><th>ACS</th><th>Skupaj</th></tr></thead>
        <tbody>
          {rows.map(([shift, counts]) => (
            <tr key={shift}>
              <th>{shift}</th>
              <td>{counts.fl}</td>
              <td>{counts.aps}</td>
              <td>{counts.acs}</td>
              <td>{counts.fl + counts.aps + counts.acs}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function FutureCalculator({
  settings,
  hourlyDemand,
}: {
  settings: CalculatorSettings;
  hourlyDemand: number[];
}) {
  const [calculationMode, setCalculationMode] = useState<FutureCalculationMode>('staff_to_coverage');
  const [totalPeople, setTotalPeople] = useState(28);
  const [flCount, setFlCount] = useState(12);
  const [apsCount, setApsCount] = useState(0);
  const [acsCount, setAcsCount] = useState(16);
  const [demand, setDemand] = useState(() => expandHourlyDemand(hourlyDemand));
  const [minWorkMinutes, setMinWorkMinutes] = useState(60);
  const [maxWorkMinutes, setMaxWorkMinutes] = useState(120);
  const [restRatio, setRestRatio] = useState(50);
  const [quarterHourStarts, setQuarterHourStarts] = useState(true);
  const [timeLimit, setTimeLimit] = useState(60);
  const [result, setResult] = useState<FutureCalculatorResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<CalculationJobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const licenseTotal = flCount + apsCount + acsCount;
  const countDifference = totalPeople - licenseTotal;
  const enabledShiftCount = settings.shifts.filter((shift) => shift.enabled !== false).length;
  const requestedSectorHours = useMemo(
    () => demand.reduce((sum, value) => sum + value, 0) / QUARTERS_PER_HOUR,
    [demand],
  );
  const canCalculate = !isLoading && countDifference === 0 && totalPeople > 0 && enabledShiftCount > 0;
  const restExamples = [120, 90, 60]
    .filter((minutes) => minutes >= minWorkMinutes && minutes <= maxWorkMinutes)
    .map((minutes) => `${formatMinutes(minutes)} → ${formatMinutes(Math.ceil(minutes * restRatio / 100 / 15) * 15)}`)
    .join(' · ');

  const updateDemand = (slot: number, value: number) => {
    setDemand((current) => current.map((item, index) => (index === slot ? clamp(value, 0, 8) : item)));
    setResult(null);
  };

  const runCalculation = async () => {
    if (!canCalculate) {
      return;
    }
    setIsLoading(true);
    setIsCancelling(false);
    setJobStatus(null);
    setError(null);
    try {
      const payload = {
        calculation_mode: calculationMode,
        total_people: totalPeople,
        fl_count: flCount,
        aps_count: apsCount,
        acs_count: acsCount,
        requested_sector_counts: demand,
        shifts: settings.shifts,
        min_continuous_work_minutes: minWorkMinutes,
        max_continuous_work_minutes: maxWorkMinutes,
        rest_ratio_percent: restRatio,
        allow_quarter_hour_shift_starts: quarterHourStarts,
        time_limit_seconds: timeLimit,
      } as const;
      const job = await startFutureCalculationJob(payload);
      setActiveJobId(job.job_id);
      let loadedResultVersion = 0;
      let currentStatus: CalculationJobStatus;
      while (true) {
        currentStatus = await getCalculationJob(job.job_id);
        setJobStatus(currentStatus);
        if (currentStatus.best_result_available && currentStatus.best_result_version > loadedResultVersion) {
          const preview = await getFutureCalculationJobResult(job.job_id);
          setResult(preview);
          loadedResultVersion = currentStatus.best_result_version;
        }
        if (currentStatus.status === 'finished') {
          break;
        }
        if (currentStatus.status === 'failed') {
          throw new Error(currentStatus.error ?? currentStatus.message);
        }
        await new Promise((resolve) => window.setTimeout(resolve, 750));
      }
      if (loadedResultVersion < currentStatus.best_result_version || !currentStatus.best_result_available) {
        const response = await getFutureCalculationJobResult(job.job_id);
        setResult(response);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Napaka pri 15-minutnem izračunu.');
    } finally {
      setIsLoading(false);
      setIsCancelling(false);
      setActiveJobId(null);
    }
  };

  const cancelFutureCalculation = async () => {
    if (!activeJobId || isCancelling) {
      return;
    }
    setIsCancelling(true);
    try {
      const status = await cancelCalculationJob(activeJobId);
      setJobStatus(status);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Preklic 15-minutnega izračuna ni uspel.');
      setIsCancelling(false);
    }
  };

  return (
    <div className={`future-workspace${result ? ' has-result' : ''}`}>
      <section className="panel future-controls">
        <div className="future-section-heading">
          <div>
            <p className="eyebrow">15-minutni model</p>
            <h2>Futuristični ATCConfMaker*</h2>
          </div>
          <div className="future-model-badge">96 terminov</div>
        </div>

        <div className="mode-switch future-mode-switch">
          <button
            className={calculationMode === 'staff_to_coverage' ? 'active' : ''}
            onClick={() => setCalculationMode('staff_to_coverage')}
            type="button"
          >
            1. Število ljudi
          </button>
          <button
            className={calculationMode === 'demand_to_staff' ? 'active' : ''}
            onClick={() => setCalculationMode('demand_to_staff')}
            type="button"
          >
            2. Odprtost sektorjev
          </button>
        </div>

        <div className="future-control-grid">
          <label className="field">
            <span>{calculationMode === 'demand_to_staff' ? 'Največ razpoložljivih' : 'Skupaj kontrolorjev'}</span>
            <input
              min="1"
              max="80"
              type="number"
              value={totalPeople}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => setTotalPeople(clamp(Number(event.target.value), 1, 80))}
            />
          </label>
          <label className="field">
            <span>FL</span>
            <input
              min="0"
              max="80"
              type="number"
              value={flCount}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => setFlCount(clamp(Number(event.target.value), 0, 80))}
            />
          </label>
          <label className="field">
            <span>APS</span>
            <input
              min="0"
              max="80"
              type="number"
              value={apsCount}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => setApsCount(clamp(Number(event.target.value), 0, 80))}
            />
          </label>
          <label className="field">
            <span>ACS</span>
            <input
              min="0"
              max="80"
              type="number"
              value={acsCount}
              onKeyDown={preventNumberInputArrowStep}
              onChange={(event) => setAcsCount(clamp(Number(event.target.value), 0, 80))}
            />
          </label>
          <label className="field">
            <span>Najmanj dela v kosu</span>
            <select value={minWorkMinutes} onChange={(event) => setMinWorkMinutes(Number(event.target.value))}>
              {[15, 30, 45, 60, 75, 90, 105, 120]
                .filter((minutes) => minutes <= maxWorkMinutes)
                .map((minutes) => (
                  <option key={minutes} value={minutes}>{formatMinutes(minutes)}</option>
                ))}
            </select>
          </label>
          <label className="field">
            <span>Največ dela v kosu</span>
            <select value={maxWorkMinutes} onChange={(event) => setMaxWorkMinutes(Number(event.target.value))}>
              {[60, 75, 90, 105, 120, 135, 150, 165, 180, 195, 210, 225, 240]
                .filter((minutes) => minutes >= minWorkMinutes)
                .map((minutes) => (
                  <option key={minutes} value={minutes}>{formatMinutes(minutes)}</option>
                ))}
            </select>
          </label>
          <label className="field future-rest-field">
            <span>Počitek po bloku: {restRatio} %</span>
            <input
              min="0"
              max="100"
              step="5"
              type="range"
              value={restRatio}
              onChange={(event) => setRestRatio(Number(event.target.value))}
            />
            <small>{restExamples || 'Počitek ni zahtevan.'}</small>
          </label>
          <label className="field">
            <span>Čas izračuna</span>
            <select value={timeLimit} onChange={(event) => setTimeLimit(Number(event.target.value))}>
              {[15, 30, 60, 120, 300, 600].map((seconds) => (
                <option key={seconds} value={seconds}>{seconds < 60 ? `${seconds} s` : `${seconds / 60} min`}</option>
              ))}
            </select>
          </label>
        </div>

        <label className="future-toggle-row">
          <input
            checked={quarterHourStarts}
            type="checkbox"
            onChange={(event) => setQuarterHourStarts(event.target.checked)}
          />
          <span>Prihodi izmen na :00, :15, :30 in :45</span>
        </label>

        {countDifference !== 0 ? (
          <div className="future-validation" role="alert">
            FL + APS + ACS je {licenseTotal}; do skupnega števila {totalPeople} je razlika {countDifference > 0 ? '+' : ''}{countDifference}.
          </div>
        ) : null}
        {enabledShiftCount === 0 ? <div className="future-validation" role="alert">V pravilih ni omogočene nobene izmene.</div> : null}
      </section>

      <section className="panel future-demand-panel">
        <div className="future-section-heading">
          <div>
            <p className="eyebrow">Želena odprtost</p>
            <h2>{calculationMode === 'demand_to_staff' ? 'Želena odprtost po 15 minutah' : 'Sektorji po četrtinah'}</h2>
          </div>
          <div className="future-demand-actions">
            <button
              className="secondary-button compact-button"
              type="button"
              onClick={() => {
                setDemand(expandHourlyDemand(hourlyDemand));
                setResult(null);
              }}
            >
              Prevzemi urni profil
            </button>
            {calculationMode === 'demand_to_staff' ? (
              <button
                className="secondary-button compact-button"
                type="button"
                onClick={() => {
                  setDemand(Array.from({ length: SLOTS_PER_DAY }, () => settings.max_sectors_per_hour));
                  setResult(null);
                }}
              >
                Vse na max
              </button>
            ) : null}
          </div>
        </div>
        <div className="future-demand-summary">
          <span>Želja</span>
          <strong>{formatSectorHours(requestedSectorHours)} SH</strong>
        </div>
        {calculationMode === 'demand_to_staff' ? (
          <div className="future-sector-matrix-wrap">
            <table className="future-sector-matrix">
              <thead>
                <tr>
                  <th>Termin</th>
                  {Array.from({ length: settings.max_sectors_per_hour }, (_, sectorIndex) => (
                    <th key={sectorIndex}>S{sectorIndex + 1}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: SLOTS_PER_DAY }, (_, slot) => (
                  <tr key={slot}>
                    <th>{quarterIntervalLabel(slot)}</th>
                    {Array.from({ length: settings.max_sectors_per_hour }, (_, sectorIndex) => {
                      const sectorCount = sectorIndex + 1;
                      const active = demand[slot] >= sectorCount;
                      return (
                        <td key={sectorCount}>
                          <button
                            aria-label={`${quarterIntervalLabel(slot)}: ${sectorCount} sektorjev`}
                            className={active ? 'active' : ''}
                            type="button"
                            onClick={() => updateDemand(slot, demand[slot] === sectorCount ? sectorCount - 1 : sectorCount)}
                          >
                            {active ? '✓' : ''}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="future-quarter-table-wrap">
            <table className="future-quarter-table">
              <thead>
                <tr>
                  <th>Ura</th>
                  <th>:00</th>
                  <th>:15</th>
                  <th>:30</th>
                  <th>:45</th>
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 24 }, (_, hourIndex) => (
                  <tr key={hourIndex}>
                    <th>{hourLabel(hourIndex)}</th>
                    {Array.from({ length: QUARTERS_PER_HOUR }, (_, quarter) => {
                      const slot = hourIndex * QUARTERS_PER_HOUR + quarter;
                      return (
                        <td key={slot}>
                          <input
                            aria-label={`${hourLabel(hourIndex)} + ${quarter * 15} min`}
                            min="0"
                            max="8"
                            type="number"
                            value={demand[slot]}
                            onKeyDown={preventNumberInputArrowStep}
                            onChange={(event) => updateDemand(slot, Number(event.target.value))}
                          />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="future-run-actions">
          <button className="primary-button future-calculate-button" disabled={!canCalculate} onClick={() => void runCalculation()} type="button">
            {isLoading
              ? 'Računam 15-minutni model ...'
              : calculationMode === 'demand_to_staff'
                ? 'Poišči najmanjšo ekipo'
                : 'Izračunaj največ SH'}
          </button>
          {isLoading ? (
            <button
              className="cancel-button compact-button"
              disabled={isCancelling}
              onClick={() => void cancelFutureCalculation()}
              type="button"
            >
              {isCancelling ? 'Prekinjam ...' : 'Prekini'}
            </button>
          ) : null}
        </div>
        {isLoading && jobStatus ? (
          <div className="future-job-status" role="status">
            <strong>{jobStatus.message}</strong>
            <span>
              {jobStatus.best_result_available && jobStatus.best_max_sector_hours !== null
                ? `${formatSectorHours(jobStatus.best_max_sector_hours)} SH · `
                : ''}
              {jobStatus.elapsed_seconds.toFixed(1)} s
            </span>
          </div>
        ) : null}
        {error ? <div className="error-box" role="alert">{error}</div> : null}
      </section>

      {result ? (
        <section className="future-results" aria-live="polite">
          <div className="future-result-heading">
            <div>
              <p className="eyebrow">{isLoading ? 'Sprotni predogled' : 'Rezultat 15-minutnega modela'}</p>
              <h2>
                {isLoading
                  ? 'Najboljša rešitev doslej'
                  : result.feasible
                  ? 'Želja je pokrita'
                  : result.solver_status === 'UNKNOWN'
                    ? 'Prekinjeno pred prvo rešitvijo'
                    : 'Najboljša dosežena pokritost'}
              </h2>
            </div>
            <span className={`future-status ${result.feasible ? 'complete' : 'partial'}`}>
              {isLoading ? 'RAČUNAM' : result.solver_status}
            </span>
          </div>

          <div className="future-metrics">
            <div><span>Pokrito</span><strong>{formatSectorHours(result.covered_sector_hours)} / {formatSectorHours(result.requested_sector_hours)} SH</strong></div>
            <div><span>Manjka</span><strong>{formatSectorHours(result.missing_sector_hours)} SH</strong></div>
            <div>
              <span>{result.calculation_mode === 'demand_to_staff' ? 'Potrebni / na voljo' : 'Aktivni ljudje'}</span>
              <strong>{result.active_people} / {result.available_people}</strong>
            </div>
            <div><span>Kontrolorske ure</span><strong>{formatSectorHours(result.controller_hours)}</strong></div>
            <div><span>Vrzel do meje</span><strong>{result.solver_gap_quarter_slots === null ? 'ni znana' : `${formatSectorHours(result.solver_gap_quarter_slots / 4)} SH`}</strong></div>
            <div><span>Čas</span><strong>{result.elapsed_seconds.toFixed(2)} s</strong></div>
          </div>

          {result.warnings.map((warning) => <div className="warning-box" key={warning}>{warning}</div>)}

          <FutureSectorSchedule result={result} maxSectorCount={settings.max_sectors_per_hour} />

          <div className="future-result-grid">
            <div className="future-result-section">
              <h3>Sestava izmen</h3>
              <FutureShiftSummary result={result} />
              <h3>Pokritost po četrtinah</h3>
              <div className="future-coverage-table-wrap">
                <table className="future-coverage-table">
                  <thead>
                    <tr><th>Ura</th><th>:00</th><th>:15</th><th>:30</th><th>:45</th></tr>
                  </thead>
                  <tbody>
                    {Array.from({ length: 24 }, (_, hourIndex) => (
                      <tr key={hourIndex}>
                        <th>{hourLabel(hourIndex)}</th>
                        {result.coverage.slice(hourIndex * 4, hourIndex * 4 + 4).map((slot) => (
                          <td
                            className={slot.open_sectors >= slot.requested_sectors ? 'covered' : 'shortfall'}
                            key={slot.slot}
                            title={slot.workers.join(', ')}
                          >
                            {slot.open_sectors}/{slot.requested_sectors}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="future-result-section">
              <h3>Delovni bloki kontrolorjev</h3>
              <div className="future-people-table-wrap">
                <table className="future-people-table">
                  <thead><tr><th>Kontrolor</th><th>Licenca</th><th>Izmena</th><th>Delo</th><th>Bloki / počitek</th></tr></thead>
                  <tbody>
                    {result.people.map((person) => (
                      <tr key={person.id}>
                        <th>{person.id}</th>
                        <td>{person.license}</td>
                        <td>{person.shift}</td>
                        <td>{formatMinutes(person.worked_minutes)}</td>
                        <td>{person.blocks.map(blockLabel).join(' · ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}

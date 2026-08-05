import { useEffect, useMemo, useState } from 'react';

import {
  calculateAirportSchedule,
  getAirportDefinitions,
} from './api/calculator';
import { preventNumberInputArrowStep } from './numberInput';
import type {
  AirportCalculatorResponse,
  AirportCode,
  AirportDefinition,
  AirportPersonResult,
  AirportScheduleVariant,
  AirportSlotResult,
  AirportTimeBlock,
} from './types/airportCalculator';


const AIRPORTS: Array<{ code: AirportCode; short: string }> = [
  { code: 'BRN', short: 'B' },
  { code: 'MBX', short: 'M' },
  { code: 'POW', short: 'P' },
  { code: 'CEK', short: 'C' },
];

const OPENING_DEFAULTS: Record<AirportCode, { start: string; end: string; allDay: boolean }> = {
  BRN: { start: '00:00', end: '00:00', allDay: true },
  MBX: { start: '06:45', end: '21:15', allDay: false },
  POW: { start: '07:45', end: '20:15', allDay: false },
  CEK: { start: '07:45', end: '23:00', allDay: false },
};

type AirportCalculationMode = 'opening' | 'selected_shifts';

function formatMinutes(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (hours === 0) {
    return `${remainder} min`;
  }
  return remainder === 0 ? `${hours} h` : `${hours} h ${remainder} min`;
}

function blockLabel(block: AirportTimeBlock): string {
  return `${block.start}-${block.end}`;
}

function personShiftLabel(person: AirportPersonResult): string {
  return person.shift_segments.map(blockLabel).join(' · ');
}

function shiftDefinitionLabel(shift: AirportDefinition['shifts'][number]): string {
  if (shift.break_start && shift.break_end) {
    return `${shift.start}-${shift.break_start} · ${shift.break_end}-${shift.end}`;
  }
  return `${shift.start}-${shift.end}`;
}

function shiftComposition(people: AirportPersonResult[]): string {
  const counts = new Map<string, number>();
  people.forEach((person) => {
    counts.set(person.shift, (counts.get(person.shift) ?? 0) + 1);
  });
  return [...counts.entries()]
    .map(([shift, count]) => `${count} × ${shift}`)
    .join(' · ');
}

interface AirportScheduleDisplay {
  opening_start: string;
  continuous_24_hours: boolean;
  coverage: AirportSlotResult[];
  people: AirportPersonResult[];
}

function openingCoverage(schedule: AirportScheduleDisplay): AirportSlotResult[] {
  const startSlot = schedule.continuous_24_hours
    ? 0
    : schedule.coverage.find((slot) => slot.start === schedule.opening_start)?.slot ?? 0;
  return schedule.coverage
    .filter((slot) => slot.is_open)
    .sort((left, right) => (
      (left.slot - startSlot + 96) % 96
      - (right.slot - startSlot + 96) % 96
    ));
}

function missingIntervals(slots: AirportSlotResult[]): string[] {
  const missing = slots.filter((slot) => !slot.is_covered);
  if (missing.length === 0) {
    return [];
  }
  const intervals: Array<{ start: string; end: string; previousSlot: number }> = [];
  missing.forEach((slot) => {
    const previous = intervals.at(-1);
    if (previous && (slot.slot - previous.previousSlot + 96) % 96 === 1) {
      previous.end = slot.end;
      previous.previousSlot = slot.slot;
      return;
    }
    intervals.push({ start: slot.start, end: slot.end, previousSlot: slot.slot });
  });
  return intervals.map((interval) => `${interval.start}-${interval.end}`);
}

function personState(
  person: AirportPersonResult,
  slot: number,
): 'controller' | 'preparation' | 'assistant' | 'break' | 'outside' {
  if (person.controller_slots.includes(slot)) {
    return 'controller';
  }
  if (person.preparation_slots.includes(slot)) {
    return 'preparation';
  }
  if (person.assistant_slots.includes(slot)) {
    return 'assistant';
  }
  if (person.presence_slots.includes(slot)) {
    return 'break';
  }
  return 'outside';
}

function AirportTimeline({ schedule }: { schedule: AirportScheduleDisplay }) {
  const slots = useMemo(() => openingCoverage(schedule), [schedule]);
  if (slots.length === 0) {
    return null;
  }
  const columnTemplate = `minmax(88px, 150px) repeat(${slots.length}, minmax(0, 1fr))`;

  return (
    <section className="airport-timeline-section">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Operativni trak</p>
          <h2>Delo in pavze po osebah</h2>
        </div>
        <div className="airport-legend" aria-label="Legenda razporeda">
          <span><i className="airport-swatch controller" />Na poziciji</span>
          <span><i className="airport-swatch preparation" />Priprava / asistenca</span>
          <span><i className="airport-swatch assistant" />Asistent / standby</span>
          <span><i className="airport-swatch break" />Pavza</span>
          <span><i className="airport-swatch outside" />Izven izmene</span>
        </div>
      </div>

      <div className="airport-timeline-scroll">
        <div className="airport-timeline-grid" style={{ gridTemplateColumns: columnTemplate }}>
          <div className="airport-timeline-label airport-timeline-head sticky-col">Čas</div>
          {slots.map((slot, index) => (
            <div
              className={`airport-time-head${slot.start.endsWith(':00') || index === 0 ? ' labelled' : ''}`}
              key={`head-${slot.slot}`}
              title={`${slot.start}-${slot.end}`}
            >
              {slot.start.endsWith(':00') || index === 0 ? slot.start : ''}
            </div>
          ))}

          {schedule.people.flatMap((person) => [
            <div className="airport-timeline-label person-label sticky-col" key={`${person.id}-label`}>
              <strong>{person.id}</strong>
              <small>{person.shift} · {personShiftLabel(person)}</small>
            </div>,
            ...slots.map((slot) => {
              const state = personState(person, slot.slot);
              const stateLabel = state === 'controller'
                ? 'na poziciji'
                : state === 'preparation'
                  ? 'priprava / asistenca'
                : state === 'assistant'
                  ? 'asistent / standby'
                : state === 'break'
                  ? 'pavza'
                  : 'izven izmene';
              return (
                <div
                  className={`airport-timeline-cell person ${state}`}
                  key={`${person.id}-${slot.slot}`}
                  title={`${person.id}, ${slot.start}-${slot.end}: ${stateLabel}`}
                />
              );
            }),
          ])}
        </div>
      </div>
    </section>
  );
}

function AirportPeopleTable({ people }: { people: AirportPersonResult[] }) {
  return (
    <div className="airport-people-table-wrap">
      <table className="airport-people-table">
        <thead>
          <tr>
            <th>Oseba</th>
            <th>Izmena</th>
            <th>Operativno delo</th>
            <th>Na poziciji</th>
            <th>Priprava</th>
            <th>Operativni bloki</th>
            <th>Pavza v hiši</th>
          </tr>
        </thead>
        <tbody>
          {people.map((person) => (
            <tr key={person.id}>
              <th>{person.id}</th>
              <td><strong>{person.shift}</strong> · {personShiftLabel(person)}</td>
              <td>{formatMinutes(person.duty_minutes)}</td>
              <td>{formatMinutes(person.controller_minutes)}</td>
              <td>{person.preparation_slots.map((slot) => `${String(Math.floor(slot / 4)).padStart(2, '0')}:${String((slot % 4) * 15).padStart(2, '0')}`).join(' · ')}</td>
              <td>{person.duty_blocks.map(blockLabel).join(' · ')}</td>
              <td>{person.break_blocks.map(blockLabel).join(' · ') || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AirportCalculator() {
  const [definitions, setDefinitions] = useState<AirportDefinition[]>([]);
  const [airport, setAirport] = useState<AirportCode>('BRN');
  const [calculationMode, setCalculationMode] = useState<AirportCalculationMode>('opening');
  const [totalPeople, setTotalPeople] = useState(8);
  const [openingStart, setOpeningStart] = useState(OPENING_DEFAULTS.BRN.start);
  const [openingEnd, setOpeningEnd] = useState(OPENING_DEFAULTS.BRN.end);
  const [continuous24Hours, setContinuous24Hours] = useState(OPENING_DEFAULTS.BRN.allDay);
  const [avoidSplitShifts, setAvoidSplitShifts] = useState(true);
  const [exploreOpeningExtension, setExploreOpeningExtension] = useState(true);
  const [selectedShiftCounts, setSelectedShiftCounts] = useState<Record<string, number>>({});
  const [selectedSchedule, setSelectedSchedule] = useState<'requested' | 'extended'>('requested');
  const [result, setResult] = useState<AirportCalculatorResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getAirportDefinitions()
      .then(setDefinitions)
      .catch((caught) => {
        setError(caught instanceof Error ? caught.message : 'Kataloga letaliških izmen ni bilo mogoče naložiti.');
      });
  }, []);

  const definition = definitions.find((item) => item.code === airport);
  const selectedShiftTotal = Object.values(selectedShiftCounts)
    .reduce((total, count) => total + count, 0);
  const isSelectedShiftResult = result?.calculation_mode === 'selected_shifts';
  const extendedSchedule = result?.extended_variant ?? null;
  const displayedVariant: AirportScheduleVariant | null = (
    selectedSchedule === 'extended' ? extendedSchedule : null
  );
  const displayedSchedule: AirportScheduleDisplay | null = result
    ? {
        opening_start: displayedVariant?.opening_start ?? result.opening_start,
        continuous_24_hours: result.continuous_24_hours,
        coverage: displayedVariant?.coverage ?? result.coverage,
        people: displayedVariant?.people ?? result.people,
      }
    : null;
  const visibleMissingIntervals = displayedSchedule
    ? missingIntervals(openingCoverage(displayedSchedule))
    : [];
  const canCalculate = !isLoading
    && (
      calculationMode === 'selected_shifts'
        ? selectedShiftTotal > 0
        : totalPeople > 0
          && openingStart !== ''
          && openingEnd !== ''
          && (continuous24Hours || openingStart !== openingEnd)
    );

  const selectAirport = (code: AirportCode) => {
    const defaults = OPENING_DEFAULTS[code];
    setAirport(code);
    setOpeningStart(defaults.start);
    setOpeningEnd(defaults.end);
    setContinuous24Hours(defaults.allDay);
    setSelectedShiftCounts({});
    setSelectedSchedule('requested');
    setResult(null);
    setError(null);
  };

  const selectCalculationMode = (mode: AirportCalculationMode) => {
    setCalculationMode(mode);
    setSelectedSchedule('requested');
    setResult(null);
    setError(null);
  };

  const changeShiftCount = (code: string, change: number) => {
    setSelectedShiftCounts((current) => {
      const currentTotal = Object.values(current)
        .reduce((total, count) => total + count, 0);
      const nextCount = Math.max(0, (current[code] ?? 0) + change);
      if (change > 0 && currentTotal >= 40) {
        return current;
      }
      const next = { ...current };
      if (nextCount === 0) {
        delete next[code];
      } else {
        next[code] = nextCount;
      }
      return next;
    });
    setResult(null);
    setError(null);
  };

  const runCalculation = async () => {
    if (!canCalculate) {
      return;
    }
    setIsLoading(true);
    setResult(null);
    setSelectedSchedule('requested');
    setError(null);
    try {
      const response = await calculateAirportSchedule({
        airport,
        total_people: calculationMode === 'selected_shifts' ? selectedShiftTotal : totalPeople,
        opening_start: calculationMode === 'selected_shifts' ? '00:00' : openingStart,
        opening_end: calculationMode === 'selected_shifts' ? '00:15' : openingEnd,
        calculation_mode: calculationMode,
        fixed_shift_counts: calculationMode === 'selected_shifts' ? selectedShiftCounts : {},
        continuous_24_hours: calculationMode === 'opening' && continuous24Hours,
        require_assistant_presence: true,
        avoid_split_shifts: calculationMode === 'opening' && avoidSplitShifts,
        explore_opening_extension: calculationMode === 'opening' && exploreOpeningExtension,
        time_limit_seconds: 15,
      });
      setResult(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Izračun letališke kontrole ni uspel.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className={`airport-workspace${result ? ' has-result' : ''}`}>
      <section className="panel airport-controls">
        <div className="airport-section-heading">
          <div>
            <p className="eyebrow">LKZP</p>
            <h2>LKZP odprtost</h2>
          </div>
          <span className="airport-rule-badge">3 h / 1 h</span>
        </div>

        <div className="airport-selector" aria-label="Letališka kontrola">
          {AIRPORTS.map((item) => (
            <button
              className={airport === item.code ? 'active' : ''}
              key={item.code}
              onClick={() => selectAirport(item.code)}
              type="button"
            >
              <span>{item.short}</span>
              {item.code}
            </button>
          ))}
        </div>

        <div className="airport-calculation-mode" role="group" aria-label="Način izračuna">
          <button
            className={calculationMode === 'opening' ? 'active' : ''}
            onClick={() => selectCalculationMode('opening')}
            type="button"
          >
            Podan odpiralni čas
          </button>
          <button
            className={calculationMode === 'selected_shifts' ? 'active' : ''}
            onClick={() => selectCalculationMode('selected_shifts')}
            type="button"
          >
            Podane izmene
          </button>
        </div>

        {calculationMode === 'opening' ? (
          <>
            <div className="airport-input-grid">
              <label className="field">
                <span>Razpoložljivi kontrolorji</span>
                <input
                  min="1"
                  max="40"
                  type="number"
                  value={totalPeople}
                  onKeyDown={preventNumberInputArrowStep}
                  onChange={(event) => {
                    setTotalPeople(Math.max(1, Math.min(40, Number(event.target.value) || 1)));
                    setResult(null);
                  }}
                />
              </label>
              <label className="field">
                <span>Odprtje</span>
                <input
                  disabled={continuous24Hours}
                  step="900"
                  type="time"
                  value={openingStart}
                  onChange={(event) => {
                    setOpeningStart(event.target.value);
                    setResult(null);
                  }}
                />
              </label>
              <label className="field">
                <span>Zaprtje</span>
                <input
                  disabled={continuous24Hours}
                  step="900"
                  type="time"
                  value={openingEnd}
                  onChange={(event) => {
                    setOpeningEnd(event.target.value);
                    setResult(null);
                  }}
                />
              </label>
            </div>

            <div className="airport-option-list">
              <label className="airport-toggle-row">
                <input
                  checked={continuous24Hours}
                  type="checkbox"
                  onChange={(event) => {
                    setContinuous24Hours(event.target.checked);
                    setResult(null);
                  }}
                />
                <span>Kontrola je odprta 24/7</span>
              </label>
              <label className="airport-toggle-row">
                <input
                  checked={avoidSplitShifts}
                  type="checkbox"
                  onChange={(event) => {
                    setAvoidSplitShifts(event.target.checked);
                    setResult(null);
                  }}
                />
                <span>Ne planiraj deljenih izmen, če je možno</span>
              </label>
              <label className="airport-toggle-row">
                <input
                  checked={exploreOpeningExtension}
                  disabled={continuous24Hours}
                  type="checkbox"
                  onChange={(event) => {
                    setExploreOpeningExtension(event.target.checked);
                    setResult(null);
                  }}
                />
                <span>Preveri možno razširitev odprtosti</span>
              </label>
            </div>
          </>
        ) : (
          <div className="airport-selected-shift-summary">
            <div>
              <span>Izbrane izmene</span>
              <strong>{selectedShiftTotal}</strong>
            </div>
            <p>Na seznamu izmen določi količino za vsakega kontrolorja.</p>
          </div>
        )}
        <div className="airport-fixed-rule">
          <span aria-hidden="true">✓</span>
          <strong>Kontrolor in asistent/standby sta oba v operativnem delu</strong>
        </div>

        <div className="airport-rule-line">
          <strong>1 kontrolor + 1 asistent</strong>
          <span>15 min priprave · največ 3 h operativno · nato najmanj 1 h pavze</span>
        </div>

        <button
          className="primary-button airport-calculate-button"
          disabled={!canCalculate}
          onClick={() => void runCalculation()}
          type="button"
        >
          {isLoading
            ? 'Računam ...'
            : calculationMode === 'selected_shifts'
              ? 'Izračunaj največjo odprtost'
              : 'Razporedi izmene'}
        </button>
        {error ? <div className="error-box" role="alert">{error}</div> : null}
      </section>

      <section className={`panel airport-shift-catalog${calculationMode === 'selected_shifts' ? ' selected-mode' : ''}`}>
        <div className="airport-section-heading">
          <div>
            <p className="eyebrow">
              {calculationMode === 'selected_shifts' ? 'Izberi izmene' : 'Dovoljene izmene'}
            </p>
            <h2>{airport}</h2>
          </div>
          <span className="airport-shift-count">
            {calculationMode === 'selected_shifts'
              ? selectedShiftTotal
              : definition?.shifts.length ?? 0}
          </span>
        </div>
        <div className="airport-shift-list">
          {definition?.shifts.map((shift) => {
            const count = selectedShiftCounts[shift.code] ?? 0;
            return (
              <div className={`airport-shift-row${count > 0 ? ' selected' : ''}`} key={shift.code}>
                <div className="airport-shift-main">
                  <strong>{shift.code}</strong>
                  <span>{shiftDefinitionLabel(shift)}</span>
                </div>
                {calculationMode === 'selected_shifts' ? (
                  <div className="airport-shift-stepper" aria-label={`Količina izmene ${shift.code}`}>
                    <button
                      aria-label={`Odstrani eno izmeno ${shift.code}`}
                      disabled={count === 0}
                      onClick={() => changeShiftCount(shift.code, -1)}
                      type="button"
                    >
                      −
                    </button>
                    <output aria-live="polite">{count}</output>
                    <button
                      aria-label={`Dodaj eno izmeno ${shift.code}`}
                      disabled={selectedShiftTotal >= 40}
                      onClick={() => changeShiftCount(shift.code, 1)}
                      type="button"
                    >
                      +
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </section>

      {result ? (
        <section className="airport-results" aria-live="polite">
          <div className="airport-result-heading">
            <div>
              <p className="eyebrow">
                Rezultat · {result.airport} · {
                  isSelectedShiftResult
                    ? 'izbrane izmene'
                    : displayedVariant
                      ? 'razširjeni čas'
                      : 'moj čas'
                }
              </p>
              <h2>
                {isSelectedShiftResult
                  ? result.feasible
                    ? 'Najdaljša izvedljiva odprtost'
                    : 'Izbrane izmene ne omogočajo odprtja'
                  : result.feasible
                    ? 'Odprtost je v celoti pokrita'
                    : 'Najboljša dosežena razporeditev'}
              </h2>
            </div>
            <span className={`airport-status ${result.feasible ? 'complete' : 'partial'}`}>
              {isSelectedShiftResult
                ? result.feasible ? 'IZRAČUNANO' : 'NI ODPRTOSTI'
                : result.feasible ? 'POKRITO' : 'DELNO'}
            </span>
          </div>

          <div className="airport-metrics">
            {isSelectedShiftResult ? (
              <>
                <div><span>Največja odprtost</span><strong>{formatMinutes(result.covered_minutes)}</strong></div>
                <div><span>Termin</span><strong>{result.opening_start}–{result.opening_end}</strong></div>
              </>
            ) : (
              <>
                <div>
                  <span>Pokritost</span>
                  <strong>
                    {formatMinutes(displayedVariant?.opening_minutes ?? result.covered_minutes)}
                    {' / '}
                    {formatMinutes(displayedVariant?.opening_minutes ?? result.requested_minutes)}
                  </strong>
                </div>
                <div><span>Manjka</span><strong>{formatMinutes(displayedVariant ? 0 : result.missing_minutes)}</strong></div>
              </>
            )}
            <div><span>Uporabljeni ljudje</span><strong>{displayedVariant?.active_people ?? result.active_people} / {result.available_people}</strong></div>
            <div><span>Predaje</span><strong>{displayedVariant?.handovers ?? result.handovers}</strong></div>
            <div><span>Čas izračuna</span><strong>{result.elapsed_seconds.toFixed(2)} s</strong></div>
          </div>

          {isSelectedShiftResult ? (
            <div className="airport-fixed-composition">
              <span>Fiksno izbrane izmene</span>
              <strong>{shiftComposition(result.people)}</strong>
            </div>
          ) : (
            <div className="airport-variant-comparison" role="tablist" aria-label="Primerjava odpiralnega časa">
              <button
                aria-selected={selectedSchedule === 'requested'}
                className={selectedSchedule === 'requested' ? 'active' : ''}
                onClick={() => setSelectedSchedule('requested')}
                role="tab"
                type="button"
              >
                <span>Moj odpiralni čas</span>
                <strong>{result.opening_start}–{result.opening_end}</strong>
                <small>{result.people.map((person) => person.shift).join(' + ')}</small>
              </button>
              {extendedSchedule ? (
                <button
                  aria-selected={selectedSchedule === 'extended'}
                  className={selectedSchedule === 'extended' ? 'active' : ''}
                  onClick={() => setSelectedSchedule('extended')}
                  role="tab"
                  type="button"
                >
                  <span>Razširjeni odpiralni čas</span>
                  <strong>{extendedSchedule.opening_start}–{extendedSchedule.opening_end}</strong>
                  <small>{extendedSchedule.people.map((person) => person.shift).join(' + ')}</small>
                </button>
              ) : null}
            </div>
          )}

          {!isSelectedShiftResult && result.opening_extension && !extendedSchedule ? (
            <div className="airport-no-extension">
              Z izbranimi pogoji dodatna razširitev ni dosežena.
            </div>
          ) : null}

          {result.warnings.map((warning) => (
            <div className="warning-box" key={warning}>{warning}</div>
          ))}
          {visibleMissingIntervals.length > 0 ? (
            <div className="airport-missing-line">
              <strong>Nepokrito:</strong>
              <span>{visibleMissingIntervals.join(' · ')}</span>
            </div>
          ) : null}

          {displayedSchedule ? <AirportTimeline schedule={displayedSchedule} /> : null}
          <section className="airport-people-section">
            <div>
              <p className="eyebrow">Izbrane izmene</p>
              <h2>Razpored kontrolorjev</h2>
            </div>
            <AirportPeopleTable people={displayedSchedule?.people ?? []} />
          </section>
        </section>
      ) : null}
    </div>
  );
}

import { useEffect, useMemo, useState } from 'react';
import { calculateSectorHours, getDefaultSettings } from './api/calculator';
import type { CalculatorRequest, CalculatorResponse, CalculatorSettings, ShiftRule } from './types/calculator';

const fallbackSettings: CalculatorSettings = {
  max_sectors_per_hour: 5,
  max_consecutive_work_hours: 2,
  rest_after_max_consecutive_hours: 1,
  include_required_shift_leaders: true,
  required_night_fl_count: 4,
  shifts: [
    { code: 'A7', start_hour: 7, duration_hours: 7 },
    { code: 'A8', start_hour: 8, duration_hours: 8 },
    { code: 'A9', start_hour: 9, duration_hours: 8 },
    { code: 'A10', start_hour: 10, duration_hours: 8 },
    { code: 'A11', start_hour: 11, duration_hours: 8 },
    { code: 'A12', start_hour: 12, duration_hours: 8 },
    { code: 'A13', start_hour: 13, duration_hours: 8 },
    { code: 'A14', start_hour: 14, duration_hours: 7 },
    { code: 'A15', start_hour: 15, duration_hours: 8 },
    { code: 'A16', start_hour: 16, duration_hours: 8 },
    { code: 'A17', start_hour: 17, duration_hours: 8 },
    { code: 'A21', start_hour: 21, duration_hours: 10 },
  ],
};

type Tab = 'calculator' | 'settings';

const DAY_START = 7;
const HOURS_IN_DAY = 24;

function buildHourLabels(): string[] {
  return Array.from({ length: HOURS_IN_DAY }, (_, index) => {
    const start = (DAY_START + index) % HOURS_IN_DAY;
    const end = (start + 1) % HOURS_IN_DAY;
    return `${start.toString().padStart(2, '0')}:00–${end.toString().padStart(2, '0')}:00`;
  });
}

function createDefaultSectorDemand(maxSectors: number): number[] {
  return Array.from({ length: HOURS_IN_DAY }, () => maxSectors);
}

function clampSectorDemand(demand: number[], maxSectors: number): number[] {
  return Array.from({ length: HOURS_IN_DAY }, (_, index) => clamp(demand[index] ?? maxSectors, 0, maxSectors));
}

const hourLabels = buildHourLabels();

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
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
      <span>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {helper ? <small>{helper}</small> : null}
    </label>
  );
}

function SettingsPanel({
  settings,
  onChange,
  onReset,
}: {
  settings: CalculatorSettings;
  onChange: (settings: CalculatorSettings) => void;
  onReset: () => void;
}) {
  const updateShift = (index: number, patch: Partial<ShiftRule>) => {
    onChange({
      ...settings,
      shifts: settings.shifts.map((shift, shiftIndex) => (shiftIndex === index ? { ...shift, ...patch } : shift)),
    });
  };

  return (
    <section className="panel settings-panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Nastavitve pravil</p>
          <h2>Fiksna pravila kalkulatorja</h2>
        </div>
        <button className="secondary-button" onClick={onReset} type="button">
          Ponastavi privzeto
        </button>
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
          label="Nočna FL zasedba"
          min={0}
          max={10}
          value={settings.required_night_fl_count}
          onChange={(value) => onChange({ ...settings, required_night_fl_count: value })}
          helper="Privzeto: V3 + 3× A21 = 4 FL."
        />
      </div>

      <label className="check-row">
        <input
          type="checkbox"
          checked={settings.include_required_shift_leaders}
          onChange={(event) => onChange({ ...settings, include_required_shift_leaders: event.target.checked })}
        />
        <span>Vedno zahtevaj V1/A7, V2/A14 in V3/A21 kot FL</span>
      </label>

      <div className="table-card">
        <div className="table-title">Izmene</div>
        <div className="responsive-table">
          <table>
            <thead>
              <tr>
                <th>Izmena</th>
                <th>Začetek</th>
                <th>Trajanje</th>
              </tr>
            </thead>
            <tbody>
              {settings.shifts.map((shift, index) => (
                <tr key={shift.code}>
                  <td className="strong">{shift.code}</td>
                  <td>
                    <input
                      className="mini-input"
                      type="number"
                      min={0}
                      max={23}
                      value={shift.start_hour}
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
                      onChange={(event) => updateShift(index, { duration_hours: Number(event.target.value) })}
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
  const sectorHeaders = Array.from({ length: maxSectors }, (_, index) => `Sektor ${index + 1}`);

  const setHourDemand = (hourIndex: number, sectorIndex: number) => {
    const clickedCount = sectorIndex + 1;
    const currentCount = values[hourIndex] ?? 0;
    const nextCount = clickedCount <= currentCount ? sectorIndex : clickedCount;
    onChange(values.map((value, index) => (index === hourIndex ? nextCount : value)));
  };

  return (
    <section className="demand-card">
      <div className="demand-header">
        <div>
          <p className="eyebrow">Faza 2</p>
          <h3>Želena odprtost po urah</h3>
        </div>
        <button className="secondary-button compact-button" onClick={() => onChange(createDefaultSectorDemand(maxSectors))} type="button">
          Vse na max
        </button>
      </div>
      <p className="demand-help">Klikni celice po urah. Označene celice povedo, koliko sektorjev želiš imeti odprtih.</p>
      <div className="demand-scroll">
        <div className="demand-grid" style={{ gridTemplateColumns: `92px repeat(${maxSectors}, minmax(58px, 1fr))` }}>
          <div className="demand-cell demand-head sticky-col">Ura</div>
          {sectorHeaders.map((sector) => (
            <div className="demand-cell demand-head" key={sector}>{sector.replace(' ', '\u00a0')}</div>
          ))}
          {hourLabels.flatMap((hour, hourIndex) => [
            <div className="demand-cell demand-hour sticky-col" key={`${hour}-label`}>{hour}</div>,
            ...sectorHeaders.map((sector, sectorIndex) => {
              const selected = sectorIndex < (values[hourIndex] ?? 0);
              return (
                <button
                  aria-label={`${hour}, ${sector}`}
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

function SectorSchedule({ result }: { result: CalculatorResponse }) {
  const peopleById = useMemo(() => new Map(result.people.map((person) => [person.id, person])), [result.people]);
  const maxSectors = Math.max(...result.hourly_coverage.map((hour) => hour.sector_workers.length), 1);
  const sectorHeaders = Array.from({ length: maxSectors }, (_, index) => `Sektor ${index + 1}`);

  const controllerLabel = (workerId: string) => {
    const person = peopleById.get(workerId);
    return person ? `${person.id}/${person.role ?? person.shift}/${person.license}` : workerId;
  };

  return (
    <section className="panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Razpored po sektorjih</p>
          <h2>Kdo dela v kateri uri</h2>
        </div>
      </div>
      <div className="schedule-scroll" aria-label="Razpored ljudi po sektorjih in urah">
        <div className="schedule-grid" style={{ gridTemplateColumns: `120px repeat(${maxSectors}, minmax(148px, 1fr))` }}>
          <div className="schedule-cell schedule-head sticky-col">Ura</div>
          {sectorHeaders.map((sector) => (
            <div className="schedule-cell schedule-head" key={sector}>{sector}</div>
          ))}
          {result.hourly_coverage.flatMap((hour) => [
            <div className="schedule-cell schedule-hour sticky-col" key={`${hour.hour}-label`}>{hour.hour}</div>,
            ...hour.sector_workers.map((sector, index) => {
              if (!sector) {
                return <div className="schedule-cell closed" key={`${hour.hour}-${index}`}>Zaprto</div>;
              }

              return (
                <div className="schedule-cell assigned" key={`${hour.hour}-${index}`}>
                  <span className="position-line">↓ {controllerLabel(sector.lower_worker)}</span>
                  <span className="position-line">↑ {controllerLabel(sector.upper_worker)}</span>
                </div>
              );
            }),
          ])}
        </div>
      </div>
    </section>
  );
}

function Results({ result }: { result: CalculatorResponse | null }) {
  if (!result) {
    return (
      <section className="panel empty-state">
        <div className="empty-icon">⌁</div>
        <h2>Vnesi podatke in zaženi kalkulator</h2>
        <p>Rezultat bo pokazal maksimalne sektorske ure, sestavo izmen in urni prikaz odprtosti.</p>
      </section>
    );
  }

  if (!result.feasible) {
    return (
      <section className="panel error-state">
        <p className="eyebrow">Konfiguracija ni izvedljiva</p>
        <h2>Premalo obveznih FL</h2>
        <p>Minimalno zahtevanih FL: {result.minimum_required_fl}</p>
        {result.notes.map((note) => (
          <p key={note}>{note}</p>
        ))}
      </section>
    );
  }

  const maxCoverage = Math.max(...result.hourly_coverage.map((hour) => hour.open_sectors), 1);

  return (
    <div className="results-stack">
      <section className="metrics-grid">
        <div className="metric-card accent">
          <span>Maksimalno sektorskih ur</span>
          <strong>{result.max_sector_hours}</strong>
        </div>
        <div className="metric-card">
          <span>Minimalno obveznih FL</span>
          <strong>{result.minimum_required_fl}</strong>
        </div>
        <div className="metric-card">
          <span>Neuporabljeni ljudje</span>
          <strong>{result.unused_people}</strong>
        </div>
      </section>

      {[...result.warnings, ...result.notes].length > 0 ? (
        <section className="panel note-list">
          {result.warnings.map((warning) => (
            <div className="warning" key={warning}>⚠️ {warning}</div>
          ))}
          {result.notes.map((note) => (
            <div key={note}>ℹ️ {note}</div>
          ))}
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Generator</p>
            <h2>Predlagana sestava izmen</h2>
          </div>
        </div>
        <div className="responsive-table">
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
                  <td className="strong">{row.shift}</td>
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

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Odprtost po urah</p>
            <h2>Urni prikaz sektorjev</h2>
          </div>
        </div>
        <div className="coverage-list">
          {result.hourly_coverage.map((hour) => (
            <div className="coverage-row" key={hour.hour}>
              <span className="coverage-hour">{hour.hour}</span>
              <div className="coverage-bar-track">
                <div className="coverage-bar" style={{ width: `${(hour.open_sectors / maxCoverage) * 100}%` }} />
              </div>
              <strong>{hour.open_sectors}</strong>
            </div>
          ))}
        </div>
      </section>

      <SectorSchedule result={result} />

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Navidezni ljudje</p>
            <h2>Ustvarjena konfiguracija A, B, C ...</h2>
          </div>
        </div>
        <div className="people-grid">
          {result.people.map((person) => (
            <div className="person-card" key={person.id}>
              <div>
                <strong>{person.id}</strong>
                <span>{person.role ?? person.shift}</span>
              </div>
              <div className="license-pill">{person.license}</div>
              <small>{person.sector_hours} sektorskih ur</small>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('calculator');
  const [settings, setSettings] = useState<CalculatorSettings>(fallbackSettings);
  const [totalPeople, setTotalPeople] = useState(28);
  const [flCount, setFlCount] = useState(12);
  const [apsCount, setApsCount] = useState(0);
  const [sectorDemand, setSectorDemand] = useState(() => createDefaultSectorDemand(fallbackSettings.max_sectors_per_hour));
  const [includeFmp, setIncludeFmp] = useState(true);
  const [result, setResult] = useState<CalculatorResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDefaultSettings()
      .then(setSettings)
      .catch(() => {
        // Fallback keeps the app usable while the backend is not running.
        setSettings(fallbackSettings);
      });
  }, []);

  const effectiveSectorDemand = useMemo(
    () => clampSectorDemand(sectorDemand, settings.max_sectors_per_hour),
    [sectorDemand, settings.max_sectors_per_hour],
  );

  const acsCount = useMemo(() => Math.max(0, totalPeople - flCount - apsCount), [apsCount, flCount, totalPeople]);

  const updateCounts = (nextTotal: number, nextFl: number, nextAps: number) => {
    const safeTotal = clamp(nextTotal, 1, 80);
    const safeFl = clamp(nextFl, 0, safeTotal);
    const safeAps = clamp(nextAps, 0, safeTotal - safeFl);
    setTotalPeople(safeTotal);
    setFlCount(safeFl);
    setApsCount(safeAps);
  };

  const runCalculation = async () => {
    setIsLoading(true);
    setError(null);
    const payload: CalculatorRequest = {
      total_people: totalPeople,
      fl_count: flCount,
      aps_count: apsCount,
      acs_count: acsCount,
      include_fmp: includeFmp,
      settings,
      requested_sector_counts: effectiveSectorDemand,
    };

    try {
      const response = await calculateSectorHours(payload);
      setResult(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Neznana napaka pri izračunu.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <main className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">KonfMaker</p>
          <h1>Kalkulator sektorskih ur</h1>
          <p>
            Prvi programček za izračun maksimalnih sektorskih ur, obveznih FL vlog in predlagane sestave izmen.
          </p>
        </div>
        <div className="hero-card">
          <span>Program</span>
          <strong>1 / Kalkulator</strong>
        </div>
      </header>

      <nav className="tabs" aria-label="Glavna navigacija">
        <button className={activeTab === 'calculator' ? 'active' : ''} onClick={() => setActiveTab('calculator')} type="button">
          Kalkulator sektorskih ur
        </button>
        <button className={activeTab === 'settings' ? 'active' : ''} onClick={() => setActiveTab('settings')} type="button">
          Nastavitve pravil
        </button>
      </nav>

      {activeTab === 'settings' ? (
        <SettingsPanel settings={settings} onChange={setSettings} onReset={() => setSettings(fallbackSettings)} />
      ) : (
        <div className="workspace">
          <section className="panel form-panel">
            <p className="eyebrow">Vhodni podatki</p>
            <h2>Dnevna sestava ljudi</h2>
            <div className="form-grid">
              <NumberField
                label="Skupaj ljudi"
                min={1}
                max={80}
                value={totalPeople}
                onChange={(value) => updateCounts(value, flCount, apsCount)}
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
                helper="Spodnji kontrolor: APS ali FL."
              />
              <NumberField
                label="ACS licence"
                min={0}
                value={acsCount}
                onChange={(value) => updateCounts(totalPeople, flCount, totalPeople - flCount - value)}
                helper="ACS = skupaj − FL − APS. Zgornji kontrolor: ACS ali FL."
              />
            </div>
            <SectorDemandInput
              maxSectors={settings.max_sectors_per_hour}
              values={effectiveSectorDemand}
              onChange={(values) => setSectorDemand(clampSectorDemand(values, settings.max_sectors_per_hour))}
            />
            <label className="check-row fmp-row">
              <input type="checkbox" checked={includeFmp} onChange={(event) => setIncludeFmp(event.target.checked)} />
              <span>Vključi FMP kot A9/FL. FMP se uporabi na sektorju samo, če je koristno.</span>
            </label>
            {error ? <div className="error-box">{error}</div> : null}
            <button className="primary-button" disabled={isLoading} onClick={runCalculation} type="button">
              {isLoading ? 'Računam ...' : 'Izračunaj maksimalne sektorske ure'}
            </button>
          </section>

          <Results result={result} />
        </div>
      )}
    </main>
  );
}

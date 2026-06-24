import { type ChangeEvent, useMemo, useState } from 'react';
import { exportModelAnalysis, inspectModelWorkbook, runModelAnalysis } from './api/analysis';
import type {
  AnalysisMapping,
  AnalysisMetricSet,
  AnalysisParams,
  AnalysisResult,
  WeekdayCode,
  WorkbookProfile,
} from './types/analysis';

const weekdays: WeekdayCode[] = ['PO', 'TO', 'SR', 'ČE', 'PE', 'SO', 'NE'];
const hiddenForecastHourNumbers = new Set([2, 3, 4, 5]);
const analysisScenarioStorageKey = 'konfmaker.analysisScenarios.v1';
const trafficForecastModes = ['previous_year_growth', 'weighted_history'];
const trafficModeHelp: Record<string, string> = {
  previous_year_growth: 'Za vsak prihodnji dan vzame primerljiv dan iz prejšnjega leta na izbranem listu preletov in ga poveča z rastjo.',
  weighted_history: 'Za vsak prihodnji dan uporabi več preteklih let iz izbranega lista preletov, jih uteži in preračuna z rastjo.',
};

type SectorDemandQueueDraft = {
  label: string;
  values: number[];
};

type ForecastDisplayHour = {
  index: number;
  hour: number;
  label: string;
};

type AnalysisScenario = {
  id: string;
  name: string;
  createdAt: string;
  mapping: AnalysisMapping;
  params: AnalysisParams;
};

const defaultMapping: AnalysisMapping = {
  sector_sheet: '23-26',
  adjusted_sector_sheet: '23-26',
  traffic_sheet: 'PRELETI',
  forecast_traffic_sheet: '',
  first_col: 4,
  last_col: 0,
  year_rows: {
    '2026': 1,
    '2025': 24,
  },
  traffic_header_row: 1,
  traffic_first_row: 2,
  traffic_date_col: 1,
  traffic_weekday_col: 3,
  traffic_flights_col: 4,
};

const defaultParams: AnalysisParams = {
  fit_years: [2025],
  test_year: 2026,
  night_add: 5,
  min_daily_sector_hours: 24,
  max_sectors: 5,
  year_weights: {
    '2025': 1.2,
  },
  thresholds: {
    '1': 0.5,
    '2': 1.4,
    '3': 2.55,
    '4': 3.45,
    '5': 4.5,
  },
  intercept_override: null,
  coefficient_override: null,
  weekday_adjustment_overrides: {},
  weekday_buffers: {},
  optimize_with_cp_sat: true,
  optimize_thresholds: true,
  threshold_search_step: 0.05,
  threshold_search_radius: 0.4,
  lock_manual_coefficients: false,
  lock_intercept: false,
  lock_coefficient: false,
  lock_weekday_adjustments: false,
  lock_thresholds: false,
  cp_sat_time_limit_seconds: 8,
  under_prediction_weight: 1,
  over_prediction_weight: 1,
  traffic_forecast_mode: 'previous_year_growth',
  traffic_source_year: 2025,
  use_actual_target_traffic: true,
  annual_traffic_growth_rates: {
    '2025': 0.111,
  },
  default_traffic_growth: 0,
  planning_safety_margin: 0,
  analog_backtest_enabled: true,
  forecast_start_date: null,
  forecast_end_date: null,
  planning_start_date: null,
  planning_end_date: null,
  fatigue_enabled: false,
  fatigue_lambda: 0,
  fatigue_apply_max: true,
  reference_year: 2025,
  target_weekday_staff: 27,
  target_weekend_staff: 28,
  reference_weekday_staff: 27,
  reference_weekend_staff: 28,
  allowed_density_increase: 0,
  season_start_month: 6,
  season_end_month: 9,
  special_days: ['01-01', '05-01'],
  special_day_buffer: 0,
  special_day_exclude_from_fit: true,
};

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(new Error('Datoteke ni mogoče prebrati.'));
    reader.readAsDataURL(file);
  });
}

function parseYearList(value: string): number[] {
  return normalizeYearList(value
    .split(/[,\s;]+/)
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isInteger(item)));
}

function normalizeYearList(years: number[]): number[] {
  return Array.from(new Set(years.filter((year) => Number.isInteger(year)))).sort((left, right) => left - right);
}

function fitYearsFromMapping(yearRows: Record<string, number>, testYear: number): number[] {
  return normalizeYearList(
    Object.entries(yearRows)
      .map(([year, startRow]) => ({ year: Number(year), startRow }))
      .filter(({ year, startRow }) => Number.isInteger(year) && year < testYear && startRow > 0)
      .map(({ year }) => year),
  );
}

function mappingWithFitYears(
  mapping: AnalysisMapping,
  fitYears: number[],
  detectedYearRows: Record<string, number>,
): AnalysisMapping {
  const yearRows = { ...mapping.year_rows };
  for (const year of fitYears) {
    const key = String(year);
    yearRows[key] = yearRows[key] ?? detectedYearRows[key] ?? 0;
  }
  return { ...mapping, year_rows: yearRows };
}

function toNullableNumber(value: string): number | null {
  if (value.trim() === '') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function optionalNumber(value: string): number | undefined {
  const parsed = toNullableNumber(value);
  return parsed === null ? undefined : parsed;
}

function parseSpecialDayList(value: string): string[] {
  return value
    .split(/[,\n;]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeTrafficForecastMode(mode: string): string {
  return trafficForecastModes.includes(mode) ? mode : 'previous_year_growth';
}

function loadAnalysisScenarios(): AnalysisScenario[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(analysisScenarioStorageKey) ?? '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveAnalysisScenarios(scenarios: AnalysisScenario[]): void {
  window.localStorage.setItem(analysisScenarioStorageKey, JSON.stringify(scenarios));
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return value.toLocaleString('sl-SI', {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  });
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return '—';
  }
  return `${formatNumber(value, 1)} %`;
}

function decimalToPercentInput(value: number): number {
  return Number((value * 100).toFixed(3));
}

function percentInputToDecimal(value: number): number {
  return Number((value / 100).toFixed(6));
}

function buildForecastDisplayHours(): ForecastDisplayHour[] {
  return Array.from({ length: 24 }, (_, index) => {
    const hour = (7 + index) % 24;
    return {
      index,
      hour,
      label: `${hour}:00`,
    };
  }).filter((item) => !hiddenForecastHourNumbers.has(item.hour));
}

function metricValue(metrics: AnalysisMetricSet | null, key: keyof AnalysisMetricSet, digits = 2): string {
  if (!metrics) {
    return '—';
  }
  return formatNumber(metrics[key] as number | null, digits);
}

function NumberInput({
  label,
  value,
  min,
  max,
  step = 1,
  helper,
  onChange,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  helper?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {helper ? <small>{helper}</small> : null}
    </label>
  );
}

function SelectInput({
  label,
  value,
  options,
  helper,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  helper?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option value={option} key={option || '__empty'}>{option || 'Ni izbrano'}</option>
        ))}
      </select>
      {helper ? <small>{helper}</small> : null}
    </label>
  );
}

function CopyTableButton({ rows }: { rows: Array<Array<string | number | null | undefined>> }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');

  const copy = async () => {
    const text = rows.map((row) => row.map((cell) => String(cell ?? '')).join('\t')).join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setState('copied');
    } catch {
      setState('failed');
    }
    window.setTimeout(() => setState('idle'), 1600);
  };

  return (
    <button className="secondary-button compact-button" onClick={copy} type="button">
      {state === 'copied' ? 'Kopirano' : state === 'failed' ? 'Napaka' : 'Kopiraj'}
    </button>
  );
}

function MetricCard({
  label,
  value,
  helper,
  accent = false,
}: {
  label: string;
  value: string;
  helper?: string;
  accent?: boolean;
}) {
  return (
    <div className={`metric-card compact-metric${accent ? ' accent' : ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {helper ? <small>{helper}</small> : null}
    </div>
  );
}

function CoefficientTable({ result }: { result: AnalysisResult }) {
  const hasManualLocks = result.optimization.lock_manual_coefficients
    || result.optimization.lock_intercept
    || result.optimization.lock_coefficient
    || result.optimization.lock_weekday_adjustments
    || result.optimization.lock_thresholds;
  const rows = [
    ['Parameter', 'Uporabljeno v napovedi'],
    ['Intercept', result.used_coefficients.intercept],
    ['Koeficient na prelet', result.used_coefficients.coefficient_per_flight],
    ...weekdays.map((weekday) => [
      `Dan ${weekday}`,
      result.used_coefficients.weekday_adjustments[weekday] ?? 0,
    ]),
    ...['1', '2', '3', '4', '5'].map((sector) => [
      `Meja ${sector}S`,
      result.used_coefficients.thresholds[sector] ?? '',
    ]),
  ];

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Prometna kalibracija</p>
          <h2>Uporabljeni parametri modela</h2>
        </div>
        <CopyTableButton rows={rows} />
      </div>
      <div className="model-mode-note">
        {hasManualLocks
          ? 'Del parametrov je ročno zaklenjen v advanced nastavitvah; prikazane so vrednosti, ki jih model dejansko uporablja v napovedi.'
          : 'Parametri so izbrani samodejno z optimizacijo in uporabljeni v napovedi.'}
      </div>
      <div className="responsive-table">
        <table>
          <thead>
            <tr>
              <th>Parameter</th>
              <th>Uporabljeno v napovedi</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(1).map((row) => (
              <tr key={String(row[0])}>
                <td className="strong">{row[0]}</td>
                <td>{row[1] === '' ? '—' : formatNumber(Number(row[1]), String(row[0]).includes('prelet') ? 6 : 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function WeekdayTable({ result }: { result: AnalysisResult }) {
  const rows = [
    ['Dan', 'Dni', 'Preleti', 'Realno SH', 'Model SH', 'Bias', 'Preleti/SH'],
    ...result.weekday_summary.map((row) => [
      row.weekday,
      row.count,
      row.avg_flights,
      row.avg_actual,
      row.avg_prediction,
      row.bias,
      row.actual_density,
    ]),
  ];

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Dnevi</p>
          <h2>Kje model odstopa</h2>
        </div>
        <CopyTableButton rows={rows} />
      </div>
      <div className="responsive-table">
        <table>
          <thead>
            <tr>
              <th>Dan</th>
              <th>Dni</th>
              <th>Preleti</th>
              <th>Realno SH</th>
              <th>Model SH</th>
              <th>Bias</th>
              <th>Preleti/SH</th>
            </tr>
          </thead>
          <tbody>
            {result.weekday_summary.map((row) => (
              <tr key={row.weekday}>
                <td className="strong">{row.weekday}</td>
                <td>{row.count}</td>
                <td>{formatNumber(row.avg_flights, 1)}</td>
                <td>{formatNumber(row.avg_actual)}</td>
                <td>{formatNumber(row.avg_prediction)}</td>
                <td>{formatNumber(row.bias)}</td>
                <td>{formatNumber(row.actual_density)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function MonthTable({ result }: { result: AnalysisResult }) {
  const rows = [
    ['Mesec', 'Dni', 'Realno skupaj', 'Model skupaj', 'MAE', 'Bias'],
    ...result.monthly_summary.map((row) => [
      row.month,
      row.count,
      row.actual_total,
      row.prediction_total,
      row.mae,
      row.bias,
    ]),
  ];

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Meseci</p>
          <h2>Mesečni fit</h2>
        </div>
        <CopyTableButton rows={rows} />
      </div>
      <div className="responsive-table">
        <table>
          <thead>
            <tr>
              <th>Mesec</th>
              <th>Dni</th>
              <th>Realno skupaj</th>
              <th>Model skupaj</th>
              <th>MAE</th>
              <th>Bias</th>
            </tr>
          </thead>
          <tbody>
            {result.monthly_summary.map((row) => (
              <tr key={row.month}>
                <td className="strong">{row.month}</td>
                <td>{row.count}</td>
                <td>{formatNumber(row.actual_total, 1)}</td>
                <td>{formatNumber(row.prediction_total, 1)}</td>
                <td>{formatNumber(row.mae)}</td>
                <td>{formatNumber(row.bias)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function MissTable({ result }: { result: AnalysisResult }) {
  const rows = [
    ['Datum', 'Dan', 'Preleti', 'Realno', 'Model', 'Napaka', 'Prometni cilj'],
    ...result.top_misses.map((row) => [
      row.date,
      row.weekday,
      row.flights,
      row.actual,
      row.prediction,
      row.error,
      row.traffic_target,
    ]),
  ];

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Največji missi</p>
          <h2>Dnevi z največjim odstopanjem</h2>
        </div>
        <CopyTableButton rows={rows} />
      </div>
      <div className="responsive-table">
        <table>
          <thead>
            <tr>
              <th>Datum</th>
              <th>Dan</th>
              <th>Preleti</th>
              <th>Realno</th>
              <th>Model</th>
              <th>Napaka</th>
              <th>Prometni cilj</th>
            </tr>
          </thead>
          <tbody>
            {result.top_misses.map((row) => (
              <tr key={`${row.date}-${row.error}`}>
                <td className="strong">{row.date}</td>
                <td>{row.weekday}</td>
                <td>{formatNumber(row.flights, 1)}</td>
                <td>{formatNumber(row.actual, 1)}</td>
                <td>{formatNumber(row.prediction, 1)}</td>
                <td>{formatNumber(row.error, 1)}</td>
                <td>{formatNumber(row.traffic_target, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function HourlyErrorTable({ result }: { result: AnalysisResult }) {
  const distributionRows = Object.entries(result.hourly_metrics.error_distribution).map(([error, count]) => [
    error,
    count,
  ]);
  const rows = [['Napaka v sektorjih', 'Število ur'], ...distributionRows];

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Ure</p>
          <h2>Porazdelitev urnih napak</h2>
        </div>
        <CopyTableButton rows={rows} />
      </div>
      <div className="error-distribution">
        {distributionRows.map(([error, count]) => (
          <div className="distribution-row" key={String(error)}>
            <span>{Number(error) > 0 ? `+${error}` : error}</span>
            <div>
              <div
                style={{
                  width: `${Math.max(4, Math.min(100, (Number(count) / Math.max(1, result.hourly_metrics.count)) * 100))}%`,
                }}
              />
            </div>
            <strong>{count}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function ForecastDayPicker({
  result,
  onUseSectorDemand,
}: {
  result: AnalysisResult;
  onUseSectorDemand?: (values: number[], label: string) => void;
}) {
  const [selectedDate, setSelectedDate] = useState(result.forecast_days[0]?.date ?? '');
  const selectedDay = result.forecast_days.find((day) => day.date === selectedDate) ?? result.forecast_days[0] ?? null;
  const thresholdText = selectedDay
    ? Object.entries(selectedDay.thresholds)
      .sort(([left], [right]) => Number(left) - Number(right))
      .map(([sector, value]) => `${sector}S=${formatNumber(value, 3)}`)
      .join(' · ')
    : '';
  const rows = [
    ['Datum', 'Dan', 'Preleti', 'Prometni cilj', 'B', 'k', 'Hibrid SH', 'Fatigue SH', 'Napoved SH', 'Formula'],
    ...result.forecast_days.map((day) => [
      day.date,
      day.weekday,
      day.flights,
      day.traffic_target,
      day.base_profile_sum,
      day.calibration_factor,
      day.hybrid_sector_hours,
      day.fatigue_required_sector_hours,
      day.predicted_sector_hours,
      day.formula,
    ]),
  ];

  if (!selectedDay) {
    return null;
  }

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Dnevna napoved</p>
          <h2>Uporaba v kalkulatorju</h2>
        </div>
        <CopyTableButton rows={rows} />
      </div>
      <div className="forecast-action-row">
        <label className="field">
          <span>Dan za kalkulator</span>
          <select value={selectedDate} onChange={(event) => setSelectedDate(event.target.value)}>
            {result.forecast_days.map((day) => (
              <option value={day.date} key={day.date}>
                {day.date} · {day.weekday} · {formatNumber(day.predicted_sector_hours, 0)} SH
              </option>
            ))}
          </select>
        </label>
        <button
          className="primary-button"
          disabled={!onUseSectorDemand}
          onClick={() => onUseSectorDemand?.(
            selectedDay.hourly_for_calculator,
            `${selectedDay.date} ${selectedDay.weekday}`,
          )}
          type="button"
        >
          Uporabi v kalkulatorju
        </button>
      </div>
      <div className="formula-box">
        <span>Formula za izbrani dan</span>
        <strong>{selectedDay.formula}</strong>
      </div>
      <div className="formula-box">
        <span>Hibrid / obremenitev</span>
        <strong>
          Hibrid {formatNumber(selectedDay.hybrid_sector_hours, 0)} SH · fatigue cilj {formatNumber(selectedDay.fatigue_required_sector_hours, 0)} SH
        </strong>
      </div>
      <div className="formula-grid">
        <div className="formula-box">
          <span>B: vsota zgodovinskega profila</span>
          <strong>{formatNumber(selectedDay.base_profile_sum, 3)} SH podnevi</strong>
        </div>
        <div className="formula-box">
          <span>k: razteg profila</span>
          <strong>{formatNumber(selectedDay.calibration_factor, 5)}</strong>
        </div>
        <div className="formula-box">
          <span>Z: P × k proti pragovom</span>
          <strong>{thresholdText}</strong>
        </div>
      </div>
      <div className="responsive-table explain-hour-table">
        <table>
          <thead>
            <tr>
              <th>Ura</th>
              <th>P profil</th>
              <th>Z</th>
              <th>Hibrid</th>
              <th>Final</th>
              <th>Realno</th>
            </tr>
          </thead>
          <tbody>
            {selectedDay.explain_hours.map((hour) => (
              <tr key={`${selectedDay.date}-${hour.hour}`}>
                <td className="strong">{hour.hour}</td>
                <td>{formatNumber(hour.profile, 3)}</td>
                <td>{formatNumber(hour.z, 3)}</td>
                <td>{hour.hybrid_sector}</td>
                <td>{hour.final_sector}</td>
                <td>{formatNumber(hour.actual_sector, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="hour-chip-row">
        {selectedDay.hourly_for_calculator.map((value, index) => (
          <span key={`${selectedDay.date}-${index}`}>
            {(7 + index) % 24}:00 · {value}
          </span>
        ))}
      </div>
    </section>
  );
}

function ForecastHeatmap({ result }: { result: AnalysisResult }) {
  const displayHours = useMemo(buildForecastDisplayHours, []);
  const rows = useMemo(
    () => [
      ['Datum', 'Dan', 'SH', ...displayHours.map((hour) => hour.label)],
      ...result.forecast_days.map((day) => [
        day.date,
        day.weekday,
        day.predicted_sector_hours,
        ...displayHours.map((hour) => day.hourly_for_calculator[hour.index] ?? ''),
      ]),
    ],
    [displayHours, result.forecast_days],
  );

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Heatmap</p>
          <h2>Napoved odprtosti po urah</h2>
        </div>
        <CopyTableButton rows={rows} />
      </div>
      <div className="heatmap-legend" aria-label="Legenda odprtosti sektorjev">
        {[0, 1, 2, 3, 4, 5].map((level) => (
          <span className={`heatmap-cell heatmap-level-${level}`} key={level}>{level}</span>
        ))}
      </div>
      <div className="forecast-heatmap-scroll">
        <div
          className="forecast-heatmap-grid"
          style={{ gridTemplateColumns: `112px 48px 58px repeat(${displayHours.length}, 34px)` }}
          role="table"
        >
          <div className="heatmap-header sticky-heatmap-col" role="columnheader">Datum</div>
          <div className="heatmap-header" role="columnheader">Dan</div>
          <div className="heatmap-header" role="columnheader">SH</div>
          {displayHours.map((hour) => (
            <div className="heatmap-header" role="columnheader" key={hour.label}>{hour.label.replace(':00', '')}</div>
          ))}
          {result.forecast_days.map((day) => (
            <div className="heatmap-row-fragment" role="row" key={day.date}>
              <div className="heatmap-label sticky-heatmap-col" role="cell">{day.date}</div>
              <div className="heatmap-label" role="cell">{day.weekday}</div>
              <div className="heatmap-label" role="cell">{day.predicted_sector_hours}</div>
              {displayHours.map((hour) => {
                const value = day.hourly_for_calculator[hour.index] ?? 0;
                const level = Math.max(0, Math.min(5, Math.round(value)));
                return (
                  <div
                    className={`heatmap-cell heatmap-level-${level}`}
                    role="cell"
                    title={`${day.date} ${hour.label} · ${value} sektorjev`}
                    key={`${day.date}-${hour.index}`}
                  >
                    {value}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function DailyForecastTable({
  result,
  onUseSectorDemand,
  onQueueSectorDemand,
}: {
  result: AnalysisResult;
  onUseSectorDemand?: (values: number[], label: string) => void;
  onQueueSectorDemand?: (items: SectorDemandQueueDraft[]) => void;
}) {
  const displayHours = useMemo(buildForecastDisplayHours, []);
  const [selectedDate, setSelectedDate] = useState(result.forecast_days[0]?.date ?? '');
  const selectedDay = result.forecast_days.find((day) => day.date === selectedDate) ?? result.forecast_days[0] ?? null;
  const rows = useMemo(
    () => [
      ['Datum', ...result.forecast_days.map((day) => day.date)],
      ['Dan', ...result.forecast_days.map((day) => day.weekday)],
      ['Preleti', ...result.forecast_days.map((day) => day.flights)],
      ['Prometni cilj', ...result.forecast_days.map((day) => day.traffic_target)],
      ['SH', ...result.forecast_days.map((day) => day.predicted_sector_hours)],
      ...displayHours.map((hour) => [
        hour.label,
        ...result.forecast_days.map((day) => day.hourly_for_calculator[hour.index] ?? ''),
      ]),
    ],
    [displayHours, result.forecast_days],
  );
  const allQueueItems = result.forecast_days.map((day) => ({
    label: `${day.date} ${day.weekday} · ${day.predicted_sector_hours} SH`,
    values: day.hourly_for_calculator,
  }));

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Dnevna napoved</p>
          <h2>Odprtost po urah</h2>
        </div>
        <div className="panel-actions">
          <CopyTableButton rows={rows} />
          <button
            className="secondary-button compact-button"
            disabled={!onQueueSectorDemand || allQueueItems.length === 0}
            onClick={() => onQueueSectorDemand?.(allQueueItems)}
            type="button"
          >
            Vse v queue
          </button>
        </div>
      </div>
      <div className="forecast-action-row matrix-action-row">
        <label className="field">
          <span>Dan za kalkulator</span>
          <select value={selectedDate} onChange={(event) => setSelectedDate(event.target.value)}>
            {result.forecast_days.map((day) => (
              <option value={day.date} key={day.date}>
                {day.date} · {day.weekday} · {formatNumber(day.predicted_sector_hours, 0)} SH
              </option>
            ))}
          </select>
        </label>
        <button
          className="primary-button"
          disabled={!onUseSectorDemand || !selectedDay}
          onClick={() => selectedDay && onUseSectorDemand?.(
            selectedDay.hourly_for_calculator,
            `${selectedDay.date} ${selectedDay.weekday}`,
          )}
          type="button"
        >
          Uporabi v kalkulatorju
        </button>
        <button
          className="secondary-button compact-button"
          disabled={!onQueueSectorDemand || !selectedDay}
          onClick={() => selectedDay && onQueueSectorDemand?.([{
            label: `${selectedDay.date} ${selectedDay.weekday} · ${selectedDay.predicted_sector_hours} SH`,
            values: selectedDay.hourly_for_calculator,
          }])}
          type="button"
        >
          Dodaj v queue
        </button>
      </div>
      <div className="heatmap-legend" aria-label="Legenda odprtosti sektorjev">
        {[0, 1, 2, 3, 4, 5].map((level) => (
          <span className={`heatmap-cell heatmap-level-${level}`} key={level}>{level}</span>
        ))}
      </div>
      <div className="responsive-table daily-forecast-table transposed-forecast-table">
        <table>
          <thead>
            <tr>
              <th className="sticky-table-col">Ura / dan</th>
              {result.forecast_days.map((day) => (
                <th className="forecast-date-header" key={day.date}>
                  <span>{day.date}</span>
                  <small>{day.weekday} · {day.predicted_sector_hours} SH</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="strong sticky-table-col">Preleti</td>
              {result.forecast_days.map((day) => (
                <td className="matrix-meta-cell" key={`${day.date}-flights`}>{formatNumber(day.flights, 0)}</td>
              ))}
            </tr>
            <tr>
              <td className="strong sticky-table-col">Cilj SH</td>
              {result.forecast_days.map((day) => (
                <td className="matrix-meta-cell" key={`${day.date}-target`}>{formatNumber(day.traffic_target, 0)}</td>
              ))}
            </tr>
            {displayHours.map((hour) => (
              <tr key={hour.label}>
                <td className="strong sticky-table-col">{hour.label}</td>
                {result.forecast_days.map((day) => {
                  const value = day.hourly_for_calculator[hour.index] ?? 0;
                  return (
                    <td
                      className={`daily-hour-cell heatmap-level-${Math.max(0, Math.min(5, Math.round(value)))}`}
                      title={`${day.date} ${hour.label} · ${value} sektorjev`}
                      key={`${day.date}-${hour.label}`}
                    >
                      {value}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function formatConfigCandidate(candidate: AnalysisResult['operational_blocks'][number]['config_candidates'][number]): string {
  const reserve = candidate.reserve_sector_hours > 0
    ? `+${candidate.reserve_sector_hours}`
    : String(candidate.reserve_sector_hours);
  return `${candidate.name} (${candidate.total_people} ljudi, ${candidate.max_sector_hours} SH, ${reserve})`;
}

function OperationalBlocks({
  result,
  onUseSectorDemand,
  onQueueSectorDemand,
}: {
  result: AnalysisResult;
  onUseSectorDemand?: (values: number[], label: string) => void;
  onQueueSectorDemand?: (items: SectorDemandQueueDraft[]) => void;
}) {
  const blocks = result.operational_blocks ?? [];
  const rows = [
    ['Rank', 'Blok', 'Dni', 'Od', 'Do', 'Profil SH', 'Izbran profil', 'Preleti profila', 'Max preleti', 'Razpon dan SH', 'Povp. dan SH', 'Max dan SH', 'Kandidati'],
    ...blocks.map((block) => [
      block.rank,
      `${block.period} · ${block.day_type}`,
      block.count,
      block.date_start,
      block.date_end,
      block.sector_hours,
      block.representative_date ?? '',
      block.representative_flights ?? '',
      block.max_flights ?? '',
      `${formatNumber(block.min_day_sector_hours, 0)}-${formatNumber(block.max_day_sector_hours, 0)}`,
      block.avg_day_sector_hours,
      block.max_day_sector_hours,
      block.config_candidates.map(formatConfigCandidate).join(' | '),
    ]),
  ];
  const queueItems = blocks.map((block) => ({
    label: block.label,
    values: block.hourly_for_calculator,
  }));

  if (blocks.length === 0) {
    return null;
  }

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Konfiguracije</p>
          <h2>Operativni bloki za nove konfiguracije</h2>
        </div>
        <div className="panel-actions">
          <CopyTableButton rows={rows} />
          <button
            className="secondary-button compact-button"
            disabled={!onQueueSectorDemand || queueItems.length === 0}
            onClick={() => onQueueSectorDemand?.(queueItems)}
            type="button"
          >
            Vse bloke v queue
          </button>
        </div>
      </div>
      <p className="model-mode-note">
        Bloki se najprej ločijo po obdobju in tipu dneva, nato po podobnih dnevnih SH.
        Vsak blok uporabi najzahtevnejši dejanski dnevni profil znotraj skupine, ne urne safe ovojnice.
        Obstoječe konfiguracije so prikazane samo kot referenca; gumb profil pošlje v kalkulator, kjer CP-SAT lahko naredi novo konfiguracijo.
      </p>
      <div className="responsive-table operational-block-table">
        <table>
          <thead>
            <tr>
              <th>Blok</th>
              <th>Dni</th>
              <th>Obdobje</th>
              <th>Profil</th>
              <th>Dnevi SH</th>
              <th>Referenca iz knjižnice</th>
              <th>Akcija</th>
            </tr>
          </thead>
          <tbody>
            {blocks.map((block) => (
              <tr key={`${block.period}-${block.day_type}-${block.representative_date ?? block.rank}`}>
                <td className="strong">
                  {block.period}
                  <small>{block.day_type}</small>
                </td>
                <td>{block.count}</td>
                <td>{block.date_start} – {block.date_end}</td>
                <td className="strong">
                  {block.sector_hours} SH
                  {block.representative_date ? <small>profil {block.representative_date}</small> : null}
                  {block.representative_flights ? (
                    <small>{formatNumber(block.representative_flights, 1)} preletov</small>
                  ) : null}
                </td>
                <td>
                  avg {formatNumber(block.avg_day_sector_hours, 1)}
                  <br />
                  razpon {formatNumber(block.min_day_sector_hours, 0)}-{formatNumber(block.max_day_sector_hours, 0)}
                  <br />
                  max {formatNumber(block.max_day_sector_hours, 0)}
                  {block.max_flights ? (
                    <>
                      <br />
                      max preleti {formatNumber(block.max_flights, 1)}
                    </>
                  ) : null}
                </td>
                <td>
                  {block.config_candidates.length > 0 ? (
                    <div className="config-candidate-list">
                      {block.config_candidates.slice(0, 3).map((candidate) => (
                        <span
                          className={`config-candidate ${candidate.reserve_sector_hours < 0 ? 'short' : ''}`}
                          key={`${block.label}-${candidate.name}`}
                        >
                          {formatConfigCandidate(candidate)}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="muted-text">Ni naložene referenčne knjižnice.</span>
                  )}
                </td>
                <td>
                  <button
                    className="secondary-button compact-button"
                    disabled={!onUseSectorDemand}
                    onClick={() => onUseSectorDemand?.(block.hourly_for_calculator, block.label)}
                    type="button"
                  >
                    Optimiziraj novo
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PatternSuggestions({
  result,
  onUseSectorDemand,
  onQueueSectorDemand,
}: {
  result: AnalysisResult;
  onUseSectorDemand?: (values: number[], label: string) => void;
  onQueueSectorDemand?: (items: SectorDemandQueueDraft[]) => void;
}) {
  const rows = [
    ['Rank', 'Vzorec', 'Dni', 'SH', 'Primeri datumov'],
    ...result.pattern_suggestions.map((pattern) => [
      pattern.rank,
      pattern.label,
      pattern.count,
      pattern.sector_hours,
      pattern.dates.join(', '),
    ]),
  ];
  const topThree = result.pattern_suggestions.slice(0, 3);

  if (result.pattern_suggestions.length === 0) {
    return null;
  }

  return (
    <section className="panel analysis-results-panel">
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">Queue</p>
          <h2>Najpogostejši urni vzorci</h2>
        </div>
        <CopyTableButton rows={rows} />
      </div>
      <div className="pattern-actions">
        <button
          className="secondary-button compact-button"
          disabled={!onQueueSectorDemand || topThree.length === 0}
          onClick={() => onQueueSectorDemand?.(topThree.map((pattern) => ({
            label: pattern.label,
            values: pattern.hourly_for_calculator,
          })))}
          type="button"
        >
          Pošlji top 3 v queue
        </button>
      </div>
      <div className="responsive-table">
        <table>
          <thead>
            <tr>
              <th>Rank</th>
              <th>Vzorec</th>
              <th>Dni</th>
              <th>SH</th>
              <th>Primeri</th>
              <th>Akcija</th>
            </tr>
          </thead>
          <tbody>
            {result.pattern_suggestions.map((pattern) => (
              <tr key={pattern.label}>
                <td className="strong">{pattern.rank}</td>
                <td>{pattern.label}</td>
                <td>{pattern.count}</td>
                <td>{pattern.sector_hours}</td>
                <td>{pattern.dates.join(', ')}</td>
                <td>
                  <button
                    className="secondary-button compact-button"
                    disabled={!onUseSectorDemand}
                    onClick={() => onUseSectorDemand?.(pattern.hourly_for_calculator, pattern.label)}
                    type="button"
                  >
                    Uporabi
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function ModelAnalysis({
  onUseSectorDemand,
  onQueueSectorDemand,
}: {
  onUseSectorDemand?: (values: number[], label: string) => void;
  onQueueSectorDemand?: (items: SectorDemandQueueDraft[]) => void;
}) {
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileBase64, setFileBase64] = useState<string | null>(null);
  const [profile, setProfile] = useState<WorkbookProfile | null>(null);
  const [mapping, setMapping] = useState<AnalysisMapping>(defaultMapping);
  const [params, setParams] = useState<AnalysisParams>(defaultParams);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scenarioName, setScenarioName] = useState('Osnovni scenarij');
  const [analysisScenarios, setAnalysisScenarios] = useState<AnalysisScenario[]>(loadAnalysisScenarios);
  const [mappingYearDraft, setMappingYearDraft] = useState('2024');

  const sheetNames = useMemo(() => profile?.sheets.map((sheet) => sheet.name) ?? [], [profile]);
  const detectedYearRows = useMemo(() => profile?.detected_year_rows ?? {}, [profile]);
  const sheetOptions = useMemo(
    () => Array.from(new Set([
      ...(sheetNames.length > 0 ? sheetNames : []),
      mapping.sector_sheet,
      mapping.traffic_sheet,
      mapping.forecast_traffic_sheet,
    ].filter(Boolean))),
    [mapping.forecast_traffic_sheet, mapping.sector_sheet, mapping.traffic_sheet, sheetNames],
  );
  const mappedYears = useMemo(
    () => Array.from(new Set([
      ...Object.keys(mapping.year_rows),
      ...params.fit_years.map(String),
      String(params.test_year),
    ]))
      .filter((year) => Number.isInteger(Number(year)))
      .sort((left, right) => Number(right) - Number(left)),
    [mapping.year_rows, params.fit_years, params.test_year],
  );
  const weightYears = useMemo(
    () => Array.from(new Set(params.fit_years.map(String))).sort((left, right) => Number(right) - Number(left)),
    [params.fit_years],
  );
  const trafficGrowthYears = useMemo(
    () => Array.from(new Set([...params.fit_years, params.test_year - 1].filter((year) => year < params.test_year).map(String)))
      .sort((left, right) => Number(left) - Number(right)),
    [params.fit_years, params.test_year],
  );
  const fitYearsText = params.fit_years.join(', ');

  const inspectFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    setIsLoading(true);
    setError(null);
    setResult(null);
    try {
      const encoded = await fileToBase64(file);
      const workbookProfile = await inspectModelWorkbook({
        file_name: file.name,
        file_base64: encoded,
        mapping,
      });
      setFileName(file.name);
      setFileBase64(encoded);
      setProfile(workbookProfile);
      const suggestedParams = {
        ...workbookProfile.suggested_params,
        traffic_forecast_mode: normalizeTrafficForecastMode(workbookProfile.suggested_params.traffic_forecast_mode),
      };
      const suggestedFitYears = fitYearsFromMapping(workbookProfile.suggested_mapping.year_rows, suggestedParams.test_year);
      setMapping(workbookProfile.suggested_mapping);
      setParams({
        ...suggestedParams,
        fit_years: suggestedFitYears.length > 0 ? suggestedFitYears : suggestedParams.fit_years,
      });
      const nextYearDraft = Math.min(...(suggestedFitYears.length > 0 ? suggestedFitYears : [suggestedParams.test_year - 1])) - 1;
      setMappingYearDraft(String(nextYearDraft));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Napaka pri branju Excela.');
    } finally {
      setIsLoading(false);
      event.target.value = '';
    }
  };

  const runAnalysis = async () => {
    if (!fileBase64 || !fileName) {
      setError('Najprej naloži Excel datoteko.');
      return;
    }
    setIsLoading(true);
    setError(null);
    const runParams: AnalysisParams = {
      ...params,
      traffic_forecast_mode: normalizeTrafficForecastMode(params.traffic_forecast_mode),
    };
    try {
      const response = await runModelAnalysis({
        file_name: fileName,
        file_base64: fileBase64,
        mapping,
        params: runParams,
      });
      setResult(response);
      setMapping(response.mapping);
      setParams(runParams);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Analiza ni uspela.');
    } finally {
      setIsLoading(false);
    }
  };

  const exportAnalysis = async () => {
    if (!fileBase64 || !fileName) {
      setError('Najprej naloži Excel datoteko.');
      return;
    }
    setIsExporting(true);
    setError(null);
    const exportParams: AnalysisParams = {
      ...params,
      traffic_forecast_mode: normalizeTrafficForecastMode(params.traffic_forecast_mode),
    };
    try {
      const blob = await exportModelAnalysis({
        file_name: fileName,
        file_base64: fileBase64,
        mapping,
        params: exportParams,
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const safeName = fileName.replace(/\.[^.]+$/, '').replace(/[^\w.-]+/g, '_');
      link.href = url;
      link.download = `${safeName || 'konfmaker'}_model_analysis.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Izvoz ni uspel.');
    } finally {
      setIsExporting(false);
    }
  };

  const updateYearRow = (year: string, value: number) => {
    const nextMapping = {
      ...mapping,
      year_rows: {
        ...mapping.year_rows,
        [year]: value,
      },
    };
    setMapping(nextMapping);
    const yearNumber = Number(year);
    if (Number.isInteger(yearNumber) && yearNumber < params.test_year) {
      setParams({
        ...params,
        fit_years: value > 0
          ? normalizeYearList([...params.fit_years, yearNumber])
          : params.fit_years.filter((fitYear) => fitYear !== yearNumber),
      });
    }
  };

  const addMappingYear = () => {
    const year = Number(mappingYearDraft.trim());
    if (!Number.isInteger(year) || year < 2000 || year > 2100) {
      setError('Vnesi veljavno leto za mapiranje.');
      return;
    }
    setError(null);
    const key = String(year);
    const startRow = mapping.year_rows[key] ?? detectedYearRows[key] ?? 0;
    const nextMapping = {
      ...mapping,
      year_rows: {
        ...mapping.year_rows,
        [key]: startRow,
      },
    };
    setMapping({
      ...nextMapping,
    });
    if (year < params.test_year && startRow > 0) {
      setParams({ ...params, fit_years: normalizeYearList([...params.fit_years, year]) });
    }
    setMappingYearDraft(String(year - 1));
  };

  const removeMappingYear = (year: string) => {
    const nextRows = { ...mapping.year_rows };
    delete nextRows[year];
    setMapping({ ...mapping, year_rows: nextRows });
    const yearNumber = Number(year);
    if (Number.isInteger(yearNumber)) {
      setParams({ ...params, fit_years: params.fit_years.filter((fitYear) => fitYear !== yearNumber) });
    }
  };

  const toggleFitYear = (year: string, checked: boolean) => {
    const yearNumber = Number(year);
    if (!Number.isInteger(yearNumber)) {
      return;
    }
    setParams({
      ...params,
      fit_years: checked
        ? normalizeYearList([...params.fit_years, yearNumber])
        : params.fit_years.filter((fitYear) => fitYear !== yearNumber),
    });
  };

  const updateWeight = (year: string, value: number) => {
    setParams({
      ...params,
      year_weights: {
        ...params.year_weights,
        [year]: value,
      },
    });
  };

  const updateTrafficGrowth = (year: string, value: number) => {
    setParams({
      ...params,
      annual_traffic_growth_rates: {
        ...params.annual_traffic_growth_rates,
        [year]: value,
      },
    });
  };

  const updateThreshold = (sector: string, value: number) => {
    setParams({
      ...params,
      thresholds: {
        ...params.thresholds,
        [sector]: value,
      },
    });
  };

  const updateWeekdayBuffer = (weekday: WeekdayCode, value: number) => {
    setParams({
      ...params,
      weekday_buffers: {
        ...params.weekday_buffers,
        [weekday]: value,
      },
    });
  };

  const updateWeekdayOverride = (weekday: WeekdayCode, value: number | undefined) => {
    const nextOverrides = { ...params.weekday_adjustment_overrides };
    if (value === undefined) {
      delete nextOverrides[weekday];
    } else {
      nextOverrides[weekday] = value;
    }
    setParams({
      ...params,
      weekday_adjustment_overrides: nextOverrides,
    });
  };

  const saveScenario = () => {
    const name = scenarioName.trim() || `Scenarij ${analysisScenarios.length + 1}`;
    const scenario: AnalysisScenario = {
      id: `${Date.now()}`,
      name,
      createdAt: new Date().toISOString(),
      mapping,
      params,
    };
    const nextScenarios = [scenario, ...analysisScenarios.filter((item) => item.name !== name)].slice(0, 12);
    setAnalysisScenarios(nextScenarios);
    saveAnalysisScenarios(nextScenarios);
  };

  const loadScenario = (scenarioId: string) => {
    const scenario = analysisScenarios.find((item) => item.id === scenarioId);
    if (!scenario) {
      return;
    }
    setMapping({ ...defaultMapping, ...scenario.mapping });
    setParams({
      ...defaultParams,
      ...scenario.params,
      year_weights: { ...defaultParams.year_weights, ...scenario.params.year_weights },
      thresholds: { ...defaultParams.thresholds, ...scenario.params.thresholds },
      weekday_adjustment_overrides: { ...scenario.params.weekday_adjustment_overrides },
      weekday_buffers: { ...defaultParams.weekday_buffers, ...scenario.params.weekday_buffers },
      annual_traffic_growth_rates: { ...defaultParams.annual_traffic_growth_rates, ...scenario.params.annual_traffic_growth_rates },
      special_days: scenario.params.special_days ?? defaultParams.special_days,
    });
    setScenarioName(scenario.name);
    setResult(null);
  };

  const deleteScenario = (scenarioId: string) => {
    const nextScenarios = analysisScenarios.filter((item) => item.id !== scenarioId);
    setAnalysisScenarios(nextScenarios);
    saveAnalysisScenarios(nextScenarios);
  };

  return (
    <div className="analysis-workspace">
      <section className="panel analysis-control-panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Analiza modela</p>
            <h2>Excel kot vir podatkov</h2>
          </div>
        </div>

        <div className="upload-row">
          <label className="secondary-button compact-button file-import-button">
            Naloži Excel
            <input type="file" accept=".xlsx" onChange={(event) => void inspectFile(event)} />
          </label>
        </div>

        <div className="upload-summary">
          <span>Datoteka</span>
          <strong>{fileName ?? 'Ni naložena'}</strong>
          {profile ? <small>{profile.sheets.length} listov prepoznanih</small> : null}
        </div>

        {profile ? (
          <div className="sheet-pills">
            {profile.sheets.map((sheet) => (
              <span key={sheet.name}>{sheet.name} · {sheet.max_row}×{sheet.max_col}</span>
            ))}
          </div>
        ) : null}

        <details className="analysis-details" open>
          <summary>Mapiranje podatkov</summary>
          <div className="demand-header">
            <div>
              <p className="eyebrow">Mapiranje</p>
              <h3>Kje so podatki v Excelu</h3>
            </div>
          </div>
          <div className="wizard-note">
            Tukaj poveš, kateri list vsebuje urna odprtja sektorjev in kateri list vsebuje dnevne prelete.
            Privzeto je izbrano samo eno zgodovinsko leto nazaj; starejša leta lahko dodaš spodaj in jih po potrebi vključiš v fit.
          </div>
          <div className="settings-grid analysis-grid">
            <SelectInput
              label="List odprtij sektorjev"
              value={mapping.sector_sheet}
              options={sheetOptions.length > 0 ? sheetOptions : [mapping.sector_sheet]}
              helper="Iz tega lista model prebere realna urna odprtja sektorjev in iz njih zgradi zgodovinski urni profil."
              onChange={(value) => setMapping({
                ...mapping,
                sector_sheet: value,
                adjusted_sector_sheet: value,
              })}
            />
            <SelectInput
              label="List preletov"
              value={mapping.traffic_sheet}
              options={sheetOptions.length > 0 ? sheetOptions : [mapping.traffic_sheet]}
              onChange={(value) => setMapping({ ...mapping, traffic_sheet: value })}
            />
            <SelectInput
              label="List napovedi preletov"
              value={mapping.forecast_traffic_sheet}
              options={Array.from(new Set(['', ...sheetOptions]))}
              onChange={(value) => setMapping({ ...mapping, forecast_traffic_sheet: value })}
            />
            <NumberInput
              label="Prvi stolpec dni"
              min={1}
              value={mapping.first_col}
              onChange={(value) => setMapping({ ...mapping, first_col: value })}
            />
            <NumberInput
              label="Zadnji stolpec"
              min={0}
              value={mapping.last_col}
              helper="0 pomeni samodejno do konca lista."
              onChange={(value) => setMapping({ ...mapping, last_col: value })}
            />
            <div className="mapping-years-list special-days-field">
              {mappedYears.map((year) => (
                <div className="year-map-item" key={year}>
                  <NumberInput
                    label={`Začetna vrstica ${year}`}
                    min={0}
                    value={mapping.year_rows[year] ?? 0}
                    helper="0 pomeni, da leto trenutno ni mapirano."
                    onChange={(value) => updateYearRow(year, value)}
                  />
                  <div className="year-map-actions">
                    {Number(year) < params.test_year ? (
                      <label className="year-fit-check">
                        <input
                          type="checkbox"
                          checked={params.fit_years.includes(Number(year))}
                          disabled={(mapping.year_rows[year] ?? 0) <= 0}
                          onChange={(event) => toggleFitYear(year, event.target.checked)}
                        />
                        <span>Uporabi za fit</span>
                      </label>
                    ) : null}
                    {!params.fit_years.includes(Number(year)) && Number(year) !== params.test_year ? (
                      <button className="secondary-button compact-button" onClick={() => removeMappingYear(year)} type="button">
                        Odstrani
                      </button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
            <div className="mapping-year-row special-days-field">
              <label className="field">
                <span>Dodaj leto v mapiranje</span>
                <input
                  inputMode="numeric"
                  value={mappingYearDraft}
                  onChange={(event) => setMappingYearDraft(event.target.value)}
                  placeholder="2024"
                />
                <small>Dodaj preteklo leto, če ga želiš uporabiti kot dodatno zgodovino za sektorski fit.</small>
              </label>
              <button className="secondary-button compact-button" onClick={addMappingYear} type="button">
                Dodaj leto
              </button>
            </div>
            <NumberInput
              label="Preleti: prva vrstica"
              min={1}
              value={mapping.traffic_first_row}
              onChange={(value) => setMapping({ ...mapping, traffic_first_row: value })}
            />
            <NumberInput
              label="Preleti: datum stolpec"
              min={1}
              value={mapping.traffic_date_col}
              onChange={(value) => setMapping({ ...mapping, traffic_date_col: value })}
            />
            <NumberInput
              label="Preleti: dan stolpec"
              min={1}
              value={mapping.traffic_weekday_col}
              onChange={(value) => setMapping({ ...mapping, traffic_weekday_col: value })}
            />
            <NumberInput
              label="Preleti: preleti stolpec"
              min={1}
              value={mapping.traffic_flights_col}
              onChange={(value) => setMapping({ ...mapping, traffic_flights_col: value })}
            />
          </div>
        </details>

        <div className="analysis-section">
          <div className="demand-header">
            <div>
              <p className="eyebrow">Napoved</p>
              <h3>Obdobje in promet</h3>
            </div>
          </div>
          <div className="settings-grid analysis-grid">
            <label className="field">
              <span>Napoved od</span>
              <input
                type="date"
                value={params.forecast_start_date ?? ''}
                onChange={(event) => setParams({ ...params, forecast_start_date: event.target.value || null })}
              />
            </label>
            <label className="field">
              <span>Napoved do</span>
              <input
                type="date"
                value={params.forecast_end_date ?? ''}
                onChange={(event) => setParams({ ...params, forecast_end_date: event.target.value || null })}
              />
            </label>
            <label className="field">
              <span>Zgodovinska leta za fit odprtij</span>
              <input
                value={fitYearsText}
                readOnly
              />
              <small>Samodejno iz mapiranja: ta leta učijo dnevni cilj SH in urni profil sektorskih odprtij.</small>
            </label>
            <NumberInput
              label="Ciljno leto"
              min={2020}
              max={2035}
              value={params.test_year}
              onChange={(value) => setParams({ ...params, test_year: value })}
            />
            <label className="field">
              <span>Napoved preletov</span>
              <select
                value={normalizeTrafficForecastMode(params.traffic_forecast_mode)}
                onChange={(event) => setParams({ ...params, traffic_forecast_mode: event.target.value })}
              >
                <option value="previous_year_growth">Prejšnje leto + rast</option>
                <option value="weighted_history">Več preteklih let + uteži + rast</option>
              </select>
              <small>{trafficModeHelp[normalizeTrafficForecastMode(params.traffic_forecast_mode)]}</small>
            </label>
            <NumberInput
              label="Vir leto"
              min={2020}
              max={2035}
              value={params.traffic_source_year ?? params.test_year - 1}
              helper="Za način prejšnje leto + rast."
              onChange={(value) => setParams({ ...params, traffic_source_year: value })}
            />
            <NumberInput
              label="Privzeta rast %"
              min={-50}
              max={200}
              step={0.1}
              value={decimalToPercentInput(params.default_traffic_growth)}
              helper="Uporabi se, če za leto ni posebne rasti."
              onChange={(value) => setParams({ ...params, default_traffic_growth: percentInputToDecimal(value) })}
            />
            <NumberInput
              label="Planerski safety %"
              min={0}
              max={100}
              step={1}
              value={Math.round(params.planning_safety_margin * 100)}
              helper="10 pomeni 10 % višji končni dnevni seštevek sektorjev."
              onChange={(value) => setParams({ ...params, planning_safety_margin: value / 100 })}
            />
            {trafficGrowthYears.map((year) => (
              <NumberInput
                key={year}
                label={`Rast ${year} → ${Number(year) + 1} %`}
                min={-50}
                max={200}
                step={0.1}
                value={decimalToPercentInput(params.annual_traffic_growth_rates[year] ?? 0)}
                onChange={(value) => updateTrafficGrowth(year, percentInputToDecimal(value))}
              />
            ))}
          </div>
          <label className="check-row">
            <input
              type="checkbox"
              checked={params.use_actual_target_traffic}
              onChange={(event) => setParams({ ...params, use_actual_target_traffic: event.target.checked })}
            />
            <span>Za že pretekli del ciljnega leta uporabi dejanske prelete, če obstajajo</span>
          </label>
        </div>

        <div className="analysis-section">
          <div className="demand-header">
            <div>
              <p className="eyebrow">Obremenitev</p>
              <h3>Referenčna gostota dela</h3>
            </div>
          </div>
          <label className="check-row">
            <input
              type="checkbox"
              checked={params.fatigue_enabled}
              onChange={(event) => setParams({ ...params, fatigue_enabled: event.target.checked })}
            />
            <span>Vključi obremenitveni popravek</span>
          </label>
          <div className="settings-grid analysis-grid">
            <NumberInput
              label="Referenčno leto"
              min={2020}
              max={2035}
              value={params.reference_year}
              onChange={(value) => setParams({ ...params, reference_year: value })}
            />
            <NumberInput
              label="Lambda"
              min={0}
              max={1}
              step={0.05}
              value={params.fatigue_lambda}
              helper="0 brez popravka, 1 polni popravek."
              onChange={(value) => setParams({ ...params, fatigue_lambda: value })}
            />
            <NumberInput
              label="Dovoljen dvig gostote %"
              min={-50}
              max={100}
              step={0.5}
              value={decimalToPercentInput(params.allowed_density_increase)}
              onChange={(value) => setParams({ ...params, allowed_density_increase: percentInputToDecimal(value) })}
            />
          </div>
        </div>

        <details className="analysis-details">
          <summary>Napredno: model, solver in ročni parametri</summary>
          <div className="demand-header">
            <div>
              <p className="eyebrow">Parametri</p>
              <h3>Fit in operativna pretvorba</h3>
            </div>
          </div>
          <div className="settings-grid analysis-grid">
            <label className="field">
              <span>Leta za fit</span>
              <input
                value={fitYearsText}
                onChange={(event) => {
                  const fitYears = parseYearList(event.target.value);
                  setMapping(mappingWithFitYears(mapping, fitYears, detectedYearRows));
                  setParams({ ...params, fit_years: fitYears });
                }}
              />
            </label>
            <NumberInput
              label="Testno leto"
              min={2020}
              max={2035}
              value={params.test_year}
              onChange={(value) => setParams({ ...params, test_year: value })}
            />
            <NumberInput
              label="Nočni dodatek"
              min={0}
              max={24}
              value={params.night_add}
              onChange={(value) => setParams({ ...params, night_add: value })}
            />
            <NumberInput
              label="Minimalne SH/dan"
              min={0}
              max={120}
              value={params.min_daily_sector_hours}
              onChange={(value) => setParams({ ...params, min_daily_sector_hours: value })}
            />
            <NumberInput
              label="Max sektorjev"
              min={1}
              max={8}
              value={params.max_sectors}
              onChange={(value) => setParams({ ...params, max_sectors: value })}
            />
            {weightYears.map((year) => (
              <NumberInput
                key={year}
                label={`Utež ${year}`}
                min={0}
                max={5}
                step={0.1}
                value={params.year_weights[year] ?? 1}
                onChange={(value) => updateWeight(year, value)}
              />
            ))}
            {['1', '2', '3', '4', '5'].map((sector) => (
              <NumberInput
                key={sector}
                label={`Meja ${sector}S`}
                min={0}
                max={8}
                step={0.05}
                value={params.thresholds[sector] ?? Number(sector) - 0.5}
                onChange={(value) => updateThreshold(sector, value)}
              />
            ))}
            <label className="field">
              <span>Ročni intercept</span>
              <input
                type="number"
                step={0.1}
                value={params.intercept_override ?? ''}
                placeholder="auto"
                onChange={(event) => setParams({ ...params, intercept_override: toNullableNumber(event.target.value) })}
              />
            </label>
            <label className="field">
              <span>Ročni koef. na prelet</span>
              <input
                type="number"
                step={0.0001}
                value={params.coefficient_override ?? ''}
                placeholder="auto"
                onChange={(event) => setParams({ ...params, coefficient_override: toNullableNumber(event.target.value) })}
              />
            </label>
            <NumberInput
              label="CP-SAT časovni limit"
              min={1}
              max={120}
              value={params.cp_sat_time_limit_seconds}
              helper="Sekunde za iskanje najboljših koeficientov."
              onChange={(value) => setParams({ ...params, cp_sat_time_limit_seconds: value })}
            />
            <NumberInput
              label="Kazen podcenjevanja"
              min={1}
              max={100}
              value={params.under_prediction_weight}
              helper="Višje pomeni bolj safe fit."
              onChange={(value) => setParams({ ...params, under_prediction_weight: value })}
            />
            <NumberInput
              label="Kazen precenjevanja"
              min={1}
              max={100}
              value={params.over_prediction_weight}
              onChange={(value) => setParams({ ...params, over_prediction_weight: value })}
            />
            <NumberInput
              label="Korak pragov"
              min={0.01}
              max={0.5}
              step={0.01}
              value={params.threshold_search_step}
              helper="Grid korak za iskanje mej sektorjev."
              onChange={(value) => setParams({ ...params, threshold_search_step: value })}
            />
            <NumberInput
              label="Razpon pragov"
              min={0}
              max={2}
              step={0.05}
              value={params.threshold_search_radius}
              helper="Koliko okoli trenutnih mej solver testira."
              onChange={(value) => setParams({ ...params, threshold_search_radius: value })}
            />
            <label className="field">
              <span>Analog od</span>
              <input
                type="date"
                value={params.planning_start_date ?? ''}
                onChange={(event) => setParams({ ...params, planning_start_date: event.target.value || null })}
              />
            </label>
            <label className="field">
              <span>Analog do</span>
              <input
                type="date"
                value={params.planning_end_date ?? ''}
                onChange={(event) => setParams({ ...params, planning_end_date: event.target.value || null })}
              />
            </label>
            <NumberInput
              label="Sezona od"
              min={1}
              max={12}
              value={params.season_start_month}
              onChange={(value) => setParams({ ...params, season_start_month: value })}
            />
            <NumberInput
              label="Sezona do"
              min={1}
              max={12}
              value={params.season_end_month}
              onChange={(value) => setParams({ ...params, season_end_month: value })}
            />
            <NumberInput
              label="Special buffer SH"
              min={-24}
              max={48}
              step={0.5}
              value={params.special_day_buffer}
              helper="Doda se prometnemu cilju T(d) za označene dneve."
              onChange={(value) => setParams({ ...params, special_day_buffer: value })}
            />
            <label className="field special-days-field">
              <span>Posebni dnevi</span>
              <textarea
                value={params.special_days.join('\n')}
                placeholder="01-01&#10;05-01&#10;2026-12-25"
                onChange={(event) => setParams({ ...params, special_days: parseSpecialDayList(event.target.value) })}
              />
              <small>Podpira YYYY-MM-DD ali ponavljajoči MM-DD / DD.MM.</small>
            </label>
          </div>

          <label className="check-row">
            <input
              type="checkbox"
              checked={params.optimize_with_cp_sat}
              onChange={(event) => setParams({ ...params, optimize_with_cp_sat: event.target.checked })}
            />
            <span>Optimiziraj prometne koeficiente s CP-SAT</span>
          </label>

          <label className="check-row">
            <input
              type="checkbox"
              checked={params.optimize_thresholds}
              onChange={(event) => setParams({ ...params, optimize_thresholds: event.target.checked })}
            />
            <span>Optimiziraj pragove sektorjev z večstopenjskim grid iskanjem</span>
          </label>

          <label className="check-row">
            <input
              type="checkbox"
              checked={params.analog_backtest_enabled}
              onChange={(event) => setParams({ ...params, analog_backtest_enabled: event.target.checked })}
            />
            <span>Vključi analogni backtest prejšnjega leta v iskanje pragov</span>
          </label>

          <label className="check-row">
            <input
              type="checkbox"
              checked={params.special_day_exclude_from_fit}
              onChange={(event) => setParams({ ...params, special_day_exclude_from_fit: event.target.checked })}
            />
            <span>Posebne dneve izloči iz fita koeficientov</span>
          </label>

          <label className="check-row">
            <input
              type="checkbox"
              checked={params.lock_manual_coefficients}
              onChange={(event) => setParams({ ...params, lock_manual_coefficients: event.target.checked })}
            />
            <span>Zakleni ročne koeficiente namesto optimizacije</span>
          </label>

          <label className="check-row">
            <input
              type="checkbox"
              checked={params.lock_intercept}
              onChange={(event) => setParams({ ...params, lock_intercept: event.target.checked })}
            />
            <span>Zakleni intercept, ko je ročno vpisan</span>
          </label>

          <label className="check-row">
            <input
              type="checkbox"
              checked={params.lock_thresholds}
              onChange={(event) => setParams({ ...params, lock_thresholds: event.target.checked })}
            />
            <span>Zakleni ročne pragove sektorjev</span>
          </label>

          <div className="weekday-buffer-grid">
            {weekdays.map((weekday) => (
              <label className="field" key={weekday}>
                <span>DAN {weekday}</span>
                <input
                  type="number"
                  step={0.001}
                  value={params.weekday_adjustment_overrides[weekday] ?? ''}
                  placeholder={weekday === 'PO' ? '0' : 'auto'}
                  onChange={(event) => updateWeekdayOverride(weekday, optionalNumber(event.target.value))}
                />
              </label>
            ))}
          </div>
          <div className="weekday-buffer-grid weekday-safe-grid">
            {weekdays.map((weekday) => (
              <label className="field" key={weekday}>
                <span>{weekday} safe +/−</span>
                <input
                  type="number"
                  step={0.25}
                  value={params.weekday_buffers[weekday] ?? 0}
                  onChange={(event) => updateWeekdayBuffer(weekday, Number(event.target.value))}
                />
              </label>
            ))}
          </div>
        </details>

        <div className="analysis-section">
          <div className="demand-header">
            <div>
              <p className="eyebrow">Scenariji</p>
              <h3>Shrani ali naloži nastavitve</h3>
            </div>
          </div>
          <div className="scenario-manager">
            <label className="field">
              <span>Ime scenarija</span>
              <input value={scenarioName} onChange={(event) => setScenarioName(event.target.value)} />
            </label>
            <button className="secondary-button compact-button" onClick={saveScenario} type="button">
              Shrani scenarij
            </button>
          </div>
          {analysisScenarios.length > 0 ? (
            <div className="scenario-list">
              {analysisScenarios.map((scenario) => (
                <div className="scenario-item" key={scenario.id}>
                  <div>
                    <strong>{scenario.name}</strong>
                    <small>{new Date(scenario.createdAt).toLocaleString('sl-SI')}</small>
                  </div>
                  <button className="secondary-button compact-button" onClick={() => loadScenario(scenario.id)} type="button">
                    Naloži
                  </button>
                  <button className="secondary-button compact-button" onClick={() => deleteScenario(scenario.id)} type="button">
                    Izbriši
                  </button>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        {error ? <div className="error-box">{error}</div> : null}
        <div className="analysis-run-actions">
          <button className="primary-button" disabled={isLoading || !fileBase64} onClick={() => void runAnalysis()} type="button">
            {isLoading ? 'Analiziram ...' : 'Zaženi analizo modela'}
          </button>
          <button className="secondary-button compact-button" disabled={isLoading || isExporting || !fileBase64} onClick={() => void exportAnalysis()} type="button">
            {isExporting ? 'Izvažam ...' : 'Izvozi Excel'}
          </button>
        </div>
      </section>

      <div className="analysis-results-stack">
        {result ? (
          <>
            <section className="panel analysis-results-panel">
              <div className="panel-header compact">
                <div>
                  <p className="eyebrow">Rezultat</p>
                  <h2>Preverjen fit modela</h2>
                </div>
              </div>
              <div className="model-mode-note">
                Napake so izračunane na dnevih z realnimi odprtji: obstoječi podatki ciljnega leta in analogno obdobje leto prej. Prihodnji dnevi so samo napoved.
              </div>
              <div className="metrics-grid">
                <MetricCard
                  label="Dnevni MAE"
                  value={metricValue(result.operational_fit_metrics, 'mae')}
                  helper="Povprečna absolutna napaka v sektorskih urah/dan."
                  accent
                />
                <MetricCard
                  label="Dnevni bias"
                  value={metricValue(result.operational_fit_metrics, 'bias')}
                  helper="Pozitivno pomeni bolj safe, negativno podcenjuje."
                />
                <MetricCard
                  label="Ure točno"
                  value={formatPercent(result.hourly_metrics.exact_percent)}
                  helper="Delež ur, kjer je število sektorjev enako realnemu."
                />
                <MetricCard
                  label="Ure ±1 sektor"
                  value={formatPercent(result.hourly_metrics.within_one_percent)}
                  helper="Delež ur v toleranci enega sektorja."
                />
                <MetricCard
                  label="Dni preverjanja"
                  value={String(result.data_counts.checked_days ?? result.operational_fit_metrics.count)}
                  helper={`${result.data_counts.fit_days} dni uporabljenih za fit.`}
                />
                <MetricCard
                  label="Dni v napovedi"
                  value={String(result.data_counts.forecast_days ?? result.data_counts.test_days)}
                  helper={`${result.data_counts.known_target_days ?? 0} dni ima realna odprtja v izbranem obdobju.`}
                />
              </div>
            </section>
            <DailyForecastTable
              result={result}
              onUseSectorDemand={onUseSectorDemand}
              onQueueSectorDemand={onQueueSectorDemand}
            />
            <OperationalBlocks
              result={result}
              onUseSectorDemand={onUseSectorDemand}
              onQueueSectorDemand={onQueueSectorDemand}
            />
            <PatternSuggestions
              result={result}
              onUseSectorDemand={onUseSectorDemand}
              onQueueSectorDemand={onQueueSectorDemand}
            />
            <CoefficientTable result={result} />
            <WeekdayTable result={result} />
            <MonthTable result={result} />
            <HourlyErrorTable result={result} />
            <MissTable result={result} />
          </>
        ) : (
          <section className="panel empty-state">
            <div>
              <div className="empty-icon">↗</div>
              <h2>Analiza še ni zagnana</h2>
              <p>Naloži Excel, preveri mapiranje in zaženi analizo.</p>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

import type {
  CalculationJobStart,
  CalculationJobStatus,
  CalculatorRequest,
  CalculatorResponse,
  CalculatorSettings,
  CompleteConfigurationRequest,
  CompleteConfigurationResult,
  ConfigurationComparisonResult,
  DeleteManualConfigurationResponse,
  ManualConfigurationAudit,
  ManualConfigurationDetail,
  ManualFocusCalibration,
  ManualConfigurationLibrary,
  ManualConfigurationOneDownResult,
  ManualConfigurationOneDownRequest,
  ParetoResponse,
  PatternLibraryProfile,
  SaveUserConfigurationRequest,
  UpdateUserConfigurationRequest,
} from '../types/calculator';
import type { FutureCalculatorRequest, FutureCalculatorResponse } from '../types/futureCalculator';
import type {
  AirportCalculatorRequest,
  AirportCalculatorResponse,
  AirportDefinition,
} from '../types/airportCalculator';

function resolveApiBaseUrl(): string {
  const configuredUrl = import.meta.env.VITE_API_BASE_URL?.trim();
  if (configuredUrl) {
    return configuredUrl.replace(/\/$/, '');
  }

  // Default to the current Vite origin. In development, vite.config.ts proxies
  // /api to FastAPI, so the browser never has to call the Codespaces backend
  // port directly and CORS cannot block the request.
  return '';
}

const API_BASE_URL = resolveApiBaseUrl();

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);

  if (options?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const responseText = await response.text();
    let message = responseText;
    try {
      const payload = JSON.parse(responseText) as { detail?: unknown };
      if (typeof payload.detail === 'string') {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail
          .map((item) => (typeof item === 'object' && item && 'msg' in item ? String(item.msg) : String(item)))
          .join(' ');
      }
    } catch {
      // Non-JSON errors are already suitable for display as plain text.
    }
    if (response.status === 504) {
      throw new Error(
        'API je prekinil zahtevo zaradi časovne omejitve (504). To običajno pomeni, da je bil izračun predolgo v teku, ne nujno da rešitev ne obstaja.',
      );
    }
    throw new Error(message || `API napaka ${response.status}`);
  }

  return response.json() as Promise<T>;
}

async function requestBlob(path: string, options?: RequestInit): Promise<Blob> {
  const headers = new Headers(options?.headers);

  if (options?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const responseText = await response.text();
    let message = responseText;
    try {
      const payload = JSON.parse(responseText) as { detail?: unknown };
      if (typeof payload.detail === 'string') {
        message = payload.detail;
      }
    } catch {
      // Non-JSON errors are already suitable for display as plain text.
    }
    throw new Error(message || `API napaka ${response.status}`);
  }

  return response.blob();
}

export function getDefaultSettings(): Promise<CalculatorSettings> {
  return requestJson<CalculatorSettings>('/api/default-settings');
}

export function calculateSectorHours(payload: CalculatorRequest): Promise<CalculatorResponse> {
  return requestJson<CalculatorResponse>('/api/calculate-sector-hours', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function exportCalculatorWorkbook(
  result: CalculatorResponse,
  name: string,
  targetDemand: number[],
  shifts: CalculatorSettings['shifts'],
): Promise<Blob> {
  return requestBlob('/api/calculator/export', {
    method: 'POST',
    body: JSON.stringify({
      result,
      name,
      target_demand: targetDemand,
      shifts,
    }),
  });
}

export function calculateFutureSectorHours(payload: FutureCalculatorRequest): Promise<FutureCalculatorResponse> {
  return requestJson<FutureCalculatorResponse>('/api/future-calculator', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function getAirportDefinitions(): Promise<AirportDefinition[]> {
  return requestJson<AirportDefinition[]>('/api/airport-calculator/shifts');
}

export function calculateAirportSchedule(payload: AirportCalculatorRequest): Promise<AirportCalculatorResponse> {
  return requestJson<AirportCalculatorResponse>('/api/airport-calculator', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function startFutureCalculationJob(payload: FutureCalculatorRequest): Promise<CalculationJobStart> {
  return requestJson<CalculationJobStart>('/api/jobs/future-calculator', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function getFutureCalculationJobResult(jobId: string): Promise<FutureCalculatorResponse> {
  return requestJson<FutureCalculatorResponse>(`/api/jobs/${encodeURIComponent(jobId)}/result`);
}

export function completeConfiguration(payload: CompleteConfigurationRequest): Promise<CompleteConfigurationResult> {
  return requestJson<CompleteConfigurationResult>('/api/complete-configuration', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function startCalculationJob(payload: CalculatorRequest): Promise<CalculationJobStart> {
  return requestJson<CalculationJobStart>('/api/jobs/calculate-sector-hours', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function startCompleteConfigurationJob(payload: CompleteConfigurationRequest): Promise<CalculationJobStart> {
  return requestJson<CalculationJobStart>('/api/jobs/complete-configuration', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function inspectPatternLibrary(payload: CalculatorRequest): Promise<PatternLibraryProfile> {
  return requestJson<PatternLibraryProfile>('/api/pattern-library/profile', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function regeneratePatternLibrary(payload: CalculatorRequest): Promise<PatternLibraryProfile> {
  return requestJson<PatternLibraryProfile>('/api/pattern-library/regenerate', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function getManualConfigurations(): Promise<ManualConfigurationLibrary> {
  return requestJson<ManualConfigurationLibrary>('/api/manual-configurations');
}

export function getManualConfiguration(id: string | number): Promise<ManualConfigurationDetail> {
  return requestJson<ManualConfigurationDetail>(`/api/manual-configurations/${encodeURIComponent(String(id))}`);
}

function focusNameQuery(names?: string[]): string {
  const selected = (names ?? []).map((name) => name.trim()).filter(Boolean);
  return selected.length > 0 ? `&names=${encodeURIComponent(selected.join(','))}` : '';
}

export function getManualConfigurationFocusAudit(timeLimitSeconds = 3, names?: string[]): Promise<ManualConfigurationAudit> {
  return requestJson<ManualConfigurationAudit>(
    `/api/manual-configurations/focus-audit?time_limit_seconds=${encodeURIComponent(String(timeLimitSeconds))}${focusNameQuery(names)}`,
  );
}

export function calibrateManualConfigurationFocus(
  names: string[],
  timeLimitSeconds = 3,
  applyOnSuccess = true,
): Promise<ManualFocusCalibration> {
  return requestJson<ManualFocusCalibration>('/api/manual-configurations/focus-calibration', {
    method: 'POST',
    body: JSON.stringify({
      names,
      time_limit_seconds: timeLimitSeconds,
      apply_on_success: applyOnSuccess,
    }),
  });
}

export function compareResultToConfigurations(result: CalculatorResponse, limit = 8): Promise<ConfigurationComparisonResult> {
  return requestJson<ConfigurationComparisonResult>('/api/manual-configurations/compare-result', {
    method: 'POST',
    body: JSON.stringify({ result, limit }),
  });
}

export function saveUserConfiguration(payload: SaveUserConfigurationRequest): Promise<ManualConfigurationDetail> {
  return requestJson<ManualConfigurationDetail>('/api/manual-configurations/user', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function updateUserConfiguration(
  id: string | number,
  payload: UpdateUserConfigurationRequest,
): Promise<ManualConfigurationDetail> {
  return requestJson<ManualConfigurationDetail>(
    `/api/manual-configurations/${encodeURIComponent(String(id))}`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
  );
}

export function deleteManualConfiguration(id: string | number): Promise<DeleteManualConfigurationResponse> {
  return requestJson<DeleteManualConfigurationResponse>(
    `/api/manual-configurations/${encodeURIComponent(String(id))}`,
    { method: 'DELETE' },
  );
}

export function runManualConfigurationOneDown(
  id: string | number,
  timeLimitSeconds = 3,
  settings?: ManualConfigurationOneDownRequest['settings'],
): Promise<ManualConfigurationOneDownResult> {
  return requestJson<ManualConfigurationOneDownResult>(
    `/api/manual-configurations/${encodeURIComponent(String(id))}/one-down?time_limit_seconds=${encodeURIComponent(String(timeLimitSeconds))}`,
    {
      method: 'POST',
      body: JSON.stringify({ time_limit_seconds: timeLimitSeconds, settings: settings ?? null }),
    },
  );
}

export function startManualConfigurationOneDownJob(
  id: string | number,
  timeLimitSeconds = 8,
  settings?: ManualConfigurationOneDownRequest['settings'],
): Promise<CalculationJobStart> {
  return requestJson<CalculationJobStart>(
    `/api/jobs/manual-configurations/${encodeURIComponent(String(id))}/one-down?time_limit_seconds=${encodeURIComponent(String(timeLimitSeconds))}`,
    {
      method: 'POST',
      body: JSON.stringify({ time_limit_seconds: timeLimitSeconds, settings: settings ?? null }),
    },
  );
}

export function startParetoJob(payload: CalculatorRequest): Promise<CalculationJobStart> {
  return requestJson<CalculationJobStart>('/api/jobs/pareto-analysis', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function getCalculationJob(jobId: string): Promise<CalculationJobStatus> {
  return requestJson<CalculationJobStatus>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export function getCalculationJobs(): Promise<CalculationJobStatus[]> {
  return requestJson<CalculationJobStatus[]>('/api/jobs');
}

export function getCalculationJobResult(jobId: string): Promise<CalculatorResponse> {
  return requestJson<CalculatorResponse>(`/api/jobs/${encodeURIComponent(jobId)}/result`);
}

export function getParetoJobResult(jobId: string): Promise<ParetoResponse> {
  return requestJson<ParetoResponse>(`/api/jobs/${encodeURIComponent(jobId)}/result`);
}

export function cancelCalculationJob(jobId: string): Promise<CalculationJobStatus> {
  return requestJson<CalculationJobStatus>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  });
}

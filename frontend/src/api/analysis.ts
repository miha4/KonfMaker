import type { AnalysisResult, WorkbookPayload, WorkbookProfile } from '../types/analysis';

function resolveApiBaseUrl(): string {
  const configuredUrl = import.meta.env.VITE_API_BASE_URL?.trim();
  if (configuredUrl) {
    return configuredUrl.replace(/\/$/, '');
  }
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
    const message = await response.text();
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
    const message = await response.text();
    throw new Error(message || `API napaka ${response.status}`);
  }

  return response.blob();
}

export function inspectModelWorkbook(payload: WorkbookPayload): Promise<WorkbookProfile> {
  return requestJson<WorkbookProfile>('/api/model-analysis/profile', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function runModelAnalysis(payload: WorkbookPayload): Promise<AnalysisResult> {
  return requestJson<AnalysisResult>('/api/model-analysis/run', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function exportModelAnalysis(payload: WorkbookPayload): Promise<Blob> {
  return requestBlob('/api/model-analysis/export', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

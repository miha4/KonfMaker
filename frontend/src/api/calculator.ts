import type { CalculatorRequest, CalculatorResponse, CalculatorSettings } from '../types/calculator';

function resolveApiBaseUrl(): string {
  const configuredUrl = import.meta.env.VITE_API_BASE_URL;
  if (configuredUrl) {
    return configuredUrl;
  }

  if (globalThis.location.hostname.endsWith('.app.github.dev')) {
    const backendHost = globalThis.location.hostname.replace(/-\d+\.app\.github\.dev$/, '-8000.app.github.dev');
    return `${globalThis.location.protocol}//${backendHost}`;
  }

  return 'http://localhost:8000';
}

const API_BASE_URL = resolveApiBaseUrl();

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options?.headers ?? {}) },
    ...options,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `API napaka ${response.status}`);
  }

  return response.json() as Promise<T>;
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

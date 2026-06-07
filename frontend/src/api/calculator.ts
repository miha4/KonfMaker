import type { CalculatorRequest, CalculatorResponse, CalculatorSettings } from '../types/calculator';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

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

import type { CalculatorRequest, CalculatorResponse, CalculatorSettings } from '../types/calculator';

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
    const message = await response.text();
    if (response.status === 504) {
      throw new Error(
        'API je prekinil zahtevo zaradi časovne omejitve (504). To običajno pomeni, da je bil izračun predolgo v teku, ne nujno da rešitev ne obstaja.',
      );
    }
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

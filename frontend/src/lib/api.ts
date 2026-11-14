import axios from 'axios';
import { AUTH_EXPIRED_EVENT } from '@/lib/session';

/** Base URL for the versioned API. Defaults to the current origin — in
 *  production the SPA is served by the API itself, so requests are same-origin
 *  (`/api/v1`). Set VITE_BACKEND_URL for a cross-origin dev server, e.g.
 *  http://localhost:8000. */
export const API_BASE = `${import.meta.env.VITE_BACKEND_URL ?? ''}/api/v1`;

/** The browser's IANA timezone (for example "Africa/Accra"), or '' when unknown. */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return '';
  }
}

/** Single axios instance. Auth token is attached from localStorage per request. */
export const api = axios.create({ baseURL: API_BASE });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  // Agents are told today's date in the person's timezone, not the server's.
  const timezone = browserTimezone();
  if (timezone) config.headers['X-Timezone'] = timezone;
  return config;
});

// A signed-in request refused with 401 may mean the sign-in ended (signed
// out on another device, or expired); UserProvider checks and signs out.
api.interceptors.response.use(undefined, (error) => {
  if (axios.isAxiosError(error) && error.response?.status === 401 && error.config?.headers?.Authorization) {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
  return Promise.reject(error);
});

/** Normalize an axios/network error into a human-readable message. */
export function errorMessage(err: unknown, fallback = 'Something went wrong.'): string {
  if (axios.isAxiosError(err)) {
    if (err.response) return (err.response.data as { detail?: string })?.detail || `Request failed (${err.response.status}).`;
    return 'Unable to reach the server. Check your connection and try again.';
  }
  return fallback;
}

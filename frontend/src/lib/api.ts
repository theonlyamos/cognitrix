import axios from 'axios';

/** Base URL for the versioned API, e.g. http://localhost:8000/api/v1 */
export const API_BASE = `${import.meta.env.VITE_BACKEND_URL}/api/v1`;

/** Single axios instance. Auth token is attached from localStorage per request. */
export const api = axios.create({ baseURL: API_BASE });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

/** Normalize an axios/network error into a human-readable message. */
export function errorMessage(err: unknown, fallback = 'Something went wrong.'): string {
  if (axios.isAxiosError(err)) {
    if (err.response) return (err.response.data as { detail?: string })?.detail || `Request failed (${err.response.status}).`;
    return 'Unable to reach the server. Check your connection and try again.';
  }
  return fallback;
}

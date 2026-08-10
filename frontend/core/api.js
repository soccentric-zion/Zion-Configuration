// API client: JSON + CSRF double-submit (spec §4.8).
// The CSRF token is the SHA-256 of the session id, returned by GET /auth/whoami
// as `csrf`. We capture it there and echo it back on every mutation via the
// X-CSRF-Token header (the csrf_guard middleware compares them).

import { clearDirty } from './dirty-guard.js';

let csrfToken = null;
let unauthorizedHandler = null;

export function setCsrf(t) { csrfToken = t; }
export function onUnauthorized(fn) { unauthorizedHandler = fn; }

async function request(method, url, body, isForm, extraHeaders) {
  const headers = { ...extraHeaders };
  if (body != null && !isForm) headers['Content-Type'] = 'application/json';
  if (csrfToken && method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
    headers['X-CSRF-Token'] = csrfToken;
  }
  const opts = { method, headers, credentials: 'same-origin' };
  if (body != null) opts.body = isForm ? body : JSON.stringify(body);
  const res = await fetch(url, opts);
  if (res.status === 401 && !url.endsWith('/auth/whoami')) {
    if (unauthorizedHandler) unauthorizedHandler();
    throw new Error('unauthorized');
  }
  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch (_) { data = text; } }
  if (!res.ok) {
    const msg = (data && data.error) || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  if (method !== 'GET' && method !== 'HEAD') {
    // A mutation just succeeded — whatever made the page "dirty" is now saved.
    clearDirty();
  }
  return data;
}

export const api = {
  get: (url) => request('GET', url, null),
  post: (url, body) => request('POST', url, body),
  put: (url, body, extraHeaders) => request('PUT', url, body, false, extraHeaders),
  del: (url) => request('DELETE', url, null),
  // Raw byte upload (no multipart wrapping — the server reads the body as
  // bytes directly). `file` is a File/Blob; metadata travels in the URL query
  // string (e.g. `?version=...&signature=...`), not the body.
  upload(url, file) {
    return request('POST', url, file, true);
  },
  eventSource: (url) => new EventSource(url),
};

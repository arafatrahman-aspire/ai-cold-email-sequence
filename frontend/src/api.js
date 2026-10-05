// Thin client for the FastAPI service. Requests go to /api/* and the Vite dev
// server forwards them to the backend (see vite.config.js).

// Same-origin only: the session cookie and CSRF check depend on it.
const BASE = '/api'

// Sign-in is through CMS (see App.jsx); the backend keeps an HttpOnly session
// cookie. Changes also need this per-session CSRF token from /auth/me.
let csrfToken = ''

export function setCsrfToken(value) {
  csrfToken = value || ''
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

function describe(detail, fallback) {
  if (!detail) return fallback
  if (typeof detail === 'string') return detail
  // FastAPI validation errors: [{loc: [...], msg: "..."}]
  if (Array.isArray(detail)) {
    return detail
      .map((d) => `${(d.loc || []).filter((p) => p !== 'body').join('.')}: ${d.msg}`)
      .join('; ')
  }
  return JSON.stringify(detail)
}

async function request(method, path, body) {
  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken

  let res
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
    })
  } catch {
    throw new ApiError('Cannot reach the backend. Is it running on port 8080?', 0)
  }

  let data = null
  const text = await res.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (!res.ok) {
    if (res.status === 401) {
      window.dispatchEvent(new Event('session-expired'))
      throw new ApiError('Your session ended. Sign in through CMS to continue.', 401)
    }
    if (res.status === 403 && typeof data?.detail === 'string' && ['access_denied', 'invalid_csrf'].includes(data.detail)) {
      window.dispatchEvent(new Event('session-check'))
      throw new ApiError('Your access could not be confirmed. Reload the page and try again.', 403)
    }
    // The Vite proxy answers an empty 500 (or 502-504) when the backend is down.
    if ((res.status >= 502 && res.status <= 504) || (res.status === 500 && !text)) {
      throw new ApiError('Cannot reach the backend. Is it running on port 8080?', 0)
    }
    throw new ApiError(describe(data?.detail, `Request failed (HTTP ${res.status})`), res.status)
  }
  return data
}

export const api = {
  health: () => request('GET', '/health'),
  stats: () => request('GET', '/stats'),
  settings: () => request('GET', '/settings'),
  putSetting: (key, value) =>
    request('PUT', `/settings/${encodeURIComponent(key)}`, { value }),
  enroll: (leads) => request('POST', '/enroll', { leads }),
  preview: (lead, stepCount) => request('POST', '/preview', { lead, step_count: stepCount }),
  route: (jobTitle) => request('GET', `/route?job_title=${encodeURIComponent(jobTitle)}`),
  suppress: (email, reason) =>
    request(
      'POST',
      `/suppress?email=${encodeURIComponent(email)}&reason=${encodeURIComponent(reason)}`,
    ),
  run: (job) => request('POST', `/run/${job}`),
  replies: (type = 'reply') => request('GET', `/replies?type=${encodeURIComponent(type)}`),
  testAlert: () => request('POST', '/replies/test-alert'),
  sendNow: (leadId) => request('POST', `/send-now${leadId ? `?lead_id=${encodeURIComponent(leadId)}` : ''}`),
  upcoming: (limit = 8) => request('GET', `/upcoming?limit=${limit}`),
  enrollments: (statuses) =>
    request('GET', `/enrollments${statuses ? `?status=${encodeURIComponent(statuses.join(','))}` : ''}`),
  retry: (leadId, overrides) =>
    request('POST', `/enrollments/${encodeURIComponent(leadId)}/retry`, overrides || {}),

  // Reply triage (OUT-05)
  triageStats: () => request('GET', '/triage/stats'),
  triageEvents: (category) =>
    request('GET', `/triage/events${category ? `?category=${encodeURIComponent(category)}` : ''}`),
  retriage: (eventId, category) =>
    request('POST', `/triage/events/${encodeURIComponent(eventId)}/rerun`, category ? { category } : {}),
  drafts: (status) => request('GET', `/triage/drafts?status=${encodeURIComponent(status)}`),
  editDraft: (id, subject, body) => request('PUT', `/triage/drafts/${encodeURIComponent(id)}`, { subject, body }),
  approveDraft: (id) => request('POST', `/triage/drafts/${encodeURIComponent(id)}/approve`),
  rejectDraft: (id) => request('POST', `/triage/drafts/${encodeURIComponent(id)}/reject`),
  meetings: () => request('GET', '/triage/meetings'),
  syncCalendar: () => request('POST', '/run/calendar-sync'),
  snoozed: () => request('GET', '/triage/snoozed'),
  evaluate: () => request('POST', '/triage/evaluate'),
  evaluation: () => request('GET', '/triage/evaluation'),
  calendarStatus: () => request('GET', '/calendar/status'),
  calendarSlots: (tz) => request('GET', `/calendar/slots?timezone_name=${encodeURIComponent(tz)}`),
}

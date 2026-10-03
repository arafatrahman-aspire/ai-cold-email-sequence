import { useCallback, useEffect, useState } from 'react'
import { api, setCsrfToken } from './api.js'
import Dashboard from './components/Dashboard.jsx'
import Enroll from './components/Enroll.jsx'
import Preview from './components/Preview.jsx'
import Settings from './components/Settings.jsx'
import Tools from './components/Tools.jsx'
import Accuracy from './components/triage/Accuracy.jsx'
import Drafts from './components/triage/Drafts.jsx'
import { FollowUps, Meetings } from './components/triage/Meetings.jsx'
import TriageReplies from './components/triage/TriageReplies.jsx'
import TriageSettings from './components/triage/TriageSettings.jsx'
import { Badge } from './components/ui.jsx'

// Two workspaces in one console: OUT-01 (sending the cold sequence) and
// OUT-05 (handling the replies). The hash holds both: #enroll, #triage/drafts.
const WORKSPACES = {
  sequence: {
    label: 'Cold sequence',
    tabs: [
      ['overview', 'Overview'],
      ['enroll', 'Enroll'],
      ['preview', 'Preview'],
      ['settings', 'Settings'],
      ['tools', 'Tools'],
    ],
  },
  triage: {
    label: 'Reply triage',
    tabs: [
      ['replies', 'Replies'],
      ['drafts', 'Drafts'],
      ['meetings', 'Meetings'],
      ['follow-ups', 'Follow-ups'],
      ['accuracy', 'Accuracy'],
      ['settings', 'Settings'],
    ],
  },
}

const THEME_KEY = 'outreach.theme'

function readHash() {
  const hash = window.location.hash.replace('#', '')
  // The old #replies link now lives in the triage workspace.
  if (hash === 'replies') return { workspace: 'triage', tab: 'replies' }
  const [first, second] = hash.split('/')
  if (first === 'triage') {
    const tabs = WORKSPACES.triage.tabs
    return { workspace: 'triage', tab: tabs.some(([t]) => t === second) ? second : tabs[0][0] }
  }
  const tabs = WORKSPACES.sequence.tabs
  return { workspace: 'sequence', tab: tabs.some(([t]) => t === first) ? first : 'overview' }
}

const hrefFor = (workspace, tab) => (workspace === 'triage' ? `#triage/${tab}` : `#${tab}`)

function readTheme() {
  try {
    return localStorage.getItem(THEME_KEY) || 'system'
  } catch {
    return 'system'
  }
}

function HealthPill({ health, error }) {
  if (error) return <Badge tone="critical">Backend offline</Badge>
  if (!health) return <Badge tone="neutral">Connecting…</Badge>
  if (health.database !== 'ok') return <Badge tone="warning">Database unavailable</Badge>
  return <Badge tone="good">Connected</Badge>
}

function login() {
  const next = window.location.pathname + window.location.search + window.location.hash
  window.location.assign(`/api/auth/start?next=${encodeURIComponent(next)}`)
}

// CMS-linked login, same flow as the landing page: an HttpOnly session cookie
// from the backend (service/app/auth.py); signing out here also signs out of
// CMS, and signing out of CMS ends this session.
export default function App() {
  const [signedOut] = useState(() => new URLSearchParams(window.location.search).has('signedout'))
  const [user, setUser] = useState(null)
  const [csrf, setCsrf] = useState('')
  const [message, setMessage] = useState('Checking your session…')
  const [canLogin, setCanLogin] = useState(false)

  useEffect(() => {
    // Leftover from the old API-key prompt; the browser no longer holds a key.
    try { localStorage.removeItem('outreach.apiKey') } catch { /* ignore */ }
    let alive = true
    async function check(redirect, fresh = false) {
      try {
        const res = await fetch(fresh ? '/api/auth/me?fresh=1' : '/api/auth/me', { credentials: 'same-origin', cache: 'no-store' })
        if (!alive) return
        if (res.ok) {
          const data = await res.json()
          if (!alive) return
          setUser(data.user); setCsrfToken(data.csrfToken); setCsrf(data.csrfToken); setCanLogin(false)
          return
        }
        // A background re-check only acts on a definite sign-out, not an outage.
        if (fresh && res.status !== 401 && res.status !== 403) return
        setUser(null); setCsrfToken(''); setCanLogin(res.status === 401)
        if (res.status === 401 && redirect) { login(); return }
        setMessage(res.status === 403 ? 'Your CMS account does not have access to this console.'
          : res.status === 401 ? 'Sign in through CMS to continue.'
          : 'Cannot verify access right now. Please reload to retry.')
      } catch {
        if (alive && !fresh) { setUser(null); setMessage('Cannot reach the backend. Please reload to retry.') }
      }
    }
    if (signedOut) {
      window.history.replaceState(null, '', '/')
      setMessage('You are signed out.'); setCanLogin(true)
    } else check(true)
    const onExpired = () => { setUser(null); setCsrfToken(''); setCanLogin(true); setMessage('Your session ended. Sign in through CMS to continue.') }
    const onCheck = () => { check(false, true) }
    // Returning to this tab (e.g. after signing out in CMS) re-checks at once.
    const onVisible = () => { if (document.visibilityState === 'visible') check(false, true) }
    window.addEventListener('session-expired', onExpired)
    window.addEventListener('session-check', onCheck)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      alive = false
      window.removeEventListener('session-expired', onExpired)
      window.removeEventListener('session-check', onCheck)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [signedOut])

  useEffect(() => {
    if (!user) return
    // Interaction keeps the session alive (30-minute inactivity limit).
    let last = 0
    const activity = () => {
      if (Date.now() - last < 60_000) return
      last = Date.now()
      fetch('/api/auth/activity', { method: 'POST', headers: { 'X-CSRF-Token': csrf }, credentials: 'same-origin' })
        .then((r) => { if (r.status === 401 || r.status === 403) window.dispatchEvent(new Event('session-check')) })
        .catch(() => {})
    }
    window.addEventListener('pointerdown', activity)
    window.addEventListener('keydown', activity)
    return () => { window.removeEventListener('pointerdown', activity); window.removeEventListener('keydown', activity) }
  }, [user, csrf])

  async function logout() {
    try {
      const res = await fetch('/api/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': csrf }, credentials: 'same-origin' })
      if (!res.ok) throw new Error()
      // The server returns the CMS logout URL; CMS signs out and sends us back to /?signedout=1.
      const { redirect } = await res.json()
      setCsrfToken('')
      window.location.assign(typeof redirect === 'string' ? redirect : '/?signedout=1')
    } catch {
      window.alert('Sign out failed. Please retry.')
    }
  }

  if (!user) {
    return (
      <main className="auth-gate">
        <div className="card auth-card">
          <h1>Outreach Console</h1>
          <p role="status" className="muted">{message}</p>
          {canLogin && <button className="btn btn-primary" onClick={login}>Sign in through CMS</button>}
        </div>
      </main>
    )
  }
  return <Console user={user} onLogout={logout} />
}

function Console({ user, onLogout }) {
  const [{ workspace, tab }, setRoute] = useState(readHash)
  const [theme, setTheme] = useState(readTheme)
  const [health, setHealth] = useState(null)
  const [healthError, setHealthError] = useState(null)

  useEffect(() => {
    const onHash = () => setRoute(readHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') delete root.dataset.theme
    else root.dataset.theme = theme
    try {
      localStorage.setItem(THEME_KEY, theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await api.health())
      setHealthError(null)
    } catch (e) {
      setHealthError(e)
    }
  }, [])

  useEffect(() => {
    refreshHealth()
  }, [refreshHealth])

  const nextTheme = { system: 'light', light: 'dark', dark: 'system' }[theme]

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M4 7l8 5.5L20 7M4 7v10h16V7" />
              </svg>
            </span>
            <span className="brand-name">Outreach Console</span>
          </div>
          <div className="workspace-switch" role="tablist" aria-label="Workspace">
            {Object.entries(WORKSPACES).map(([id, w]) => (
              <a key={id} role="tab" aria-selected={workspace === id}
                className={`workspace ${workspace === id ? 'is-active' : ''}`}
                href={hrefFor(id, w.tabs[0][0])}>
                {w.label}
              </a>
            ))}
          </div>
          <div className="topbar-status">
            <HealthPill health={health} error={healthError} />
            {health?.dry_run && (
              <span className="badge badge-info" title="DRY_RUN=true in .env: no email actually leaves">
                Dry run
              </span>
            )}
            <span className="muted" title={`Role: ${user.role}`}>{user.email}</span>
            <button className="btn btn-ghost btn-sm" onClick={onLogout}>
              Sign out
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setTheme(nextTheme)}
              title={`Theme: ${theme} (click for ${nextTheme})`}
            >
              {theme === 'dark' ? 'Dark' : theme === 'light' ? 'Light' : 'Auto'}
            </button>
          </div>
        </div>
        <nav className="tabs" aria-label="Sections">
          <div className="tabs-inner">
            {WORKSPACES[workspace].tabs.map(([id, label]) => (
              <a key={id} href={hrefFor(workspace, id)} className={`tab ${tab === id ? 'is-active' : ''}`} aria-current={tab === id ? 'page' : undefined}>
                {label}
              </a>
            ))}
          </div>
        </nav>
      </header>

      <main className="content">
        {healthError && (
          <div className="note note-critical" role="alert">
            <Badge tone="critical">Backend offline</Badge>
            <span>
              {healthError.message}
              {healthError.status === 0 && (
                <>
                  {' '}Start it from the project folder with{' '}
                  <code>python -m uvicorn app.main:app --app-dir service --port 8080</code>
                </>
              )}
            </span>
            <button className="btn btn-ghost btn-sm" onClick={refreshHealth}>
              Retry
            </button>
          </div>
        )}
        {workspace === 'sequence' && (
          <>
            {tab === 'overview' && <Dashboard health={health} onRefreshHealth={refreshHealth} />}
            {tab === 'enroll' && <Enroll />}
            {tab === 'preview' && <Preview />}
            {tab === 'settings' && <Settings />}
            {tab === 'tools' && <Tools />}
          </>
        )}
        {workspace === 'triage' && (
          <>
            {tab === 'replies' && <TriageReplies />}
            {tab === 'drafts' && <Drafts />}
            {tab === 'meetings' && <Meetings />}
            {tab === 'follow-ups' && <FollowUps />}
            {tab === 'accuracy' && <Accuracy />}
            {tab === 'settings' && <TriageSettings />}
          </>
        )}
      </main>
    </div>
  )
}

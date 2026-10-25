import { useCallback, useEffect, useState } from 'react'
import { api, setCsrfToken } from './api.js'
import LeadJourney from './components/LeadJourney.jsx'
import Campaigns from './components/Campaigns.jsx'
import Dashboard from './components/Dashboard.jsx'
import EmailDesign from './components/EmailDesign.jsx'
import Enroll from './components/Enroll.jsx'
import Preview from './components/Preview.jsx'
import Settings from './components/Settings.jsx'
import Tools from './components/Tools.jsx'
import NurtureContent from './components/nurture/NurtureContent.jsx'
import NurtureCalendar from './components/nurture/NurtureCalendar.jsx'
import NurtureDashboard from './components/nurture/NurtureDashboard.jsx'
import NurtureLeads from './components/nurture/NurtureLeads.jsx'
import NurtureReview, { NurtureHandoffs } from './components/nurture/NurtureReview.jsx'
import NurtureSettings from './components/nurture/NurtureSettings.jsx'
import Accuracy from './components/triage/Accuracy.jsx'
import AnswerLibrary from './components/triage/AnswerLibrary.jsx'
import Drafts from './components/triage/Drafts.jsx'
import { FollowUps, Meetings } from './components/triage/Meetings.jsx'
import TriageReplies from './components/triage/TriageReplies.jsx'
import TriageSettings from './components/triage/TriageSettings.jsx'
import { Badge, ConfirmHost, Toaster, UserContext, isAdminUser, toast } from './components/ui.jsx'

// Three workspaces in one console: OUT-01 (sending the cold sequence), OUT-05
// (handling the replies) and Email Nurture. The hash holds both parts:
// #enroll, #triage/drafts, #nurture/leads.
const WORKSPACES = {
  sequence: {
    label: 'Cold sequence',
    tabs: [
      ['overview', 'Overview'],
      ['campaigns', 'Campaigns'],
      ['enroll', 'Enroll'],
      ['design', 'Email design'],
      ['preview', 'Preview', 'admin'],
      ['settings', 'Settings', 'admin'],
      ['tools', 'Tools', 'admin'],
    ],
  },
  triage: {
    label: 'Reply triage',
    tabs: [
      ['replies', 'Replies'],
      ['drafts', 'Drafts'],
      ['meetings', 'Meetings'],
      ['follow-ups', 'Follow-ups'],
      ['library', 'Answer library'],
      ['accuracy', 'Accuracy', 'admin'],
      ['settings', 'Settings', 'admin'],
    ],
  },
  nurture: {
    label: 'Email Nurture',
    tabs: [
      ['dashboard', 'Dashboard'],
      ['leads', 'Leads'],
      ['review', 'Review'],
      ['calendar', 'Calendar'],
      ['handoffs', 'Hand-offs'],
      ['content', 'Content'],
      ['settings', 'Settings', 'admin'],
    ],
  },
}

// Icons (24px stroke paths): workspaces in the top bar, pages in the sidebar
// (the folded sidebar shows only these).
const WORKSPACE_ICONS = {
  sequence: 'M4 7l8 5.5L20 7M4 7v10h16V7',
  triage: 'M4 5h16v11H8l-4 4V5z',
  nurture: 'M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.5-7 10-7 10z',
}
const PAGE_ICONS = {
  overview: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  campaigns: 'M5 21V4h12l-2 4 2 4H5',
  enroll: 'M15 19a6 6 0 0 0-12 0M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM19 8v6M16 11h6',
  design: 'M4 20l4-1 11-11-3-3L5 16l-1 4zM14 6l3 3',
  preview: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  settings: 'M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1M15 4v4M9 10v4M17 16v4',
  tools: 'M14.5 5.5a4 4 0 0 0 4.9 4.9L10 19.8 5.2 15l9.3-9.5z',
  replies: 'M4 13l3-8h10l3 8v6H4zM4 13h5l1 2h4l1-2h5',
  drafts: 'M6 3h9l4 4v14H6zM14 3v5h5M9 13h6M9 17h4',
  meetings: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  'follow-ups': 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  library: 'M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11M9 8h6',
  accuracy: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 12.5v-1',
  dashboard: 'M4 20V11M10 20V5M16 20v-6M21 20H3',
  leads: 'M16 20a4 4 0 0 0-8 0M12 12a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM21 20a3.5 3.5 0 0 0-3-3.5M3 20a3.5 3.5 0 0 1 3-3.5',
  review: 'M4 4h16v16H4zM8 12l3 3 5-6',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4M8 14h2M12 14h2',
  handoffs: 'M21 3L10 14M21 3l-7 18-4-7-7-4z',
  content: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5',
}
const NAV_KEY = 'outreach.nav'

function Icon({ d }) {
  return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true"><path d={d} /></svg>
}

function readNav() {
  try {
    return JSON.parse(localStorage.getItem(NAV_KEY)) || {}
  } catch {
    return {}
  }
}

const THEME_KEY = 'outreach.theme'

// Tabs this user may open: admin-only ones are hidden from everyone else.
const tabsFor = (workspace, admin) => WORKSPACES[workspace].tabs.filter(([, , only]) => admin || only !== 'admin')

function readHash(admin) {
  const hash = window.location.hash.replace('#', '')
  // The old #replies link now lives in the triage workspace.
  if (hash === 'replies') return { workspace: 'triage', tab: 'replies' }
  const [first, second] = hash.split('/')
  // One lead's journey: #lead/<uuid>. Not a tab; reached from any lead.
  if (first === 'lead' && /^[0-9a-f-]{36}$/i.test(second || '')) return { workspace: 'lead', tab: second }
  if (first === 'triage' || first === 'nurture') {
    const tabs = tabsFor(first, admin)
    return { workspace: first, tab: tabs.some(([t]) => t === second) ? second : tabs[0][0] }
  }
  const tabs = tabsFor('sequence', admin)
  return { workspace: 'sequence', tab: tabs.some(([t]) => t === first) ? first : 'overview' }
}

const hrefFor = (workspace, tab) => (workspace === 'sequence' ? `#${tab}` : `#${workspace}/${tab}`)

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
      toast.error('Sign out failed. Please retry.')
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
  const admin = isAdminUser(user)
  const [{ workspace, tab }, setRoute] = useState(() => readHash(admin))
  const [theme, setTheme] = useState(readTheme)
  const [health, setHealth] = useState(null)
  const [healthError, setHealthError] = useState(null)

  useEffect(() => {
    const onHash = () => setRoute(readHash(admin))
    onHash() // the role can change while signed in (CMS re-checks it)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [admin])

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
  const [navOpen, setNavOpen] = useState(false) // phones: the sidebar as a drawer
  // Desktop: the sidebar folds to an icon rail, and each workspace group folds
  // on its own. Remembered per browser.
  const [nav, setNav] = useState(readNav)
  const folded = !!nav.folded
  const groupOpen = (id) => nav.groups?.[id] ?? id === workspace
  const saveNav = (next) => {
    setNav(next)
    try { localStorage.setItem(NAV_KEY, JSON.stringify(next)) } catch { /* ignore */ }
  }
  const toggleGroup = (id) => saveNav({ ...nav, groups: { ...nav.groups, [id]: !groupOpen(id) } })
  useEffect(() => { setNavOpen(false) }, [workspace, tab])
  const tabLabel = workspace === 'lead' ? 'Lead journey'
    : (tabsFor(workspace, admin).find(([id]) => id === tab) || [])[1]

  return (
    <UserContext.Provider value={user}>
    <div className={`shell ${folded ? 'is-folded' : ''} ${navOpen ? 'nav-open' : ''}`}>
      <header className="topbar">
        <button className="icon-btn nav-toggle" onClick={() => setNavOpen(!navOpen)} aria-label="Menu" aria-expanded={navOpen}>
          <Icon d="M4 7h16M4 12h16M4 17h16" />
        </button>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M4 7l8 5.5L20 7M4 7v10h16V7" /></svg>
          </span>
          <span className="brand-name">Outreach<span className="brand-accent">OS</span></span>
        </div>
        <nav className="ws-tabs" aria-label="Workspace">
          {Object.entries(WORKSPACES).map(([id, w]) => (
            <a key={id} href={hrefFor(id, tabsFor(id, admin)[0][0])}
              className={`ws-tab ${workspace === id ? 'is-active' : ''}`} aria-current={workspace === id ? 'page' : undefined}>
              <Icon d={WORKSPACE_ICONS[id]} />
              <span>{w.label}</span>
            </a>
          ))}
        </nav>
        <div className="topbar-status">
          <HealthPill health={health} error={healthError} />
          {health?.environment === 'dev' && (
            <span className="badge badge-info" title="APP_ENV=dev in .env: nurture emails are sent only with Send now">Dev</span>
          )}
          {health?.dry_run && (
            <span className="badge badge-info" title="DRY_RUN=true in .env: no email actually leaves">Dry run</span>
          )}
          <button className="icon-btn" onClick={() => setTheme(nextTheme)} title={`Theme: ${theme} (click for ${nextTheme})`} aria-label="Change theme">
            <Icon d={theme === 'light' ? 'M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z'
              : theme === 'dark' ? 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z' : 'M12 21a9 9 0 1 0 0-18v18z'} />
          </button>
          <div className="user-chip" title={`${user.email} (${user.role})`}>
            <span className="avatar" aria-hidden="true">{(user.email || '?')[0].toUpperCase()}</span>
            <span className="user-chip-text">
              <span className="user-chip-email">{user.email}</span>
              <span className="user-chip-role">{user.role}</span>
            </span>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onLogout}>Sign out</button>
        </div>
      </header>

      <aside className="sidebar" aria-label="Pages">
        <nav className="side-nav">
          {Object.entries(WORKSPACES).map(([id, w]) => {
            const open = folded || groupOpen(id)
            return (
              <div key={id} className={`side-group ${workspace === id ? 'is-current' : ''}`}>
                <button className="side-group-head" onClick={() => toggleGroup(id)} aria-expanded={open}
                  title={folded ? w.label : undefined} disabled={folded}>
                  <Icon d={WORKSPACE_ICONS[id]} />
                  <span className="side-label">{w.label}</span>
                  <svg className="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
                </button>
                {open && (
                  <div className="side-items">
                    {tabsFor(id, admin).map(([tid, label]) => {
                      const active = workspace === id && tab === tid
                      return (
                        <a key={tid} href={hrefFor(id, tid)} className={`side-link ${active ? 'is-active' : ''}`}
                          aria-current={active ? 'page' : undefined} title={folded ? `${w.label}: ${label}` : undefined}>
                          <Icon d={PAGE_ICONS[tid] || PAGE_ICONS.overview} />
                          <span className="side-label">{label}</span>
                        </a>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </nav>
        <button className="side-fold" onClick={() => saveNav({ ...nav, folded: !folded })}
          aria-label={folded ? 'Expand sidebar' : 'Collapse sidebar'} title={folded ? 'Expand sidebar' : 'Collapse sidebar'}>
          <Icon d={folded ? 'M13 6l6 6-6 6M5 6l6 6-6 6' : 'M11 6l-6 6 6 6M19 6l-6 6 6 6'} />
          <span className="side-label">Collapse</span>
        </button>
      </aside>
      <div className="scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />

      <div className="main">
        <div className="page-head">
          <div className="crumbs">
            {workspace !== 'lead' && <span>{WORKSPACES[workspace].label}</span>}
            {workspace !== 'lead' && <span aria-hidden="true">›</span>}
            <span className="crumb-current">{tabLabel}</span>
          </div>
        </div>

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
        {workspace === 'lead' && <LeadJourney leadId={tab} />}
        {workspace === 'sequence' && (
          <>
            {tab === 'overview' && <Dashboard health={health} onRefreshHealth={refreshHealth} />}
            {tab === 'campaigns' && <Campaigns />}
            {tab === 'enroll' && <Enroll />}
            {tab === 'design' && <EmailDesign />}
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
            {tab === 'library' && <AnswerLibrary />}
            {tab === 'accuracy' && <Accuracy />}
            {tab === 'settings' && <TriageSettings />}
          </>
        )}
        {workspace === 'nurture' && (
          <>
            {tab === 'dashboard' && <NurtureDashboard />}
            {tab === 'leads' && <NurtureLeads />}
            {tab === 'review' && <NurtureReview />}
            {tab === 'calendar' && <NurtureCalendar />}
            {tab === 'handoffs' && <NurtureHandoffs />}
            {tab === 'content' && <NurtureContent />}
            {tab === 'settings' && <NurtureSettings />}
          </>
        )}
      </main>
      </div>
      <Toaster />
      <ConfirmHost />
    </div>
    </UserContext.Provider>
  )
}

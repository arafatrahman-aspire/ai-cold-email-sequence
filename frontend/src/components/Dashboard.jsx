import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import {
  EMAIL_STATUSES,
  ENROLLMENT_ACTIVE,
  ENROLLMENT_OUTCOMES,
  formatNumber,
} from '../labels.js'
import NeedsAttention from './NeedsAttention.jsx'
import { Badge, Card, ErrorNote, ReplyCheckSchedule, Spinner, useAction } from './ui.jsx'

const REFRESH_MS = 30_000

export function StatTile({ label, value, note }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value == null ? '—' : typeof value === 'string' ? value : formatNumber(value)}</span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  )
}

// Horizontal bars, one hue: the job is comparing counts across stages.
// Each row has its value as a direct label and a hover tooltip with the share.
// Pass a shared `max` when several lists sit side by side, so equal counts
// always draw equal lengths.
export function BarList({ rows, counts, total, max: sharedMax }) {
  const max = sharedMax || Math.max(1, ...rows.map(([key]) => counts[key] || 0))
  return (
    <ul className="bars">
      {rows.map(([key, label]) => {
        const n = counts[key] || 0
        const pct = total ? Math.round((n / total) * 100) : 0
        return (
          <li key={key} className={`bar-row ${n ? '' : 'is-zero'}`} tabIndex={0}>
            <span className="bar-label">{label}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(n / max) * 100}%` }} />
            </span>
            <span className="bar-value">{formatNumber(n)}</span>
            <span className="tooltip" role="tooltip">
              <strong>{label}</strong>
              {formatNumber(n)} of {formatNumber(total)} · {pct}%
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function InboxMeter({ inbox, defaultCap }) {
  const cap = inbox.daily_cap ?? defaultCap ?? 0
  const used = inbox.sent_today || 0
  const pct = cap ? Math.min(100, (used / cap) * 100) : 0
  const full = cap > 0 && used >= cap
  return (
    <li className="meter-row">
      <div className="meter-head">
        <span className="meter-name" title={inbox.email}>
          {inbox.email}
        </span>
        {!inbox.active ? (
          <Badge tone="neutral">Paused</Badge>
        ) : full ? (
          <Badge tone="warning">Cap reached</Badge>
        ) : null}
      </div>
      <div
        className="meter-track"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={cap}
        aria-valuenow={used}
        aria-label={`${inbox.email}: ${used} of ${cap} sent today`}
      >
        <span className="meter-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="meter-value">
        {formatNumber(used)} / {formatNumber(cap)} sent today
      </span>
    </li>
  )
}

const JOBS = [
  ['intake', 'Draft sequences', 'Writes emails for newly enrolled leads', () => api.run('intake')],
  ['sender', 'Send due emails', 'Sends what is due, within hours and caps', () => api.run('sender')],
  ['poller', 'Check replies', 'Reads the inbox for replies and bounces', () => api.run('poller')],
]
const SEND_NOW = ['send-now', 'Send next email now', "Each scheduled lead's next email, ignoring business hours", () => api.sendNow()]

const RESULT_WORDS = {
  tracking_started: 'started watching the inbox (existing mail skipped)',
  unmatched: 'not about an enrolled lead (ignored)',
  deferred_hours: 'waiting for business hours',
  deferred_cap: 'waiting for daily cap',
  error: 'inbox failed',
}

function JobResult({ label, result }) {
  const { errors, ...counts } = result
  const parts = Object.entries(counts).map(
    ([k, v]) => `${v} ${RESULT_WORDS[k] || k.replace(/_/g, ' ')}`,
  )
  return (
    <>
      <div className="job-result">
        <span className="muted">{label}:</span> {parts.length ? parts.join(' · ') : 'nothing to do'}
      </div>
      {errors?.map((e) => <ErrorNote key={e} error={new Error(e)} />)}
    </>
  )
}

function RunJobs({ onDone, dryRun, schedule }) {
  const [running, setRunning] = useState(null)
  const [last, setLast] = useState(null)
  const [confirming, setConfirming] = useState(false)

  const run = async (job, label, call) => {
    setConfirming(false)
    setRunning(job)
    try {
      const res = await call()
      setLast({ label, result: res.result })
      onDone()
    } catch (error) {
      setLast({ label, error })
    } finally {
      setRunning(null)
    }
  }

  return (
    <Card title="Run now" subtitle="These also run on their own in the background." actions={<ReplyCheckSchedule schedule={schedule} />}>
      <div className="jobs">
        {JOBS.map(([job, label, hint, call]) => (
          <button
            key={job}
            className="job"
            disabled={running !== null}
            onClick={() => run(job, label, call)}
          >
            <span className="job-title">
              {running === job ? <Spinner /> : null}
              {label}
            </span>
            <span className="job-hint">{hint}</span>
          </button>
        ))}
        <button
          className="job job-accent"
          disabled={running !== null}
          onClick={() => setConfirming(true)}
          aria-expanded={confirming}
        >
          <span className="job-title">
            {running === SEND_NOW[0] ? <Spinner /> : null}
            {SEND_NOW[1]}
          </span>
          <span className="job-hint">{SEND_NOW[2]}</span>
        </button>
      </div>
      {confirming && (
        <div className="note note-warning confirm">
          <Badge tone="warning">Confirm</Badge>
          <span>
            Sends the <strong>next</strong> email of every scheduled lead right now, even outside
            their business hours. Later emails keep their schedule, and anyone emailed in the last
            24 hours is skipped. Daily caps, blocked addresses and lead status still apply.{' '}
            {dryRun ? 'Dry run is on, so nothing actually leaves.' : 'Dry run is OFF: real emails will be sent.'}
          </span>
          <div className="confirm-actions">
            <button className="btn btn-ghost btn-sm" onClick={() => setConfirming(false)}>
              Cancel
            </button>
            <button className="btn btn-primary btn-sm" onClick={() => run(...SEND_NOW.slice(0, 2), SEND_NOW[3])}>
              Send now
            </button>
          </div>
        </div>
      )}
      {last?.error && <ErrorNote error={last.error} />}
      {last?.result && <JobResult label={last.label} result={last.result} />}
    </Card>
  )
}

function relative(date) {
  const mins = Math.round((date - Date.now()) / 60000)
  if (mins <= 0) return 'due now'
  if (mins < 60) return `in ${mins} min`
  const hours = Math.round(mins / 60)
  if (hours < 48) return `in ${hours} h`
  return `in ${Math.round(hours / 24)} days`
}

function Upcoming({ refreshKey }) {
  const [emails, setEmails] = useState(null)
  const [{ error }, load] = useAction(async () => setEmails((await api.upcoming()).emails || []))
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey])

  const missing = error?.message?.includes('upcoming_emails')
  return (
    <Card title="Upcoming emails" subtitle="Times shown in your local time; each is sent inside the lead's own business hours.">
      {missing ? (
        <p className="muted">
          Run <code>supabase/migrations/0004_send_now.sql</code> in the Supabase SQL Editor to see this.
        </p>
      ) : error && error.status !== 0 ? (
        <ErrorNote error={error} onRetry={load} />
      ) : emails?.length ? (
        <ul className="upcoming">
          {emails.map((m) => {
            const due = new Date(m.due_at)
            return (
              <li key={m.id}>
                <div className="upcoming-who">
                  <span className="upcoming-email">{m.email}</span>
                  <span className="muted">Email {m.step_number} · {m.subject}</span>
                </div>
                <div className="upcoming-when">
                  <span>{due.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                  <span className="muted">{relative(due)}</span>
                </div>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="muted">Nothing scheduled.</p>
      )}
    </Card>
  )
}

export default function Dashboard({ health, onRefreshHealth }) {
  const [stats, setStats] = useState(null)
  const [defaultCap, setDefaultCap] = useState(null)
  const [updated, setUpdated] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [{ busy, error }, load] = useAction(async () => {
    const [s, settings] = await Promise.all([api.stats(), api.settings()])
    setStats(s)
    const cap = settings.settings?.find((x) => x.key === 'default_daily_cap')
    setDefaultCap(cap ? Number(cap.value) : null)
    setUpdated(new Date())
  })

  const refresh = useCallback(() => {
    load()
    onRefreshHealth()
    setRefreshKey((k) => k + 1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, REFRESH_MS)
    return () => clearInterval(id)
  }, [refresh])

  const enr = stats?.enrollments || {}
  const emails = stats?.emails || {}
  const enrTotal = Object.values(enr).reduce((a, b) => a + b, 0)
  const emailTotal = Object.values(emails).reduce((a, b) => a + b, 0)
  const inProgress = ['ready_for_outreach', 'processing', 'sequence_ready', 'sending']
    .reduce((a, k) => a + (enr[k] || 0), 0)
  const pipelineMax = Math.max(1, ...Object.values(enr))
  const replyRate = enr.replied && enrTotal ? `${Math.round((enr.replied / enrTotal) * 100)}% of enrolled` : null

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Overview</h1>
          <p className="muted">
            {updated
              ? `Updated ${updated.toLocaleTimeString()} · refreshes every 30s`
              : error
                ? 'Not loaded yet · retrying every 30s'
                : 'Loading…'}
          </p>
        </div>
        <button className="btn" onClick={refresh} disabled={busy}>
          {busy ? <Spinner /> : null} Refresh
        </button>
      </div>

      {health && health.database !== 'ok' && (
        <div className="note note-warning" role="alert">
          <Badge tone="warning">Database unavailable</Badge>
          <span>{health.database}</span>
        </div>
      )}
      {/* An unreachable backend is already announced at the top of the page. */}
      <ErrorNote error={error?.status === 0 ? null : error} onRetry={refresh} />

      <NeedsAttention refreshKey={refreshKey} onChanged={() => load()} />

      <div className="stats">
        <StatTile label="Leads enrolled" value={stats && enrTotal} note={stats && `${formatNumber(inProgress)} in progress`} />
        <StatTile label="Emails sent" value={stats && (emails.sent || 0)} note={stats && `${formatNumber(emails.pending)} scheduled`} />
        <StatTile label="Replies" value={stats && (enr.replied || 0)} note={replyRate} />
        <StatTile label="Bounced" value={stats && (enr.bounced || 0)} />
        <StatTile label="Suppressed addresses" value={stats?.suppressed} />
      </div>

      <div className="grid-2">
        <Card title="Lead pipeline" subtitle={`${formatNumber(enrTotal)} enrolled leads by stage`}>
          <h3 className="group-title">In progress</h3>
          <BarList rows={ENROLLMENT_ACTIVE} counts={enr} total={enrTotal} max={pipelineMax} />
          <h3 className="group-title">Stopped</h3>
          <BarList rows={ENROLLMENT_OUTCOMES} counts={enr} total={enrTotal} max={pipelineMax} />
        </Card>

        <div className="stack">
          <Card title="Emails" subtitle={`${formatNumber(emailTotal)} drafted emails by status`}>
            <BarList rows={EMAIL_STATUSES} counts={emails} total={emailTotal} />
          </Card>
          <Card title="Inboxes today" subtitle="Sends against each inbox's daily cap (UTC day)">
            {stats?.inboxes?.length ? (
              <ul className="meters">
                {stats.inboxes.map((ib) => (
                  <InboxMeter key={ib.email} inbox={ib} defaultCap={defaultCap} />
                ))}
              </ul>
            ) : (
              <p className="muted">No inboxes yet. Run supabase/migrations/0002_seed.sql.</p>
            )}
          </Card>
          <Upcoming refreshKey={refreshKey} />
        </div>
      </div>

      <RunJobs onDone={refresh} dryRun={health?.dry_run} schedule={health?.schedule} />
    </div>
  )
}

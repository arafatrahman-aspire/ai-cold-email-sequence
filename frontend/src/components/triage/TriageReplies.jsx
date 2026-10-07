import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api.js'
import { CATEGORIES, CATEGORY_ORDER, TRIAGE_STATUS, formatNumber, relativeTime } from '../../labels.js'
import { BarList, StatTile } from '../Dashboard.jsx'
import { Badge, Card, ErrorNote, ReplyCheckSchedule, Spinner, useAction } from '../ui.jsx'

const FILTERS = [['all', 'All'], ['needs_human', 'Needs you'], ...CATEGORY_ORDER.map((c) => [c, CATEGORIES[c][1]])]

function pct(n) {
  return n == null ? '—' : `${Math.round(n * 100)}%`
}

function ReplyCard({ reply, onChanged }) {
  const category = reply.human_category || reply.category
  const [tone, label] = CATEGORIES[category] || ['neutral', 'Not classified']
  const [sTone, sLabel] = TRIAGE_STATUS[reply.triage_status] || ['neutral', reply.triage_status || '']
  const actions = reply.extracted?.actions || []
  const [{ busy, error }, rerun] = useAction((cat) => api.retriage(reply.id, cat))

  const change = async (cat) => {
    if ((await rerun(cat)) !== undefined) onChanged()
  }

  return (
    <li className="reply triage-reply">
      <div className="reply-head">
        <div className="reply-who">
          <strong>{reply.from_email}</strong>
          <span className="muted">
            {reply.extracted?.source === 'nurture'
              ? <>Reply to an Email Nurture email · <a href="#nurture/leads">see Email Nurture → Leads</a></>
              : [reply.job_title, reply.company].filter(Boolean).join(' · ') || 'No job title or company'}
          </span>
        </div>
        <div className="reply-meta">
          <span className="badge-row">
            {category && <Badge tone={tone}>{label}</Badge>}
            {reply.triage_status !== 'done' && <Badge tone={sTone}>{sLabel}</Badge>}
          </span>
          <span className="muted">
            {relativeTime(reply.received_at)}
            {reply.confidence != null && !reply.human_category && ` · ${pct(reply.confidence)} sure`}
            {reply.human_category && ' · set by you'}
          </span>
        </div>
      </div>
      {reply.summary && <p className="reply-summary">{reply.summary}</p>}
      <p className="reply-subject">{reply.subject || '(no subject)'}</p>
      {reply.snippet && <p className="reply-snippet">{reply.snippet}</p>}

      {(actions.length > 0 || reply.triage_error) && (
        <div className="did">
          <span className="did-title">What happened</span>
          <ul>
            {actions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
          {reply.triage_error && <p className="field-error">{reply.triage_error}</p>}
        </div>
      )}

      <div className="reply-actions">
        <label className="inline-select">
          <span className="muted">Category</span>
          <select
            value=""
            disabled={busy || reply.triage_status === 'processing'}
            onChange={(e) => e.target.value && change(e.target.value)}
            aria-label="Change category and re-run"
          >
            <option value="">{category ? 'Change…' : 'Set…'}</option>
            {CATEGORY_ORDER.map((c) => (
              <option key={c} value={c} disabled={c === category}>
                {CATEGORIES[c][1]}
              </option>
            ))}
          </select>
        </label>
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => change(null)}>
          {busy ? <Spinner /> : null} Run triage again
        </button>
        {reply.gmail_link && (
          <a className="btn btn-sm" href={reply.gmail_link} target="_blank" rel="noreferrer">
            Open in Gmail ↗
          </a>
        )}
      </div>
      <ErrorNote error={error} />
    </li>
  )
}

export default function TriageReplies() {
  const [filter, setFilter] = useState('all')
  const [stats, setStats] = useState(null)
  const [schedule, setSchedule] = useState(null)
  const [replies, setReplies] = useState(null)
  const [{ busy, error }, load] = useAction(async () => {
    const cat = CATEGORY_ORDER.includes(filter) ? filter : null
    const [s, r, h] = await Promise.all([api.triageStats(), api.triageEvents(cat), api.health()])
    setStats(s)
    setReplies(r.events || [])
    setSchedule(h.schedule || {})
  })
  const [{ busy: running, error: runError, result: runResult }, runNow] = useAction(() => api.run('triage'))

  const refresh = useCallback(() => load(), [load])
  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  const missing = error?.message?.includes('not found') && error.message.includes('triage')
  const shown = (replies || []).filter((r) => filter !== 'needs_human' || ['needs_human', 'failed'].includes(r.triage_status))
  const cats = stats?.categories || {}
  const total = Object.values(cats).reduce((a, b) => a + b, 0)
  const waiting = (stats?.drafts?.pending || 0) + (stats?.drafts?.approved || 0)
  const needsYou = (stats?.triage?.needs_human || 0) + (stats?.triage?.failed || 0)

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Replies</h1>
          <p className="muted">
            Every reply is read and sorted automatically. Interested leads get meeting times, "not now"
            comes back in 60 days, referrals are enrolled, and unsubscribes are blocked everywhere.
          </p>
        </div>
        <div className="toolbar-actions">
          <ReplyCheckSchedule schedule={schedule} />
          <button className="btn" onClick={() => runNow().then(refresh)} disabled={running}>
            {running ? <Spinner /> : null} Triage now
          </button>
        </div>
      </div>

      {missing ? (
        <div className="note note-warning">
          <Badge tone="warning">Setup</Badge>
          <span>
            Run <code>supabase/migrations/0006_triage.sql</code> in the Supabase SQL Editor to turn on reply triage.
          </span>
        </div>
      ) : (
        <ErrorNote error={error?.status === 0 ? null : error} onRetry={load} />
      )}
      <ErrorNote error={runError} />
      {runResult && (
        <p className="muted">
          Triage run:{' '}
          {Object.keys(runResult.result || {}).length
            ? Object.entries(runResult.result).map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`).join(' · ')
            : 'nothing new'}
        </p>
      )}

      <div className="stats">
        <StatTile
          label="Reply → meeting rate"
          value={stats ? pct(stats.reply_to_meeting_rate) : null}
          note={stats && `${formatNumber(stats.meeting_leads)} of ${formatNumber(stats.replied_leads)} who replied`}
        />
        <StatTile label="Meetings coming up" value={stats?.meetings_upcoming} />
        <StatTile label="Drafts waiting" value={stats ? waiting : null} note={stats && 'approve, edit or let them auto-send'} />
        <StatTile label="Need you" value={stats ? needsYou : null} note={stats && 'unclear or failed replies'} />
        <StatTile label="Coming back later" value={stats?.snoozed} note={stats && '"not now" leads'} />
      </div>

      {total > 0 && (
        <Card title="Replies by category" subtitle={`${formatNumber(total)} triaged replies`}>
          <BarList rows={CATEGORY_ORDER.map((c) => [c, CATEGORIES[c][1]])} counts={cats} total={total} />
        </Card>
      )}

      <div className="chips" role="tablist" aria-label="Filter replies">
        {FILTERS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={filter === id}
            className={`chip ${filter === id ? 'is-active' : ''}`} onClick={() => setFilter(id)}>
            {label}
          </button>
        ))}
        {busy && <Spinner />}
      </div>

      {replies && !missing && (
        <Card>
          {shown.length ? (
            <ul className="replies">
              {shown.map((r) => (
                <ReplyCard key={r.id} reply={r} onChanged={refresh} />
              ))}
            </ul>
          ) : (
            <p className="muted">Nothing here. Replies appear within minutes of arriving.</p>
          )}
        </Card>
      )}
    </div>
  )
}

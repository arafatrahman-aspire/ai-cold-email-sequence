import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import {
  EXIT_REASONS, formatNumber, MESSAGE_STATUS, NURTURE_PERSONAS, NURTURE_STATUS, nurtureOutcome,
  relativeTime, shortDateTime, TEMPERATURES,
} from '../../labels.js'
import { Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

const PAGE = 50
const STATUSES = [
  ['live', 'In nurture'],
  ['handed_off', 'Handed off'],
  ['completed', 'Completed'],
  ['exited', 'Exited'],
  ['', 'All'],
]

const name = (e) => [e.first_name, e.last_name].filter(Boolean).join(' ') || e.email
const live = (s) => ['active', 'paused', 'held'].includes(s)

function StatusBadge({ status }) {
  const [tone, label] = NURTURE_STATUS[status] || ['neutral', status]
  return <Badge tone={tone}>{label}</Badge>
}

// ---------------------------------------------------------------------------
// Detail: timeline and actions
// ---------------------------------------------------------------------------

const EVENT_TEXT = {
  enrolled: (d) => `Enrolled on the ${TEMPERATURES[d.temperature] || d.temperature} track as ${NURTURE_PERSONAS[d.persona] || d.persona} (${d.persona_source || 'keyword'}${d.source ? `, ${d.source}` : ''})`,
  track: (d) => `Track changed from ${TEMPERATURES[d.from] || d.from} to ${TEMPERATURES[d.to] || d.to}`,
  persona: (d) => `Persona changed to ${NURTURE_PERSONAS[d.to] || d.to}${d.by ? ` by ${d.by}` : ''}`,
  handoff: (d) => `Handed to sales: ${EXIT_REASONS[d.trigger] || d.trigger}`,
  exited: (d) => `Left nurture: ${EXIT_REASONS[d.reason] || d.reason}`,
  completed: () => 'All 6 emails sent: completed',
  unsubscribed: (d) => `Unsubscribed (${d.via === 'one_click' ? 'one-click in their mail app' : 'unsubscribe page'})`,
  paused: (d) => `Paused${d.reason ? `: ${d.reason}` : ''}`,
  held: (d) => `Held${d.reason ? `: ${d.reason}` : ''}`,
  active: (d) => `Active again${d.reason ? `: ${d.reason}` : ''}`,
  delay: (d) => `Next email moved to ${shortDateTime(d.until)} (${d.reason})`,
  review: (d) => `Flagged for review: ${d.reason}`,
  reviewed: (d) => `Review cleared${d.reason ? ` (${d.reason})` : ''}`,
}

function MessageItem({ m }) {
  const [open, setOpen] = useState(false)
  const log = m.ai_log || {}
  const [tone, label] = MESSAGE_STATUS[m.status] || ['neutral', m.status]
  return (
    <li className="tl-item tl-email">
      <span className="tl-when">{shortDateTime(m.sent_at || m.send_at)}</span>
      <div className="tl-body">
        <div className="tl-line">
          <strong>Email {m.step}: {m.subject || '(being written)'}</strong>
          <span className="badge-row">
            <Badge tone={tone}>{label}</Badge>
            {m.source && <Badge tone={m.source === 'fallback' ? 'warning' : 'neutral'}>{m.source === 'fallback' ? 'Fallback text' : m.source === 'edited' ? 'Edited' : 'AI'}</Badge>}
          </span>
        </div>
        <button className="link-btn" onClick={() => setOpen(!open)}>{open ? 'Hide content' : 'Show content'}</button>
        {open && (
          <div className="tl-email-content">
            {m.preheader && <p className="muted">Preview: {m.preheader}</p>}
            <pre className="email-text">{m.body}</pre>
            <p className="muted">
              {[log.model, log.prompt_version, log.brief_version && `brief v${log.brief_version}`,
                log.judge_score != null && `judge ${Number(log.judge_score).toFixed(2)}`,
                log.attempts != null && `${log.attempts} attempt${log.attempts === 1 ? '' : 's'}`,
                (log.tokens_in || log.tokens_out) && `${formatNumber((log.tokens_in || 0) + (log.tokens_out || 0))} tokens`,
                m.error && `note: ${m.error}`].filter(Boolean).join(' · ')}
            </p>
            {(log.checks || []).filter((c) => c.problems || c.error).map((c) => (
              <p key={c.attempt} className="muted">Attempt {c.attempt}: {(c.problems || [c.error]).join('; ')}</p>
            ))}
          </div>
        )}
      </div>
    </li>
  )
}

function Timeline({ detail }) {
  const items = [
    ...(detail.events || []).filter((e) => e.kind !== 'sent' && e.kind !== 'click')
      .map((e) => ({ at: e.at, key: `e${e.id}`, node: (
        <li key={`e${e.id}`} className="tl-item">
          <span className="tl-when">{shortDateTime(e.at)}</span>
          <div className="tl-body">{(EVENT_TEXT[e.kind] || ((d) => `${e.kind} ${JSON.stringify(d)}`))(e.detail || {})}</div>
        </li>) })),
    ...(detail.messages || []).map((m) => ({ at: m.sent_at || m.send_at, key: `m${m.id}`, node: <MessageItem key={`m${m.id}`} m={m} /> })),
    ...(detail.events || []).filter((e) => e.kind === 'click').map((c) => ({ at: c.at, key: `c${c.id}`, node: (
      <li key={`c${c.id}`} className="tl-item">
        <span className="tl-when">{shortDateTime(c.at)}</span>
        <div className="tl-body">
          Clicked the {c.detail?.link} link in email {c.detail?.step}
          {c.detail?.bot && <> <Badge tone="neutral">Mail scanner, ignored</Badge></>}
        </div>
      </li>) })),
    ...(detail.replies || []).map((r) => ({ at: r.received_at, key: `r${r.id}`, node: (
      <li key={`r${r.id}`} className="tl-item tl-reply">
        <span className="tl-when">{shortDateTime(r.received_at)}</span>
        <div className="tl-body">
          <strong>{r.event_type === 'reply' ? 'Replied' : r.event_type.replace('_', ' ')}</strong>
          {r.category && <> <Badge tone="neutral">{r.category.replace('_', ' ')}</Badge></>}
          {r.summary && <div className="muted">{r.summary}</div>}
          <p className="reply-snippet">{r.snippet}</p>
        </div>
      </li>) })),
  ].sort((a, b) => new Date(a.at) - new Date(b.at))
  return <ol className="nurture-timeline">{items.map((i) => i.node)}</ol>
}

function Detail({ id, onChanged, onClose }) {
  const [detail, setDetail] = useState(null)
  const [{ busy: loading, error }, load] = useAction(async () => setDetail(await api.nurtureEnrollment(id)))
  const [{ busy, error: actError }, act] = useAction(async (action, body, confirm) => {
    if (confirm && !window.confirm(confirm)) return
    await api.nurtureAction(id, action, body)
    await load()
    onChanged()
  })
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  if (!detail) return <Card>{loading ? <Spinner label="Loading…" /> : <ErrorNote error={error} onRetry={load} />}</Card>
  // Name, job title and company come from the shared lead; the rest from the enrollment.
  const e = { ...(detail.lead || {}), ...detail.enrollment }
  return (
    <Card className="nurture-detail"
      title={name(e)}
      subtitle={[e.email, e.job_title, e.company].filter(Boolean).join(' · ')}
      actions={<button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>}>
      <div className="detail-facts">
        <StatusBadge status={e.status} />
        <span>{NURTURE_PERSONAS[e.persona]} · {TEMPERATURES[e.temperature]} track</span>
        <span>Email {e.step} of 6 sent</span>
        {e.next_send_at && live(e.status) && <span>Next {shortDateTime(e.next_send_at)} ({relativeTime(e.next_send_at)})</span>}
        <span>Score now: {detail.lead?.tier || '—'}</span>
        {e.exit_reason && <span>{EXIT_REASONS[e.exit_reason] || e.exit_reason}</span>}
        {e.test_mode && <Badge tone="info">Test</Badge>}
      </div>
      {e.needs_review && (
        <div className="note note-warning">
          <Badge tone="warning">Review</Badge>
          <span>{e.note}</span>
          {live(e.status) && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => act('reviewed')}>Mark reviewed</button>}
        </div>
      )}
      {e.note && !e.needs_review && ['paused', 'held'].includes(e.status) && <p className="muted">Why {e.status}: {e.note}</p>}
      {live(e.status) && (
        <div className="detail-actions">
          {e.status === 'active'
            ? <button className="btn btn-sm" disabled={busy} onClick={() => act('pause')}>Pause</button>
            : <button className="btn btn-sm" disabled={busy} onClick={() => act('resume')}>Resume</button>}
          <label className="inline-select">
            Persona
            <select value={e.persona} disabled={busy}
              onChange={(ev) => act('persona', { persona: ev.target.value },
                `Change persona to ${NURTURE_PERSONAS[ev.target.value]}? The next email is rewritten.`)}>
              {Object.entries(NURTURE_PERSONAS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          </label>
          <button className="btn btn-primary btn-sm" disabled={busy}
            onClick={() => act('handoff', undefined, `Hand ${name(e)} to sales now? Nurture emails stop and sales is notified.`)}>
            Hand off now
          </button>
          <button className="btn btn-ghost btn-sm btn-remove" disabled={busy}
            onClick={() => act('remove', undefined, `Remove ${name(e)} from nurture? Unsent emails are cancelled.`)}>
            Remove
          </button>
        </div>
      )}
      <ErrorNote error={actError} />
      {e.handoff_summary && (
        <div className="did"><span className="did-title">Summary sent to sales</span><p>{e.handoff_summary}</p></div>
      )}
      <h3 className="detail-h">Timeline</h3>
      <Timeline detail={detail} />
    </Card>
  )
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

function EnrollEligible({ onDone }) {
  const [{ result: eligible }, count] = useAction(() => api.nurtureEligible())
  const [{ busy, error, result }, enroll] = useAction(async () => {
    if (!window.confirm(`Enroll all ${eligible.count} eligible Warm and Cold leads? Each starts receiving nurture emails.`)) return null
    const r = await api.nurtureEnroll({ all_eligible: true })
    onDone()
    count()
    return r
  })
  useEffect(() => {
    count()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  if (!eligible) return null
  const reasons = {}
  for (const r of result?.results || []) if (r.outcome !== 'enrolled') reasons[r.outcome] = (reasons[r.outcome] || 0) + 1
  return (
    <div className="enroll-eligible">
      <button className="btn" disabled={busy || !eligible.count} onClick={() => enroll()}
        title="Leads already Warm or Cold when nurture started are not enrolled automatically.">
        {busy ? <Spinner /> : null} Enroll eligible leads ({formatNumber(eligible.count)})
      </button>
      {result && (
        <span className="muted">
          Enrolled {formatNumber(result.enrolled)}.
          {Object.entries(reasons).map(([k, n]) => ` ${nurtureOutcome(k)}: ${n}.`)}
        </span>
      )}
      <ErrorNote error={error} />
    </div>
  )
}

export default function NurtureLeads() {
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState({ search: '', status: 'live', persona: '', temperature: '', review: '' })
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(null)
  const [{ busy, error }, load] = useAction(async () => {
    setData(await api.nurtureEnrollments({ ...filters, limit: PAGE, offset }))
  })
  useEffect(() => {
    const t = setTimeout(() => {
      setFilters((f) => ({ ...f, search: search.trim() }))
      setOffset(0)
    }, 300)
    return () => clearTimeout(t)
  }, [search])
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, offset])
  const setFilter = (patch) => {
    setFilters((f) => ({ ...f, ...patch }))
    setOffset(0)
  }

  const rows = data?.enrollments || []
  const total = data?.total || 0
  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Nurture leads</h1>
          <p className="muted">Leads join when their score becomes Warm or Cold. Select one to see every email, click and reply.</p>
        </div>
        <EnrollEligible onDone={load} />
      </div>

      <div className="lead-filters">
        <input type="search" className="lead-search" value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name, email, company or job title" aria-label="Search" />
        <div className="filter-row">
          <div className="chips" role="tablist" aria-label="Status">
            {STATUSES.map(([key, label]) => (
              <button key={key} role="tab" aria-selected={filters.status === key}
                className={`chip ${filters.status === key ? 'is-active' : ''}`} onClick={() => setFilter({ status: key })}>
                {label}
              </button>
            ))}
          </div>
          <select aria-label="Persona" value={filters.persona} onChange={(e) => setFilter({ persona: e.target.value })}>
            <option value="">All personas</option>
            {Object.entries(NURTURE_PERSONAS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <select aria-label="Track" value={filters.temperature} onChange={(e) => setFilter({ temperature: e.target.value })}>
            <option value="">Warm and Cold</option>
            {Object.entries(TEMPERATURES).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <label className="check-pill-inline">
            <input type="checkbox" checked={filters.review === 'true'}
              onChange={(e) => setFilter({ review: e.target.checked ? 'true' : '' })} />
            Needs review
          </label>
        </div>
      </div>
      <ErrorNote error={error} onRetry={load} />

      {selected && <Detail id={selected} onChanged={load} onClose={() => setSelected(null)} />}

      <Card>
        {!data && busy ? <Spinner label="Loading…" /> : rows.length ? (
          <div className="table-wrap">
            <table className="lead-table">
              <thead>
                <tr><th>Lead</th><th>Persona · track</th><th>Step</th><th>Status</th><th>Next email</th><th aria-label="Open" /></tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr key={e.id} className={selected === e.id ? 'is-selected' : undefined}>
                    <td>
                      <strong>{name(e)}</strong>
                      <div className="muted">{[e.job_title, e.company].filter(Boolean).join(' · ') || e.email}</div>
                    </td>
                    <td>
                      {NURTURE_PERSONAS[e.persona]} · {TEMPERATURES[e.temperature]}
                      {e.needs_review && <div><Badge tone="warning">Review</Badge></div>}
                    </td>
                    <td>{e.step} of 6</td>
                    <td>
                      <StatusBadge status={e.status} />
                      {e.exit_reason && <div className="muted">{EXIT_REASONS[e.exit_reason] || e.exit_reason}</div>}
                    </td>
                    <td>{e.next_send_at && live(e.status) ? <>{shortDateTime(e.next_send_at)}<div className="muted">{relativeTime(e.next_send_at)}</div></> : <span className="muted">—</span>}</td>
                    <td className="col-action"><button className="btn btn-sm" onClick={() => setSelected(e.id)}>Open</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : data ? <p className="muted">No leads here.</p> : null}
        {total > PAGE && (
          <div className="pager">
            <span className="muted">{formatNumber(offset + 1)}–{formatNumber(Math.min(offset + PAGE, total))} of {formatNumber(total)}</span>
            <button className="btn btn-sm" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
            <button className="btn btn-sm" disabled={busy || offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
          </div>
        )}
      </Card>
    </div>
  )
}

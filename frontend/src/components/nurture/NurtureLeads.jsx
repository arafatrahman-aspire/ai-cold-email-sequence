import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import {
  EXIT_REASONS, formatNumber, MESSAGE_STATUS, NURTURE_PERSONAS, NURTURE_STATUS, nurtureOutcome,
  relativeTime, shortDateTime, TEMPERATURES,
} from '../../labels.js'
import { Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

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

const VIEWS = [
  ['all', 'All leads'],
  ['can_join', 'Can join'],
  ['in_nurture', 'In nurture'],
  ['finished', 'Finished'],
]
const PAGE_SIZE = 100

// A lead that may be enrolled now, or one in nurture that may be removed.
const canEnroll = (l) => !l.why_not && !live(l.status)
const canRemove = (l) => live(l.status)

function NurtureState({ lead }) {
  if (lead.status) {
    return (
      <>
        <StatusBadge status={lead.status} />
        <div className="muted">
          {NURTURE_PERSONAS[lead.persona]} · {TEMPERATURES[lead.temperature]} · {lead.step} of 6 sent
        </div>
        {live(lead.status) && lead.next_send_at && <div className="muted">next {shortDateTime(lead.next_send_at)}</div>}
        {!live(lead.status) && lead.exit_reason && <div className="muted">{EXIT_REASONS[lead.exit_reason] || lead.exit_reason}</div>}
        {lead.needs_review && <div><Badge tone="warning">Review</Badge></div>}
      </>
    )
  }
  if (lead.why_not) return <span className="muted">{nurtureOutcome(lead.why_not)}</span>
  return <span className="muted">Not in nurture</span>
}

export default function NurtureLeads() {
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [view, setView] = useState('all')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(() => new Map()) // lead_id -> lead (or {lead_id} from "select all")
  const [open, setOpen] = useState(null)
  const [notice, setNotice] = useState(null)

  const [{ busy: loading, error }, load] = useAction(async () => {
    setData(await api.nurtureLeads({ search: query, view, limit: PAGE_SIZE, offset }))
  })
  useEffect(() => {
    const t = setTimeout(() => { setQuery(search.trim()); setOffset(0) }, 300)
    return () => clearTimeout(t)
  }, [search])
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, view, offset])

  const [{ busy: acting, error: actError }, act] = useAction(async (action, leads) => {
    if (action === 'enroll') {
      if (!window.confirm(`Enroll ${leads.length} lead${leads.length === 1 ? '' : 's'} in Email Nurture? Each starts receiving the 6 emails.`)) return
      const r = await api.nurtureEnroll({ lead_ids: leads.map((l) => l.lead_id) })
      const reasons = {}
      for (const x of r.results) if (x.outcome !== 'enrolled') reasons[x.outcome] = (reasons[x.outcome] || 0) + 1
      setNotice({ tone: Object.keys(reasons).length ? 'warning' : 'good',
        text: `Enrolled ${r.enrolled} of ${leads.length}.` + Object.entries(reasons).map(([k, n]) => ` ${nurtureOutcome(k)}: ${n}.`).join('') })
    } else {
      if (!window.confirm(`Remove ${leads.length} lead${leads.length === 1 ? '' : 's'} from nurture? Unsent emails are cancelled.`)) return
      let done = 0
      for (const l of leads) {
        try { await api.nurtureAction(l.enrollment_id, 'remove'); done += 1 } catch { /* shown in the count */ }
      }
      setNotice({ tone: done === leads.length ? 'good' : 'warning', text: `Removed ${done} of ${leads.length}.` })
    }
    setSelected(new Map())
    await load()
  })

  // Every lead that can join, across all pages.
  const [{ busy: selectingAll }, selectAllEligible] = useAction(async () => {
    const r = await api.nurtureEligible()
    setSelected(new Map(r.lead_ids.map((id) => [id, { lead_id: id }])))
  })

  const rows = data?.leads || []
  const total = data?.total || 0
  const pickable = rows.filter((l) => canEnroll(l) || canRemove(l))
  const allOnPage = pickable.length > 0 && pickable.every((l) => selected.has(l.lead_id))
  const picked = [...selected.values()]
  const toEnroll = picked.filter(canEnroll)
  const toRemove = picked.filter((l) => canRemove(l))
  const busy = loading || acting

  const toggle = (l) => setSelected((m) => {
    const next = new Map(m)
    next.has(l.lead_id) ? next.delete(l.lead_id) : next.set(l.lead_id, l)
    return next
  })
  const togglePage = () => setSelected((m) => {
    const next = new Map(m)
    pickable.forEach((l) => (allOnPage ? next.delete(l.lead_id) : next.set(l.lead_id, l)))
    return next
  })

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Nurture leads</h1>
          <p className="muted">
            Every lead in your shared leads table. Tick the ones to enroll (Warm and Cold leads that may join), or the
            ones in nurture to remove. Score changes still enroll leads automatically.
          </p>
        </div>
      </div>

      <div className="lead-filters">
        <input type="search" className="lead-search" value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name, email, company or job title" aria-label="Search" />
        <div className="chips" role="tablist" aria-label="Which leads">
          {VIEWS.map(([key, label]) => (
            <button key={key} role="tab" aria-selected={view === key}
              className={`chip ${view === key ? 'is-active' : ''}`}
              onClick={() => { setView(key); setOffset(0) }}>
              {label}{data?.counts && <span className="chip-count">{formatNumber(data.counts[key])}</span>}
            </button>
          ))}
        </div>
      </div>

      {notice && (
        <div className={`note note-${notice.tone}`} role="status">
          <Badge tone={notice.tone}>{notice.tone === 'good' ? 'Done' : 'Partly done'}</Badge>
          <span>{notice.text}</span>
          <button className="btn btn-ghost btn-sm" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}
      <ErrorNote error={error} onRetry={load} />
      <ErrorNote error={actError} />

      {open && <Detail id={open} onChanged={load} onClose={() => setOpen(null)} />}

      <div className="save-bar" role="region" aria-label="Selection">
        <span>{picked.length ? `${formatNumber(picked.length)} selected` : 'Tick leads to enroll or remove them'}</span>
        <div className="save-actions">
          <button className="btn btn-ghost btn-sm" disabled={selectingAll || busy} onClick={() => selectAllEligible()}
            title="Selects every Warm and Cold lead that may join, on every page">
            {selectingAll ? <Spinner /> : null} Select all that can join{data?.counts ? ` (${formatNumber(data.counts.can_join)})` : ''}
          </button>
          {picked.length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => setSelected(new Map())}>Clear</button>}
          {toEnroll.length > 0 && (
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act('enroll', toEnroll)}>
              Enroll {formatNumber(toEnroll.length)}
            </button>
          )}
          {toRemove.length > 0 && (
            <button className="btn btn-sm btn-remove" disabled={busy} onClick={() => act('remove', toRemove)}>
              Remove {formatNumber(toRemove.length)}
            </button>
          )}
        </div>
      </div>

      <Card>
        {!data && loading ? <Spinner label="Loading leads…" /> : rows.length ? (
          <div className="table-wrap">
            <table className="lead-table">
              <thead>
                <tr>
                  <th className="col-check">
                    <input type="checkbox" checked={allOnPage} disabled={!pickable.length} onChange={togglePage}
                      aria-label="Select every lead on this page" />
                  </th>
                  <th>Lead</th><th>Role</th><th>Score</th><th>Nurture</th><th aria-label="Action" />
                </tr>
              </thead>
              <tbody>
                {rows.map((l) => (
                  <tr key={l.lead_id} className={selected.has(l.lead_id) ? 'is-selected' : undefined}>
                    <td className="col-check">
                      {(canEnroll(l) || canRemove(l)) && (
                        <input type="checkbox" checked={selected.has(l.lead_id)} onChange={() => toggle(l)}
                          aria-label={`Select ${name(l)}`} />
                      )}
                    </td>
                    <td><strong>{[l.first_name, l.last_name].filter(Boolean).join(' ') || '—'}</strong><div className="muted">{l.email || 'No email'}</div></td>
                    <td>{l.job_title || <span className="muted">No job title</span>}<div className="muted">{l.company || 'No company'}</div></td>
                    <td>{l.tier || <span className="muted">—</span>}<div className="muted">{l.lead_status}</div></td>
                    <td><NurtureState lead={l} /></td>
                    <td className="col-action">
                      {l.enrollment_id && <button className="btn btn-sm" onClick={() => setOpen(l.enrollment_id)}>Open</button>}
                      {canEnroll(l) && <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act('enroll', [l])}>Enroll</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : data ? <p className="muted">{query ? `No leads match "${query}".` : 'No leads in this view.'}</p> : null}
        {total > PAGE_SIZE && (
          <div className="pager">
            <span className="muted">{formatNumber(offset + 1)}–{formatNumber(Math.min(offset + PAGE_SIZE, total))} of {formatNumber(total)}</span>
            <button className="btn btn-sm" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
            <button className="btn btn-sm" disabled={busy || offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
          </div>
        )}
      </Card>
    </div>
  )
}

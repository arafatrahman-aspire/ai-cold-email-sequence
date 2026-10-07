import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { ENROLLMENT_BADGE, enrollOutcome, formatNumber, shortDateTime } from '../labels.js'
import { Badge, Card, ErrorNote, Field, Spinner, useAction } from './ui.jsx'

const PAGE = 50

const VIEWS = [
  ['all', 'All'],
  ['available', 'Ready to enroll'],
  ['in_sequence', 'In sequence'],
  ['finished', 'Finished'],
  ['not_enrolled', 'Not enrolled'],
]

// Enrollment statuses a lead can be removed from (the sequence still has
// something to send). "processing" is a few seconds of drafting: wait.
const REMOVABLE = new Set(['ready_for_outreach', 'sequence_ready', 'sending', 'manual_review', 'failed', 'snoozed'])

function actionFor(lead) {
  const s = lead.enrollment_status
  if (!s) return lead.blocked ? null : 'enroll'
  if (s === 'stopped') return 'reenroll'
  if (REMOVABLE.has(s)) return 'remove'
  return null
}

const fullName = (l) => [l.first_name, l.last_name].filter(Boolean).join(' ')
const who = (l) => fullName(l) || l.email || l.lead_ref || 'this lead'

function SequenceState({ lead }) {
  if (!lead.enrollment_status) {
    if (!lead.blocked) return <span className="muted">Not enrolled</span>
    const [tone, label] = enrollOutcome(lead.blocked)
    return <Badge tone={tone}>{label}</Badge>
  }
  const [tone, label] = ENROLLMENT_BADGE[lead.enrollment_status] || ['neutral', lead.enrollment_status]
  const detail = [
    lead.emails_sent ? `${formatNumber(lead.emails_sent)} sent` : null,
    lead.next_email_at ? `next ${shortDateTime(lead.next_email_at)}` : null,
  ].filter(Boolean).join(' · ')
  return (
    <>
      <Badge tone={tone}>{label}</Badge>
      {detail && <div className="muted">{detail}</div>}
      {lead.enrollment_note && <div className="muted lead-note">{lead.enrollment_note}</div>}
    </>
  )
}

function RowAction({ lead, busy, onAct }) {
  const action = actionFor(lead)
  if (lead.enrollment_status === 'processing') {
    return <button className="btn btn-sm" disabled>Drafting…</button>
  }
  if (action === 'enroll') {
    return <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onAct('enroll', [lead])}>Enroll</button>
  }
  if (action === 'reenroll') {
    return <button className="btn btn-sm" disabled={busy} onClick={() => onAct('reenroll', [lead])}>Enroll again</button>
  }
  if (action === 'remove') {
    return <button className="btn btn-ghost btn-sm btn-remove" disabled={busy} onClick={() => onAct('remove', [lead])}>Remove</button>
  }
  return null
}

function confirmText(action, leads) {
  const many = leads.length > 1
  const name = many ? `${leads.length} leads` : who(leads[0])
  if (action === 'remove') {
    return `Remove ${name} from the cold sequence?\n\nEmails not sent yet are cancelled. Nothing already sent is lost, and you can enroll ${many ? 'them' : 'this lead'} again later.`
  }
  if (action === 'reenroll') {
    return `Enroll ${name} again?\n\nA fresh sequence is drafted and starts from the first email.`
  }
  return null
}

function LeadBrowser() {
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [view, setView] = useState('all')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(() => new Map())
  const [notice, setNotice] = useState(null)

  const [{ busy: loading, error }, load] = useAction(async () => {
    setData(await api.leads({ search: query, view, limit: PAGE, offset }))
  })

  // Search as you type, without a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      setQuery(search.trim())
      setOffset(0)
    }, 300)
    return () => clearTimeout(t)
  }, [search])
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, view, offset])

  const [{ busy: acting, error: actError }, act] = useAction(async (action, leads) => {
    const text = confirmText(action, leads)
    if (text && !window.confirm(text)) return
    let done = 0
    const problems = []
    if (action === 'enroll') {
      const r = await api.enroll(leads.map((l) => ({ lead_id: l.id })))
      done = r.enrolled
      r.results.forEach((x, i) => x.outcome !== 'enrolled' && problems.push([leads[i], enrollOutcome(x.outcome)[1]]))
    } else if (action === 'remove') {
      const r = await api.removeFromSequence(leads.map((l) => l.id))
      done = r.removed
      const why = { busy: 'its sequence is being drafted; try again in a moment', not_active: 'it is no longer in the sequence', not_enrolled: 'it is not enrolled' }
      r.results.forEach((x, i) => x.outcome !== 'removed' && problems.push([leads[i], why[x.outcome] || x.outcome]))
    } else {
      for (const l of leads) {
        try {
          await api.retry(l.id)
          done += 1
        } catch (e) {
          problems.push([l, e.message || 'could not enroll again'])
        }
      }
    }
    const verb = { enroll: 'Enrolled', reenroll: 'Enrolled again', remove: 'Removed' }[action]
    const subject = leads.length === 1 ? who(leads[0]) : `${formatNumber(done)} of ${formatNumber(leads.length)}`
    setNotice({
      tone: problems.length ? 'warning' : 'good',
      text: done
        ? `${verb} ${subject}.${action === 'remove' ? '' : ' Sequences are drafted within 5 minutes.'}`
        : 'Nothing changed.',
      problems,
    })
    setSelected(new Map())
    await load()
  })

  const leads = data?.leads || []
  const actionable = leads.filter((l) => actionFor(l))
  const picked = [...selected.values()]
  const toEnroll = picked.filter((l) => actionFor(l) === 'enroll')
  const toReenroll = picked.filter((l) => actionFor(l) === 'reenroll')
  const toRemove = picked.filter((l) => actionFor(l) === 'remove')
  const allPicked = actionable.length > 0 && actionable.every((l) => selected.has(l.id))

  const toggle = (lead) =>
    setSelected((m) => {
      const next = new Map(m)
      next.has(lead.id) ? next.delete(lead.id) : next.set(lead.id, lead)
      return next
    })
  const toggleAll = () =>
    setSelected((m) => {
      const next = new Map(m)
      actionable.forEach((l) => (allPicked ? next.delete(l.id) : next.set(l.id, l)))
      return next
    })

  const total = data?.total ?? 0
  const busy = acting || loading
  return (
    <div className="stack">
      <div className="lead-filters">
        <input
          type="search"
          className="lead-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name, email, company, job title or lead ID"
          aria-label="Search leads"
        />
        <div className="chips" role="tablist" aria-label="Which leads">
          {VIEWS.map(([key, label]) => (
            <button
              key={key}
              role="tab"
              aria-selected={view === key}
              className={`chip ${view === key ? 'is-active' : ''}`}
              onClick={() => {
                setView(key)
                setOffset(0)
              }}
            >
              {label}
              {data?.counts ? <span className="chip-count">{formatNumber(data.counts[key])}</span> : null}
            </button>
          ))}
        </div>
      </div>

      {notice && (
        <div className={`note note-${notice.tone}`} role="status">
          <Badge tone={notice.tone}>{notice.tone === 'good' ? 'Done' : 'Partly done'}</Badge>
          <div>
            <div>{notice.text}</div>
            {notice.problems.map(([l, why]) => (
              <div key={l.id} className="muted">{who(l)}: {why}</div>
            ))}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}
      <ErrorNote error={error} onRetry={load} />
      <ErrorNote error={actError} />

      {picked.length > 0 && (
        <div className="save-bar is-dirty" role="region" aria-label="Selected leads">
          <span>{formatNumber(picked.length)} selected</span>
          <div className="save-actions">
            {toEnroll.length > 0 && (
              <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act('enroll', toEnroll)}>
                Enroll {formatNumber(toEnroll.length)}
              </button>
            )}
            {toReenroll.length > 0 && (
              <button className="btn btn-sm" disabled={busy} onClick={() => act('reenroll', toReenroll)}>
                Enroll again {formatNumber(toReenroll.length)}
              </button>
            )}
            {toRemove.length > 0 && (
              <button className="btn btn-sm btn-remove" disabled={busy} onClick={() => act('remove', toRemove)}>
                Remove {formatNumber(toRemove.length)}
              </button>
            )}
            <button className="btn btn-ghost btn-sm" onClick={() => setSelected(new Map())}>Clear</button>
          </div>
        </div>
      )}

      <Card>
        {!data && loading ? (
          <Spinner label="Loading leads…" />
        ) : leads.length ? (
          <div className="table-wrap">
            <table className="lead-table">
              <thead>
                <tr>
                  <th className="col-check">
                    <input type="checkbox" checked={allPicked} disabled={!actionable.length}
                      onChange={toggleAll} aria-label="Select every lead on this page" />
                  </th>
                  <th>Lead</th>
                  <th>Role</th>
                  <th>Source</th>
                  <th>Cold sequence</th>
                  <th aria-label="Action" />
                </tr>
              </thead>
              <tbody>
                {leads.map((l) => (
                  <tr key={l.id} className={selected.has(l.id) ? 'is-selected' : undefined}>
                    <td className="col-check">
                      {actionFor(l) && (
                        <input type="checkbox" checked={selected.has(l.id)} onChange={() => toggle(l)}
                          aria-label={`Select ${who(l)}`} />
                      )}
                    </td>
                    <td>
                      <strong>{fullName(l) || '—'}</strong>
                      <div>{l.email || <span className="muted">No email</span>}</div>
                      <div className="muted"><code>{l.lead_ref || l.id.slice(0, 8)}</code></div>
                    </td>
                    <td>
                      {l.job_title || <span className="muted">No job title</span>}
                      <div className="muted">{l.company || 'No company'}</div>
                    </td>
                    <td>
                      {l.source || <span className="muted">—</span>}
                      <div className="muted">{l.lead_status}</div>
                    </td>
                    <td><SequenceState lead={l} /></td>
                    <td className="col-action"><RowAction lead={l} busy={busy} onAct={act} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : data ? (
          <p className="muted">{query ? `No leads match "${query}".` : 'No leads in this view.'}</p>
        ) : null}

        {total > PAGE && (
          <div className="pager">
            <span className="muted">
              {formatNumber(offset + 1)}–{formatNumber(Math.min(offset + PAGE, total))} of {formatNumber(total)}
            </span>
            <button className="btn btn-sm" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Previous
            </button>
            <button className="btn btn-sm" disabled={busy || offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
              Next
            </button>
          </div>
        )}
      </Card>
    </div>
  )
}

const emptyRow = () => ({ lead_id: '', job_title: '', company: '', timezone: '' })

// Enroll by ID, with corrections for leads whose shared data is missing or
// wrong (job title decides the persona, timezone decides send hours).
function EnrollById() {
  const [rows, setRows] = useState([emptyRow()])
  const [bulk, setBulk] = useState('')
  const [{ busy, error, result }, submit] = useAction((leads) => api.enroll(leads))

  const update = (i, key, value) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: value } : r)))

  const addBulk = () => {
    const ids = bulk.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean)
    if (!ids.length) return
    setRows((rs) => [...rs.filter((r) => r.lead_id.trim()), ...ids.map((id) => ({ ...emptyRow(), lead_id: id }))])
    setBulk('')
  }

  const onSubmit = (e) => {
    e.preventDefault()
    const leads = rows
      .filter((r) => r.lead_id.trim())
      .map((r) => {
        const lead = { lead_id: r.lead_id.trim() }
        for (const k of ['job_title', 'company', 'timezone']) if (r[k].trim()) lead[k] = r[k].trim()
        return lead
      })
    if (leads.length) submit(leads)
  }

  const count = rows.filter((r) => r.lead_id.trim()).length

  return (
    <details className="card advanced">
      <summary>Enroll by ID, with corrections</summary>
      <p className="muted">
        For leads whose job title, company or timezone is missing or wrong in the shared tables: job title
        decides the persona, timezone decides send hours.
      </p>
      <div className="stack">
        <div className="inline-form">
          <textarea
            rows={3}
            value={bulk}
            onChange={(e) => setBulk(e.target.value)}
            placeholder={'Paste many IDs, separated by new lines, spaces or commas\nL-1042\nL-1043'}
          />
          <button type="button" className="btn" onClick={addBulk} disabled={!bulk.trim()}>
            Add to list
          </button>
        </div>

        <form onSubmit={onSubmit}>
          <div className="lead-rows">
            {rows.map((r, i) => (
              <div className="lead-row" key={i}>
                <Field label="Lead ID">
                  <input
                    required={i === 0}
                    value={r.lead_id}
                    onChange={(e) => update(i, 'lead_id', e.target.value)}
                    placeholder="lead_id or uuid"
                  />
                </Field>
                <Field label="Job title">
                  <input value={r.job_title} onChange={(e) => update(i, 'job_title', e.target.value)} placeholder="e.g. CISO" />
                </Field>
                <Field label="Company">
                  <input value={r.company} onChange={(e) => update(i, 'company', e.target.value)} />
                </Field>
                <Field label="Timezone">
                  <input value={r.timezone} onChange={(e) => update(i, 'timezone', e.target.value)} placeholder="e.g. Asia/Dhaka" />
                </Field>
                {rows.length > 1 ? (
                  <button
                    type="button"
                    className="btn btn-ghost btn-icon"
                    aria-label={`Remove row ${i + 1}`}
                    onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                ) : (
                  <span className="btn-icon-spacer" aria-hidden="true" />
                )}
              </div>
            ))}
          </div>
          <div className="form-actions">
            <button type="button" className="btn btn-ghost" onClick={() => setRows((rs) => [...rs, emptyRow()])}>
              + Add row
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy || !count}>
              {busy ? <Spinner /> : null} Enroll {count > 1 ? `${count} leads` : 'lead'}
            </button>
          </div>
        </form>

        <ErrorNote error={error} />

        {result && (
          <div className="table-wrap">
            <p className="muted">
              {formatNumber(result.enrolled)} of {formatNumber(result.results.length)} enrolled.
            </p>
            <table>
              <thead>
                <tr>
                  <th>Lead</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {result.results.map((r) => {
                  const [tone, label] = enrollOutcome(r.outcome)
                  return (
                    <tr key={r.lead_id}>
                      <td><code>{r.lead_id}</code></td>
                      <td><Badge tone={tone}>{label}</Badge></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </details>
  )
}

export default function Enroll() {
  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Leads</h1>
          <p className="muted">
            Everyone in your shared <code>leads</code> table. <strong>Enroll</strong> starts the cold sequence;{' '}
            <strong>Remove</strong> cancels the emails not sent yet. The shared tables are never changed.
          </p>
        </div>
      </div>
      <LeadBrowser />
      <EnrollById />
    </div>
  )
}

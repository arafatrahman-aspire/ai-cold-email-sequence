import { useState } from 'react'
import { api } from '../api.js'
import { enrollOutcome, formatNumber } from '../labels.js'
import { Badge, Card, ErrorNote, Field, Spinner, useAction } from './ui.jsx'

const emptyRow = () => ({ lead_id: '', job_title: '', company: '', timezone: '' })

export default function Enroll() {
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
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Enroll leads</h1>
          <p className="muted">
            Add existing leads from your <code>leads</code> table to cold outreach. Nothing is
            written to the shared tables.
          </p>
        </div>
      </div>

      <Card title="Paste many IDs" subtitle="Separated by new lines, spaces or commas.">
        <div className="inline-form">
          <textarea
            rows={3}
            value={bulk}
            onChange={(e) => setBulk(e.target.value)}
            placeholder={'L-1042\nL-1043\n3f2c…-uuid'}
          />
          <button type="button" className="btn" onClick={addBulk} disabled={!bulk.trim()}>
            Add to list
          </button>
        </div>
      </Card>

      <Card
        title="Leads to enroll"
        subtitle="Only the lead ID is required. Fill the rest when your data is missing or wrong: job title decides the persona, timezone decides send hours."
      >
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
      </Card>

      <ErrorNote error={error} />

      {result && (
        <Card
          title="Result"
          subtitle={`${formatNumber(result.enrolled)} of ${formatNumber(result.results.length)} enrolled. New enrollments are drafted within 5 minutes, or run "Draft sequences" on the Overview.`}
        >
          <div className="table-wrap">
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
                      <td>
                        <code>{r.lead_id}</code>
                      </td>
                      <td>
                        <Badge tone={tone}>{label}</Badge>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

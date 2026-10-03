import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Badge, Card, ErrorNote, Field, Spinner, useAction } from './ui.jsx'

const STUCK = ['manual_review', 'failed']

// The drafting step records every problem it found, separated by "; ".
function reasons(lastError) {
  return (lastError || 'No reason recorded').split('; ').filter(Boolean)
}

function StuckLead({ lead, onRetried }) {
  const [open, setOpen] = useState(false)
  const [jobTitle, setJobTitle] = useState(lead.job_title || '')
  const [company, setCompany] = useState(lead.company || '')
  const [{ busy, error }, retry] = useAction(() =>
    api.retry(lead.lead_id, {
      job_title: jobTitle.trim() || null,
      company: company.trim() || null,
    }),
  )

  const onRetry = async () => {
    if ((await retry()) !== undefined) onRetried()
  }

  return (
    <li className="stuck">
      <div className="stuck-head">
        <div className="stuck-who">
          <strong>{lead.email}</strong>
          <span className="muted">
            {lead.job_title || 'No job title'}
            {lead.company ? ` · ${lead.company}` : ''}
          </span>
        </div>
        <Badge tone={lead.status === 'failed' ? 'critical' : 'warning'}>
          {lead.status === 'failed' ? 'Failed' : 'Needs review'}
        </Badge>
      </div>
      <ul className="reasons">
        {reasons(lead.last_error).map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      {open && (
        <div className="form-grid stuck-fix">
          <Field label="Job title" hint="Decides the persona and the angle of the emails.">
            <input value={jobTitle} onChange={(e) => setJobTitle(e.target.value)} placeholder="e.g. IT Manager" />
          </Field>
          <Field label="Company">
            <input value={company} onChange={(e) => setCompany(e.target.value)} />
          </Field>
        </div>
      )}
      <ErrorNote error={error} />
      <div className="form-actions">
        {!open && (
          <button className="btn btn-ghost btn-sm" onClick={() => setOpen(true)}>
            Edit details
          </button>
        )}
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={onRetry}>
          {busy ? <Spinner /> : null} {open ? 'Save & retry' : 'Retry drafting'}
        </button>
      </div>
    </li>
  )
}

// Leads the drafting step could not finish: why, and a way to try again.
export default function NeedsAttention({ refreshKey, onChanged }) {
  const [leads, setLeads] = useState(null)
  const [{ error }, load] = useAction(async () => {
    const res = await api.enrollments(STUCK)
    setLeads(res.enrollments || [])
  })

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey])

  if (error?.message?.includes('not found') && error.message.includes('list_enrollments')) {
    return (
      <div className="note note-warning">
        <Badge tone="warning">Setup</Badge>
        <span>
          To review stuck leads here, run <code>supabase/migrations/0003_review.sql</code> in the
          Supabase SQL Editor.
        </span>
      </div>
    )
  }
  if (error && error.status !== 0) return <ErrorNote error={error} onRetry={load} />
  if (!leads?.length) return null

  return (
    <Card
      title="Needs attention"
      subtitle="The AI could not produce emails that passed the quality checks, so nothing was sent. Fix the details if needed and retry; it is drafted again on the next run."
    >
      <ul className="stuck-list">
        {leads.map((lead) => (
          <StuckLead
            key={lead.lead_id}
            lead={lead}
            onRetried={() => {
              load()
              onChanged()
            }}
          />
        ))}
      </ul>
    </Card>
  )
}

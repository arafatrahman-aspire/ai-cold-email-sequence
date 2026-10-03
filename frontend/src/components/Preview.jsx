import { useState } from 'react'
import { api } from '../api.js'
import { PERSONAS } from '../labels.js'
import { Badge, Card, ErrorNote, Field, Spinner, useAction } from './ui.jsx'

export default function Preview() {
  const [lead, setLead] = useState({
    first_name: 'Dana',
    last_name: '',
    company: 'Northwind Logistics',
    job_title: 'Chief Information Security Officer',
    timezone: 'UTC',
  })
  const [steps, setSteps] = useState(4)
  const [{ busy, error, result }, generate] = useAction(() => {
    const body = Object.fromEntries(Object.entries(lead).filter(([, v]) => v.trim()))
    return api.preview(body, steps)
  })

  const set = (key) => (e) => setLead((l) => ({ ...l, [key]: e.target.value }))
  const ok = result?.status === 'ok'

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Preview a sequence</h1>
          <p className="muted">Draft emails for a made-up lead. Nothing is saved or sent.</p>
        </div>
      </div>

      <Card>
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault()
            generate()
          }}
        >
          <Field label="First name">
            <input value={lead.first_name} onChange={set('first_name')} />
          </Field>
          <Field label="Last name">
            <input value={lead.last_name} onChange={set('last_name')} />
          </Field>
          <Field label="Company">
            <input value={lead.company} onChange={set('company')} />
          </Field>
          <Field label="Job title" hint="Decides the persona.">
            <input value={lead.job_title} onChange={set('job_title')} />
          </Field>
          <Field label="Emails in sequence">
            <input
              type="number"
              min={1}
              max={8}
              value={steps}
              onChange={(e) => setSteps(Math.min(8, Math.max(1, Number(e.target.value) || 1)))}
            />
          </Field>
          <div className="form-actions span-all">
            <button className="btn btn-primary" disabled={busy}>
              {busy ? <Spinner label="Drafting… this can take up to a minute" /> : 'Generate preview'}
            </button>
          </div>
        </form>
      </Card>

      <ErrorNote error={error} />

      {result && (
        <>
          <div className="meta-row">
            <Badge tone={ok ? 'good' : 'warning'}>{ok ? 'Passed validation' : 'Needs review'}</Badge>
            <span>
              Persona: <strong>{PERSONAS[result.persona] || result.persona}</strong> (
              {result.routing_mode === 'llm' ? 'chosen by AI' : 'keyword match'})
            </span>
            {result.model && (
              <span className="muted">
                {result.provider} · {result.model}
              </span>
            )}
          </div>
          {result.validation_errors?.length > 0 && (
            <div className="note note-warning">
              <Badge tone="warning">Validation</Badge>
              <ul className="plain-list">
                {result.validation_errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="emails">
            {result.emails.map((m) => (
              <article className="email" key={m.step_number}>
                <span className="email-step">Email {m.step_number}</span>
                <h3 className="email-subject">{m.subject}</h3>
                <p className="email-body">{m.body}</p>
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

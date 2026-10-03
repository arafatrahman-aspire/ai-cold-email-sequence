import { useState } from 'react'
import { api } from '../api.js'
import { PERSONAS } from '../labels.js'
import { Badge, Card, ErrorNote, Field, Spinner, useAction } from './ui.jsx'

function Suppress() {
  const [email, setEmail] = useState('')
  const [reason, setReason] = useState('manual')
  const [{ busy, error, result }, submit] = useAction(() => api.suppress(email.trim(), reason))

  return (
    <Card
      title="Block an address"
      subtitle="Never email this address again and cancel anything still scheduled for it. Use for opt-outs received by phone or elsewhere."
    >
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <Field label="Email address">
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
        </Field>
        <Field label="Reason">
          <select value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="manual">Manual</option>
            <option value="unsubscribed">Unsubscribed</option>
            <option value="complaint">Complaint</option>
            <option value="hard_bounce">Hard bounce</option>
          </select>
        </Field>
        <div className="form-actions span-all">
          <button className="btn btn-danger" disabled={busy || !email.trim()}>
            {busy ? <Spinner /> : null} Block address
          </button>
        </div>
      </form>
      <ErrorNote error={error} />
      {result && (
        <div className="note note-good">
          <Badge tone="good">Blocked</Badge>
          <span>
            {result.email} · {result.steps_cancelled} scheduled email
            {result.steps_cancelled === 1 ? '' : 's'} cancelled
          </span>
        </div>
      )}
    </Card>
  )
}

function Router() {
  const [title, setTitle] = useState('')
  const [{ busy, error, result }, check] = useAction(() => api.route(title.trim()))
  return (
    <Card title="Which persona?" subtitle="See how a job title is routed before enrolling.">
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault()
          check()
        }}
      >
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Head of People Operations" aria-label="Job title" />
        <button className="btn" disabled={busy || !title.trim()}>
          {busy ? <Spinner /> : null} Check
        </button>
      </form>
      <ErrorNote error={error} />
      {result && (
        <div className="meta-row">
          <strong>{PERSONAS[result.persona] || result.persona}</strong>
          <span className="muted">
            {result.routing_mode === 'llm'
              ? `chosen by AI${result.confidence != null ? ` (confidence ${Math.round(result.confidence * 100)}%)` : ''}`
              : 'keyword match'}
          </span>
        </div>
      )}
    </Card>
  )
}

export default function Tools() {
  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Tools</h1>
        </div>
      </div>
      <div className="grid-2">
        <Suppress />
        <Router />
      </div>
    </div>
  )
}

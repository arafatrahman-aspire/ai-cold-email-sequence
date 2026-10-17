import { useState } from 'react'
import { api } from '../api.js'
import { ErrorNote, Spinner, useAction } from './ui.jsx'

// The email exactly as it will arrive: the designed HTML in a sandboxed
// frame (no scripts, no clicks), with the plain-text version one click away.
export function EmailFrame({ email }) {
  const [plain, setPlain] = useState(false)
  if (!email) return null
  const showPlain = plain || !email.html
  return (
    <div className="email-frame">
      <div className="email-frame-bar">
        <span className="muted">Subject: <strong>{email.subject || '(none)'}</strong></span>
        {email.html && (
          <button className="btn btn-ghost btn-sm" onClick={() => setPlain(!plain)}>
            {plain ? 'Show designed' : 'Show plain text'}
          </button>
        )}
      </div>
      {email.note && <p className="muted">{email.note}</p>}
      {showPlain
        ? <pre className="email-plain">{email.text}</pre>
        : <iframe title="Email preview" sandbox="" srcDoc={email.html} className="email-iframe" />}
    </div>
  )
}

// A "Preview" button for any email: request = what /email-design/render takes.
export default function EmailPreview({ request, label = 'Preview' }) {
  const [open, setOpen] = useState(false)
  const [{ busy, error, result }, load] = useAction(() => api.renderEmail(request()))
  const toggle = () => {
    if (!open) load()
    setOpen(!open)
  }
  return (
    <>
      <button type="button" className="btn btn-ghost btn-sm" onClick={toggle} aria-expanded={open}>
        {open ? 'Hide preview' : label}
      </button>
      {open && (
        <div className="email-preview-pop">
          {busy ? <Spinner label="Rendering…" /> : <EmailFrame email={result} />}
          <ErrorNote error={error} onRetry={load} />
        </div>
      )}
    </>
  )
}

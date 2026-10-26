import { useRef, useState } from 'react'
import { api } from '../api.js'
import { ErrorNote, Spinner, useAction } from './ui.jsx'

// The email exactly as it will arrive, shown the way an inbox shows it:
// sender, subject, then the designed HTML in a sandboxed frame (no scripts,
// no clicks) that grows to fit, on a desktop or phone width. The plain-text
// version is one click away.
export function EmailFrame({ email }) {
  const [plain, setPlain] = useState(false)
  const [device, setDevice] = useState('desktop')
  const [height, setHeight] = useState(480)
  const frame = useRef(null)
  if (!email) return null
  const showPlain = plain || !email.html
  const fromName = email.from_name || email.from_email || 'You'
  // Same-origin (scripts stay off) only so its height can be read.
  const fit = () => {
    const doc = frame.current?.contentDocument
    if (doc?.body) setHeight(Math.max(240, doc.documentElement.scrollHeight + 8))
  }
  return (
    <div className="email-frame">
      <div className="email-frame-bar">
        <div className="seg" role="tablist" aria-label="Screen">
          {[['desktop', 'Desktop'], ['mobile', 'Phone']].map(([id, label]) => (
            <button key={id} role="tab" aria-selected={device === id} className={device === id ? 'is-active' : ''}
              onClick={() => setDevice(id)}>{label}</button>
          ))}
        </div>
        {email.html && (
          <button className="btn btn-ghost btn-sm" onClick={() => setPlain(!plain)}>
            {plain ? 'Show designed' : 'Show plain text'}
          </button>
        )}
      </div>
      {email.note && <p className="muted">{email.note}</p>}
      <div className={`mail-mock mail-mock-${device}`}>
        <div className="mail-mock-head">
          <span className="inbox-avatar" aria-hidden="true">{fromName[0].toUpperCase()}</span>
          <div className="mail-mock-who">
            <span><strong>{fromName}</strong>{email.from_email && <span className="mail-mock-addr"> &lt;{email.from_email}&gt;</span>}</span>
            <span className="mail-mock-to">to Dana Okafor</span>
          </div>
          <span className="mail-mock-time">now</span>
        </div>
        <h3 className="mail-mock-subject">{email.subject || '(no subject)'}</h3>
        <div className="mail-mock-body">
          {showPlain
            ? <pre className="email-plain">{email.text}</pre>
            : <iframe ref={frame} title="Email preview" sandbox="allow-same-origin" srcDoc={email.html}
                onLoad={fit} className="email-iframe" style={{ height }} />}
        </div>
      </div>
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

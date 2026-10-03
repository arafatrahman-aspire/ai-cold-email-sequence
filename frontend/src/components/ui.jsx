import { useLayoutEffect, useRef, useState } from 'react'

const ICONS = {
  good: 'M5 10.5l3.2 3.2L15 7',
  warning: 'M10 6v5M10 14h.01',
  critical: 'M7 7l6 6M13 7l-6 6',
  neutral: 'M6 10h8',
}

// Status is never color alone: every badge carries an icon and a label.
export function Badge({ tone = 'neutral', children }) {
  return (
    <span className={`badge badge-${tone}`}>
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d={ICONS[tone] || ICONS.neutral} />
      </svg>
      {children}
    </span>
  )
}

export function Card({ title, subtitle, actions, children, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p className="muted">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function ErrorNote({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="note note-critical" role="alert">
      <Badge tone="critical">Error</Badge>
      <span>{error.message || String(error)}</span>
      {onRetry && (
        <button className="btn btn-ghost btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

export function Spinner({ label }) {
  return (
    <span className="spinner-wrap" role="status">
      <span className="spinner" aria-hidden="true" />
      {label && <span>{label}</span>}
    </span>
  )
}

// Wraps an async action with busy / error / result state.
export function useAction(fn) {
  const [state, setState] = useState({ busy: false, error: null, result: null })
  const run = async (...args) => {
    setState({ busy: true, error: null, result: null })
    try {
      const result = await fn(...args)
      setState({ busy: false, error: null, result })
      return result
    } catch (error) {
      setState({ busy: false, error, result: null })
      return undefined
    }
  }
  return [state, run]
}

export function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

// "Inbox checked every 10 min · next check 14:20 (in 6 min)", from /health.
export function ReplyCheckSchedule({ schedule }) {
  const job = schedule?.poller
  if (!schedule) return null
  if (!job) {
    return <span className="schedule-note">Background checks are off (RUN_WORKERS=false): use "Check replies".</span>
  }
  const mins = Math.round(job.interval_seconds / 60)
  const next = job.next_run ? new Date(job.next_run) : null
  const inMin = next ? Math.max(0, Math.round((next - Date.now()) / 60000)) : null
  return (
    <span className="schedule-note">
      <span className="pulse" aria-hidden="true" />
      Inbox checked every {mins} min
      {next && <> · next check {next.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} ({inMin === 0 ? 'now' : `in ${inMin} min`})</>}
    </span>
  )
}

// A textarea that grows to fit its text, so nothing is hidden behind a scrollbar.
export function AutoTextarea({ value, minRows = 4, ...props }) {
  const ref = useRef(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight + 2}px`
  }, [value])
  return <textarea ref={ref} rows={minRows} value={value} {...props} style={{ overflow: 'hidden', resize: 'none' }} />
}

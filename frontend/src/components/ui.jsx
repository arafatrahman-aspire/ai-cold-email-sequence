import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react'

// The signed-in user ({id, email, role}). Admins (admin / superadmin) also see
// configuration, test mode and dev tools; the server enforces the same rule.
export const UserContext = createContext(null)
export const ADMIN_ROLES = ['admin', 'superadmin']
export const isAdminUser = (user) => ADMIN_ROLES.includes(user?.role)
export const useIsAdmin = () => isAdminUser(useContext(UserContext))

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


// --- Toasts -------------------------------------------------------------------
// toast.success('Saved') from anywhere (no hook needed); <Toaster /> renders
// them once, at the app root. Errors stay longer than confirmations.
const toastListeners = new Set()
let toastSeq = 0
function pushToast(tone, message, { title, duration } = {}) {
  const item = { id: ++toastSeq, tone, title, message, duration: duration ?? (tone === 'critical' ? 9000 : 5000) }
  toastListeners.forEach((fn) => fn(item))
}
export const toast = {
  success: (message, opts) => pushToast('good', message, opts),
  info: (message, opts) => pushToast('neutral', message, opts),
  warning: (message, opts) => pushToast('warning', message, opts),
  error: (message, opts) => pushToast('critical', message, opts),
}

export function Toaster() {
  const [items, setItems] = useState([])
  const close = (id) => setItems((list) => list.filter((t) => t.id !== id))
  useEffect(() => {
    const add = (item) => {
      setItems((list) => [...list.slice(-3), item])
      setTimeout(() => close(item.id), item.duration)
    }
    toastListeners.add(add)
    return () => { toastListeners.delete(add) }
  }, [])
  return (
    <div className="toaster" role="region" aria-label="Notifications" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}`} role={t.tone === 'critical' ? 'alert' : 'status'}>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d={ICONS[t.tone] || ICONS.neutral} /></svg>
          <div className="toast-body">
            {t.title && <strong>{t.title}</strong>}
            <span>{t.message}</span>
          </div>
          <button className="toast-close" onClick={() => close(t.id)} aria-label="Dismiss">×</button>
        </div>
      ))}
    </div>
  )
}

// --- Modal ----------------------------------------------------------------------
// A native <dialog>: focus is trapped, Esc closes it, the page behind is inert.
export function Modal({ open, title, onClose, children, footer, size = 'md' }) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (open && !el.open) el.showModal()
    if (!open && el.open) el.close()
  }, [open])
  return (
    <dialog ref={ref} className={`modal modal-${size}`} onCancel={(e) => { e.preventDefault(); onClose?.() }}
      onClick={(e) => { if (e.target === ref.current) onClose?.() }}>
      {open && (
        <div className="modal-inner">
          <header className="modal-head">
            <h2>{title}</h2>
            {onClose && <button className="modal-x" onClick={onClose} aria-label="Close">×</button>}
          </header>
          <div className="modal-body">{children}</div>
          {footer && <footer className="modal-foot">{footer}</footer>}
        </div>
      )}
    </dialog>
  )
}

// await confirmDialog('Remove 3 leads?\n\nUnsent emails are cancelled.')
// -> true / false. The first paragraph is the title, the rest the details;
// removing, ending or deleting gets a red button. <ConfirmHost /> renders it.
let openConfirm = null
export function confirmDialog(message, { confirmLabel, danger } = {}) {
  let [title, ...rest] = String(message).split(/\n\s*\n/)
  // "End it? Unsent emails are cancelled." -> title "End it?", the rest below.
  const q = !rest.length && title.match(/^([^?]+\?)\s+(.+)$/s)
  if (q) { title = q[1]; rest = [q[2]] }
  const verb = title.split(/\s/)[0].replace(/\W/g, '')
  const req = {
    title, body: rest.join('\n\n'),
    danger: danger ?? /^(remove|end|delete|reject|cancel)\b/i.test(title),
    // The button says what happens ("Enroll", "Book", "End"), not "OK".
    confirmLabel: confirmLabel || (/^(let|is|are|do|should)$/i.test(verb) || !verb ? 'Confirm' : verb),
  }
  if (!openConfirm) return Promise.resolve(window.confirm(message))
  return new Promise((resolve) => openConfirm({ ...req, resolve }))
}

export function ConfirmHost() {
  const [req, setReq] = useState(null)
  useEffect(() => { openConfirm = setReq; return () => { openConfirm = null } }, [])
  const answer = (value) => { req?.resolve(value); setReq(null) }
  return (
    <Modal open={!!req} title={req?.title || ''} onClose={() => answer(false)} size="sm"
      footer={req && (
        <>
          <button className="btn btn-ghost" onClick={() => answer(false)}>Cancel</button>
          <button className={`btn ${req.danger ? 'btn-danger' : 'btn-primary'}`} autoFocus onClick={() => answer(true)}>
            {req.confirmLabel}
          </button>
        </>
      )}>
      {req?.body && <p className="modal-text">{req.body}</p>}
    </Modal>
  )
}

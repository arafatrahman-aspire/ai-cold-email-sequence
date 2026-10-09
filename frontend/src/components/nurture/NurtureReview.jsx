import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { EXIT_REASONS, MESSAGE_STATUS, NURTURE_PERSONAS, relativeTime, shortDateTime, TEMPERATURES } from '../../labels.js'
import { AutoTextarea, Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

function MessageCard({ m, onChanged, dev }) {
  const [subject, setSubject] = useState(m.subject || '')
  const [preheader, setPreheader] = useState(m.preheader || '')
  const [body, setBody] = useState(m.body || '')
  useEffect(() => {
    setSubject(m.subject || '')
    setPreheader(m.preheader || '')
    setBody(m.body || '')
  }, [m.subject, m.preheader, m.body])
  const dirty = subject !== (m.subject || '') || preheader !== (m.preheader || '') || body !== (m.body || '')
  const [{ busy, error }, act] = useAction(async (what) => {
    if (dirty && what !== 'reject') await api.nurtureEditMessage(m.id, { subject: subject.trim(), preheader: preheader.trim(), body: body.trim() })
    if (what === 'approve' || what === 'reject') await api.nurtureDecide(m.id, what)
    if (what === 'send') {
      if (!window.confirm(`Send this email to ${m.email} now?`)) return false
      await api.nurtureSendNow(m.id)
    }
    onChanged()
    return true
  })
  const [tone, label] = MESSAGE_STATUS[m.status] || ['neutral', m.status]
  return (
    <li className="draft">
      <div className="reply-head">
        <div className="reply-who">
          <strong>To {[m.first_name, m.email].filter(Boolean).join(' · ')}</strong>
          <span className="muted">{[m.job_title, m.company].filter(Boolean).join(' · ') || ' '}</span>
        </div>
        <div className="reply-meta">
          <span className="badge-row">
            <Badge tone={tone}>{label}</Badge>
            <Badge tone={m.source === 'fallback' ? 'warning' : 'neutral'}>{m.source === 'fallback' ? 'Fallback text' : m.source === 'edited' ? 'Edited' : 'AI'}</Badge>
          </span>
          <span className="muted">
            Email {m.step} of 6 · {NURTURE_PERSONAS[m.persona]} · {TEMPERATURES[m.temperature]}
            {m.ai_log?.judge_score != null && ` · judge ${Number(m.ai_log.judge_score).toFixed(2)}`}
          </span>
        </div>
      </div>
      <div className="nurture-edit">
        <input aria-label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
        <input aria-label="Preview text" value={preheader} placeholder="Preview text" onChange={(e) => setPreheader(e.target.value)} />
        <AutoTextarea aria-label="Body" minRows={7} value={body} onChange={(e) => setBody(e.target.value)} />
        <p className="muted">
          Keep <code>{'{{CTA_DEMO}}'}</code> and <code>{'{{CTA_PRICING}}'}</code> (they become the tracked links); never type a web
          address. The greeting, signature and unsubscribe footer are added automatically.
        </p>
      </div>
      <div className="reply-actions">
        <span className={m.status === 'needs_approval' ? 'auto-send waits' : 'auto-send'}>
          {dev
            ? <>Dev mode: goes out only when you click Send now{m.status === 'needs_approval' ? ' (sending also approves it)' : ''}</>
            : m.status === 'needs_approval'
            ? <>Waits for your approval{new Date(m.send_at) < new Date() ? ' (its send time has passed)' : ''}: due {shortDateTime(m.send_at)}</>
            : new Date(m.send_at) <= new Date()
              ? <>Due now: goes out on the next send cycle unless you reject it</>
              : <>Sends <strong>{relativeTime(m.send_at)}</strong> ({shortDateTime(m.send_at)}) unless you reject it</>}
        </span>
        <div className="draft-buttons">
          {dirty && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { setSubject(m.subject || ''); setPreheader(m.preheader || ''); setBody(m.body || '') }}>Undo edits</button>}
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => act('reject')} title="It is written again (after two rejections the pre-approved text is used)">Reject</button>
          {dirty && <button className="btn btn-sm" disabled={busy} onClick={() => act('save')}>Save edits</button>}
          {dev && (
            <button className="btn btn-primary btn-sm" disabled={busy || !subject.trim() || !body.trim()} onClick={() => act('send')}
              title="APP_ENV=dev: send this email now, ignoring its scheduled time and the sending window">
              {busy ? <Spinner /> : null} {dirty ? 'Save & send now' : 'Send now'}
            </button>
          )}
          {m.status === 'needs_approval' && (
            <button className="btn btn-primary btn-sm" disabled={busy || !subject.trim() || !body.trim()} onClick={() => act('approve')}>
              {busy ? <Spinner /> : null} {dirty ? 'Save & approve' : 'Approve'}
            </button>
          )}
        </div>
      </div>
      <ErrorNote error={error} />
    </li>
  )
}

export default function NurtureReview() {
  const [messages, setMessages] = useState(null)
  const [mode, setMode] = useState(null)
  const [dev, setDev] = useState(false)
  const [{ busy, error }, load] = useAction(async () => {
    const [r, s, st] = await Promise.all([api.nurtureReview(), api.nurtureSettings(), api.nurtureStatus()])
    setMessages(r.messages || [])
    setMode(s.settings.approval)
    setDev(st.environment === 'dev')
  })
  useEffect(() => {
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const waiting = (messages || []).filter((m) => m.status === 'needs_approval')
  const upcoming = (messages || []).filter((m) => m.status !== 'needs_approval')
  const modeText = !mode ? '' : mode.mode === 'all' ? 'Pilot mode: every AI draft waits for approval.'
    : mode.mode === 'sample' ? `About ${Math.round(mode.sample_rate * 100)}% of AI drafts wait for approval.`
      : 'Drafts send on schedule unless you reject them.'
  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Review queue</h1>
          <p className="muted">
            Emails written ahead of their send time.{' '}
            {dev ? 'APP_ENV=dev: nothing is sent automatically; use Send now to test each email.' : `${modeText} Change this in Settings → Review.`}
          </p>
        </div>
        <button className="btn" onClick={() => load()} disabled={busy}>{busy ? <Spinner /> : null} Refresh</button>
      </div>
      <ErrorNote error={error} onRetry={load} />
      {messages && (
        <>
          <Card title={`Waiting for approval (${waiting.length})`}>
            {waiting.length ? <ul className="replies">{waiting.map((m) => <MessageCard key={m.id} m={m} onChanged={load} dev={dev} />)}</ul>
              : <p className="muted">Nothing is waiting for you.</p>}
          </Card>
          <Card title={`Going out in the next 3 days (${upcoming.length})`}>
            {upcoming.length ? <ul className="replies">{upcoming.map((m) => <MessageCard key={m.id} m={m} onChanged={load} dev={dev} />)}</ul>
              : <p className="muted">No emails scheduled in the next 3 days.</p>}
          </Card>
        </>
      )}
    </div>
  )
}

export function NurtureHandoffs() {
  const [rows, setRows] = useState(null)
  const [{ busy, error }, load] = useAction(async () => setRows((await api.nurtureHandoffs()).handoffs || []))
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Hand-offs to sales</h1>
          <p className="muted">Leads that became ready: Hot score, a pricing or demo click, or an interested reply. Sales gets each one by email.</p>
        </div>
        <button className="btn" onClick={() => load()} disabled={busy}>{busy ? <Spinner /> : null} Refresh</button>
      </div>
      <ErrorNote error={error} onRetry={load} />
      {rows && (
        <Card>
          {rows.length ? (
            <ul className="replies">
              {rows.map((h) => (
                <li key={h.id} className="reply">
                  <div className="reply-head">
                    <div className="reply-who">
                      <strong>{[h.first_name, h.last_name].filter(Boolean).join(' ') || h.email}</strong>
                      <span className="muted">{[h.email, h.job_title, h.company].filter(Boolean).join(' · ')}</span>
                    </div>
                    <div className="reply-meta">
                      <Badge tone="good">{EXIT_REASONS[h.trigger] || h.trigger}</Badge>
                      <span className="muted">{shortDateTime(h.handed_off_at)} · {relativeTime(h.handed_off_at)}</span>
                    </div>
                  </div>
                  <p className="muted">
                    {NURTURE_PERSONAS[h.persona]} · {TEMPERATURES[h.temperature]} track · {h.step} of 6 emails received
                  </p>
                  {h.summary ? <p>{h.summary}</p> : <p className="muted">Summary is being written…</p>}
                  <p className="muted">
                    {h.sales_notified_at ? `Sales notified ${relativeTime(h.sales_notified_at)}` : 'Notifying sales…'}
                  </p>
                </li>
              ))}
            </ul>
          ) : <p className="muted">No hand-offs yet.</p>}
        </Card>
      )}
    </div>
  )
}

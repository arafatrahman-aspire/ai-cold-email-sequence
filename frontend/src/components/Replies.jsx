import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { PERSONAS } from '../labels.js'
import { Badge, Card, ErrorNote, Field, Spinner, useAction } from './ui.jsx'

const FILTERS = [
  ['reply', 'Replies'],
  ['unsubscribe', 'Unsubscribes'],
  ['bounce', 'Bounces'],
  ['auto_reply', 'Auto-replies'],
  ['all', 'All'],
]

const TYPE_BADGE = {
  reply: ['good', 'Reply'],
  unsubscribe: ['warning', 'Unsubscribed'],
  bounce: ['critical', 'Bounce'],
  auto_reply: ['neutral', 'Auto-reply'],
  unknown: ['neutral', 'Other'],
}

export function AlertSettings() {
  const [address, setAddress] = useState('')
  const [saved, setSaved] = useState(null)
  const [{ busy: loading }, load] = useAction(async () => {
    const res = await api.settings()
    const value = res.settings?.find((s) => s.key === 'reply_alert_email')?.value || ''
    setAddress(value)
    setSaved(value)
  })
  const [{ busy: saving, error: saveError }, save] = useAction(async () => {
    await api.putSetting('reply_alert_email', address.trim())
    setSaved(address.trim())
  })
  const [{ busy: testing, error: testError, result: testResult }, test] = useAction(() => api.testAlert())

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const dirty = address.trim() !== (saved ?? '')
  return (
    <Card
      title="Reply alerts"
      subtitle="Get an email with the prospect's message as soon as they reply. It is sent from the inbox that received the reply."
    >
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault()
          save()
        }}
      >
        <input
          type="email"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder={loading ? 'Loading…' : 'you@company.com (empty = no alerts)'}
          aria-label="Alert email address"
        />
        <button className="btn btn-primary" disabled={saving || !dirty}>
          {saving ? <Spinner /> : null} Save
        </button>
        <button type="button" className="btn" disabled={testing || dirty || !saved} onClick={() => test()}>
          {testing ? <Spinner /> : null} Send test alert
        </button>
      </form>
      <p className="muted alert-status">
        {saved ? (
          <>
            <Badge tone="good">On</Badge>
            <span>
              Alerts go to <strong>{saved}</strong>.
            </span>
          </>
        ) : (
          <>
            <Badge tone="neutral">Off</Badge>
            <span>No alert address set.</span>
          </>
        )}
      </p>
      <ErrorNote error={saveError || testError} />
      {testResult && (
        <div className="note note-good">
          <Badge tone="good">Sent</Badge>
          <span>
            Test alert sent to {testResult.sent_to} from {testResult.from}.
            {testResult.dry_run && ' Dry run is on, so it was not actually delivered.'}
          </span>
        </div>
      )}
    </Card>
  )
}

function ReplyItem({ reply }) {
  const [tone, label] = TYPE_BADGE[reply.event_type] || TYPE_BADGE.unknown
  const when = new Date(reply.received_at)
  const lead = [reply.job_title, reply.company].filter(Boolean).join(' · ')
  return (
    <li className="reply">
      <div className="reply-head">
        <div className="reply-who">
          <strong>{reply.from_email}</strong>
          <span className="muted">
            {lead || 'No job title or company'}
            {reply.persona ? ` · ${PERSONAS[reply.persona] || reply.persona}` : ''}
          </span>
        </div>
        <div className="reply-meta">
          <Badge tone={tone}>{label}</Badge>
          <span className="muted">
            {when.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>
      </div>
      <p className="reply-subject">{reply.subject || '(no subject)'}</p>
      {reply.snippet && <p className="reply-snippet">{reply.snippet}</p>}
      <div className="reply-actions">
        <span className="muted">Received in {reply.inbox_email}</span>
        {reply.gmail_link && (
          <a className="btn btn-sm" href={reply.gmail_link} target="_blank" rel="noreferrer">
            Open in Gmail ↗
          </a>
        )}
      </div>
    </li>
  )
}

export default function Replies() {
  const [filter, setFilter] = useState('reply')
  const [replies, setReplies] = useState(null)
  const [{ busy, error }, load] = useAction(async (type) => {
    setReplies((await api.replies(type)).replies || [])
  })

  useEffect(() => {
    load(filter)
    const id = setInterval(() => load(filter), 60_000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  const missing = error?.message?.includes('list_inbox_events')

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Replies</h1>
          <p className="muted">
            The sequence never answers for you: when a prospect replies, their remaining emails are
            cancelled and it is your turn. Answer from the inbox that received it.
          </p>
        </div>
        <button className="btn" onClick={() => load(filter)} disabled={busy}>
          {busy ? <Spinner /> : null} Refresh
        </button>
      </div>

      <AlertSettings />

      <div className="chips" role="tablist" aria-label="Filter">
        {FILTERS.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={filter === id}
            className={`chip ${filter === id ? 'is-active' : ''}`}
            onClick={() => setFilter(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {missing ? (
        <div className="note note-warning">
          <Badge tone="warning">Setup</Badge>
          <span>
            Run <code>supabase/migrations/0005_replies.sql</code> in the Supabase SQL Editor to see
            replies here.
          </span>
        </div>
      ) : (
        <ErrorNote error={error?.status === 0 ? null : error} onRetry={() => load(filter)} />
      )}

      {replies && !missing && (
        <Card>
          {replies.length ? (
            <ul className="replies">
              {replies.map((r) => (
                <ReplyItem key={r.id} reply={r} />
              ))}
            </ul>
          ) : (
            <p className="muted">Nothing here yet. New mail is checked every 7 minutes, or use "Check replies" on the Overview.</p>
          )}
        </Card>
      )}
    </div>
  )
}

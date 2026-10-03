import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { CATEGORIES, DRAFT_KINDS, relativeTime, shortDateTime } from '../../labels.js'
import { AutoTextarea, Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

const FILTERS = [
  ['pending,approved,failed', 'Waiting'],
  ['sent', 'Sent'],
  ['rejected,cancelled', 'Rejected / cancelled'],
  ['all', 'All'],
]

const STATUS = {
  pending: ['warning', 'Waiting'],
  approved: ['good', 'Approved, sending'],
  sending: ['neutral', 'Sending'],
  sent: ['good', 'Sent'],
  rejected: ['neutral', 'Rejected'],
  cancelled: ['neutral', 'Cancelled'],
  failed: ['critical', 'Send failed'],
}

// Re-render every 30s so countdowns stay current.
function useNow() {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])
  return now
}

function SendInfo({ draft, now }) {
  if (draft.status === 'sent') return <span className="muted">Sent {relativeTime(draft.sent_at, now)}</span>
  if (draft.status === 'approved') return <span className="muted">Approved by {draft.decided_by || 'you'}; goes out within a minute</span>
  if (draft.status === 'rejected') return <span className="muted">Rejected by {draft.decided_by || 'you'}</span>
  if (draft.status === 'cancelled') return <span className="muted">{draft.last_error || 'Cancelled'}</span>
  if (draft.status === 'failed') return <span className="field-error">Not sent: {draft.last_error}</span>
  if (draft.auto_send_at) {
    return (
      <span className="auto-send">
        Sends automatically <strong>{relativeTime(draft.auto_send_at, now)}</strong> ({shortDateTime(draft.auto_send_at)}) unless you act
      </span>
    )
  }
  return <span className="auto-send waits">Waits for your approval: it will not send on its own</span>
}

function DraftCard({ draft, now, onChanged }) {
  const editable = ['pending', 'failed'].includes(draft.status)
  const [subject, setSubject] = useState(draft.subject)
  const [body, setBody] = useState(draft.body)
  useEffect(() => {
    setSubject(draft.subject)
    setBody(draft.body)
  }, [draft.subject, draft.body])
  const dirty = subject !== draft.subject || body !== draft.body

  const [{ busy, error }, act] = useAction(async (what) => {
    if (dirty && what !== 'reject') await api.editDraft(draft.id, subject.trim(), body.trim())
    if (what === 'approve') await api.approveDraft(draft.id)
    if (what === 'reject') await api.rejectDraft(draft.id)
    return true
  })
  const run = async (what) => {
    if (await act(what)) onChanged()
  }

  const [sTone, sLabel] = STATUS[draft.status] || ['neutral', draft.status]
  const [cTone, cLabel] = CATEGORIES[draft.category] || ['neutral', draft.category]

  return (
    <li className="draft">
      <div className="reply-head">
        <div className="reply-who">
          <strong>To {draft.to_email}</strong>
          <span className="muted">{[draft.job_title, draft.company].filter(Boolean).join(' · ') || ' '}</span>
        </div>
        <div className="reply-meta">
          <span className="badge-row">
            <Badge tone={sTone}>{sLabel}</Badge>
            {draft.category && <Badge tone={cTone}>{cLabel}</Badge>}
          </span>
          <span className="muted">{DRAFT_KINDS[draft.kind] || draft.kind}</span>
        </div>
      </div>

      <div className="draft-grid">
        <div className="draft-theirs">
          <span className="did-title">They wrote {relativeTime(draft.reply_received_at, now)}</span>
          <p className="reply-subject">{draft.reply_subject || '(no subject)'}</p>
          <p className="reply-snippet">{draft.reply_snippet}</p>
        </div>
        <div className="draft-ours">
          <span className="did-title">Our reply</span>
          {editable ? (
            <>
              <input aria-label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
              <AutoTextarea aria-label="Reply" minRows={6} value={body} onChange={(e) => setBody(e.target.value)} />
            </>
          ) : (
            <>
              <p className="reply-subject">{draft.subject}</p>
              <p className="reply-snippet">{draft.body}</p>
            </>
          )}
          {draft.offered_slots?.length > 0 && (
            <p className="muted slots-note">
              Offers {draft.offered_slots.length} time{draft.offered_slots.length > 1 ? 's' : ''}:{' '}
              {draft.offered_slots.map((s) => shortDateTime(s.start)).join(' · ')} (your time)
            </p>
          )}
        </div>
      </div>

      <div className="reply-actions">
        <SendInfo draft={draft} now={now} />
        {editable && (
          <div className="draft-buttons">
            {dirty && (
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { setSubject(draft.subject); setBody(draft.body) }}>
                Undo edits
              </button>
            )}
            {draft.status === 'pending' && (
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => run('reject')}>
                Reject
              </button>
            )}
            {dirty && (
              <button className="btn btn-sm" disabled={busy || !subject.trim() || !body.trim()} onClick={() => run('save')}>
                Save edits
              </button>
            )}
            <button className="btn btn-primary btn-sm" disabled={busy || !subject.trim() || !body.trim()} onClick={() => run('approve')}>
              {busy ? <Spinner /> : null} {draft.status === 'failed' ? 'Retry send' : dirty ? 'Save & send' : 'Approve & send'}
            </button>
          </div>
        )}
      </div>
      <ErrorNote error={error} />
    </li>
  )
}

export default function Drafts() {
  const [filter, setFilter] = useState(FILTERS[0][0])
  const [drafts, setDrafts] = useState(null)
  const now = useNow()
  const [{ busy, error }, load] = useAction(async () => setDrafts((await api.drafts(filter)).drafts || []))

  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Drafts</h1>
          <p className="muted">
            AI-written replies. Approve, edit or reject them; if you do nothing, most send on their own after the
            delay set in Settings, inside the lead's business hours. Unsure ones always wait for you.
          </p>
        </div>
        <button className="btn" onClick={() => load()} disabled={busy}>
          {busy ? <Spinner /> : null} Refresh
        </button>
      </div>

      <div className="chips" role="tablist" aria-label="Filter drafts">
        {FILTERS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={filter === id}
            className={`chip ${filter === id ? 'is-active' : ''}`} onClick={() => setFilter(id)}>
            {label}
          </button>
        ))}
      </div>
      <ErrorNote error={error?.status === 0 ? null : error} onRetry={load} />

      {drafts && (
        <Card>
          {drafts.length ? (
            <ul className="replies">
              {drafts.map((d) => (
                <DraftCard key={d.id} draft={d} now={now} onChanged={load} />
              ))}
            </ul>
          ) : (
            <p className="muted">No drafts here.</p>
          )}
        </Card>
      )}
    </div>
  )
}

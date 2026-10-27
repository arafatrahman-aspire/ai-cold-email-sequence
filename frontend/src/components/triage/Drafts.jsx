import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { CATEGORIES, DRAFT_KINDS, relativeTime, shortDateTime } from '../../labels.js'
import { AutoTextarea, Badge, Card, ErrorNote, Field, Modal, Spinner, toast, useAction, useAutoRefresh } from '../ui.jsx'
import { LeadLink } from '../LeadJourney.jsx'
import EmailPreview from '../EmailPreview.jsx'

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

// The answer library, loaded once per page view: inserting approved wording
// while editing an answer.
let snippetCache = null
export const forgetSnippets = () => { snippetCache = null }
function useSnippets() {
  const [snippets, setSnippets] = useState(snippetCache || [])
  useEffect(() => {
    if (snippetCache) return
    api.snippets().then((r) => { snippetCache = r.snippets || []; setSnippets(snippetCache) }).catch(() => {})
  }, [])
  return snippets
}

// Write a new answer to the same reply, optionally steered ("shorter",
// "offer Thursday instead"). Keeps the reply's category; the new answer
// follows the usual send rules.
function RedraftButton({ draft, onChanged }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [{ busy, error }, go] = useAction(async () => {
    await api.redraft(draft.event_id, draft.category, text.trim())
    toast.success('New answer written. Review it below.')
    setOpen(false)
    setText('')
    onChanged()
    return true
  })
  return (
    <>
      <button className="btn btn-sm" onClick={() => setOpen(true)}>Redraft</button>
      <Modal open={open} title="Write a new answer" onClose={() => !busy && setOpen(false)}
        footer={(
          <>
            <button className="btn btn-ghost" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn btn-primary" disabled={busy} onClick={() => go()}>
              {busy ? <Spinner /> : null} Write new answer
            </button>
          </>
        )}>
        <div className="stack">
          <p className="modal-text">
            The AI writes a fresh answer to {draft.to_email}
            {draft.status === 'pending' ? ', replacing this one' : ''}. If they asked for a time that is free,
            it is booked. Nothing is sent without your approval or the usual delay.
          </p>
          <Field label="Instructions for the AI (optional)"
            hint="For example: Shorter. Offer Thursday or Friday afternoon. Don't mention pricing.">
            <AutoTextarea minRows={3} maxLength={800} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
          </Field>
          <ErrorNote error={error} />
        </div>
      </Modal>
    </>
  )
}

export function DraftCard({ draft, now, onChanged, compact = false }) {
  const snippets = useSnippets()
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
      {!compact && <div className="reply-head">
        <div className="reply-who">
          <strong>To <LeadLink leadId={draft.lead_id}>{draft.to_email}</LeadLink></strong>
          <span className="muted">{[draft.job_title, draft.company].filter(Boolean).join(' · ') || ' '}</span>
        </div>
        <div className="reply-meta">
          <span className="badge-row">
            <Badge tone={sTone}>{sLabel}</Badge>
            {draft.category && <Badge tone={cTone}>{cLabel}</Badge>}
          </span>
          <span className="muted">{DRAFT_KINDS[draft.kind] || draft.kind}</span>
        </div>
      </div>}
      {compact && (
        <div className="reply-head">
          <span className="did-title">Our answer: {DRAFT_KINDS[draft.kind] || draft.kind}</span>
          <Badge tone={sTone}>{sLabel}</Badge>
        </div>
      )}

      <div className={compact ? 'draft-grid draft-compact' : 'draft-grid'}>
        {!compact && <div className="draft-theirs">
          <span className="did-title">They wrote {relativeTime(draft.reply_received_at, now)}</span>
          <p className="reply-subject">{draft.reply_subject || '(no subject)'}</p>
          <p className="reply-snippet">{draft.reply_snippet}</p>
        </div>}
        <div className="draft-ours">
          <span className="did-title">Our reply</span>
          {editable ? (
            <>
              <input aria-label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
              <AutoTextarea aria-label="Reply" minRows={6} value={body} onChange={(e) => setBody(e.target.value)} />
              {snippets.length > 0 && (
                <select className="snippet-pick" value="" aria-label="Insert from the answer library"
                  onChange={(e) => {
                    const s = snippets.find((x) => x.id === e.target.value)
                    if (s) setBody((b) => b.replace(/\s*$/, '') + '\n\n' + s.text)
                  }}>
                  <option value="">Insert from the answer library…</option>
                  {snippets.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
                </select>
              )}
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
        <EmailPreview label="Preview as sent" request={() => ({ kind: 'reply', subject, body })} />
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
            {draft.status === 'pending' && draft.event_id && <RedraftButton draft={draft} onChanged={onChanged} />}
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
        {['rejected', 'cancelled'].includes(draft.status) && draft.event_id && (
          <div className="draft-buttons">
            <RedraftButton draft={draft} onChanged={onChanged} />
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

  useAutoRefresh(load, [filter])

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

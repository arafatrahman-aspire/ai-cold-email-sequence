import { useEffect, useState } from 'react'
import { api } from '../api.js'
import {
  CATEGORIES, DRAFT_KINDS, ENROLLMENT_BADGE, EXIT_REASONS, MESSAGE_STATUS, NURTURE_PERSONAS,
  NURTURE_STATUS, TEMPERATURES, TRIAGE_STATUS, relativeTime, shortDateTime,
} from '../labels.js'
import { Badge, Card, ErrorNote, Spinner, useAction } from './ui.jsx'

// Link to a lead's journey page from anywhere a lead is shown.
export const journeyHref = (leadId) => `#lead/${leadId}`
export function LeadLink({ leadId, children }) {
  if (!leadId) return children
  return <a href={journeyHref(leadId)} title="See everything that happened with this lead">{children}</a>
}

const ANSWER_STATUS = {
  pending: ['warning', 'Waiting'], approved: ['good', 'Approved'], sending: ['neutral', 'Sending'],
  sent: ['good', 'Sent'], rejected: ['neutral', 'Rejected'], cancelled: ['neutral', 'Cancelled'],
  failed: ['critical', 'Failed'],
}
const COLD_STATUS = { pending: ['neutral', 'Scheduled'], sending: ['neutral', 'Sending'], failed: ['critical', 'Failed'] }
const INBOUND = { reply: 'Replied', bounce: 'Bounced', unsubscribe: 'Unsubscribed', auto_reply: 'Auto-reply', unknown: 'Email' }
const LINKS = { demo: 'the demo link', pricing: 'the pricing link', resource: 'a resource' }

// One line of the timeline: [who/what, badge, text, body].
function describe(it) {
  switch (it.type) {
    case 'cold_sent':
      return { side: 'out', title: `Cold email ${it.step} sent`, badge: null, subject: it.subject, body: it.body }
    case 'cold_scheduled':
      return { side: 'out', title: `Cold email ${it.step}`, badge: COLD_STATUS[it.status], subject: it.subject, body: it.body, note: it.error }
    case 'inbound': {
      const cat = CATEGORIES[it.category]
      return {
        side: 'in',
        title: `${INBOUND[it.event_type] || 'Email'}${it.source === 'nurture' ? ' (to a nurture email)' : ''}`,
        badge: cat ? [cat[0], cat[1] + (it.set_by_person ? ' (set by a person)' : '')] : TRIAGE_STATUS[it.triage_status],
        subject: it.subject, body: it.snippet, note: it.summary,
        actions: it.actions,
      }
    }
    case 'answer':
      return {
        side: 'out', title: `Our answer: ${DRAFT_KINDS[it.kind] || it.kind}`, badge: ANSWER_STATUS[it.status],
        subject: it.subject, body: it.body,
        note: it.status === 'pending' && it.auto_send_at ? `Sends ${relativeTime(it.auto_send_at)} unless someone acts` : it.error,
      }
    case 'meeting':
      return {
        side: 'event', title: `Meeting (booked through the ${it.how})`,
        badge: it.status === 'cancelled' ? ['neutral', 'Cancelled'] : new Date(it.at) > new Date() ? ['good', 'Upcoming'] : ['neutral', 'Past'],
        link: it.meeting_url,
      }
    case 'nurture_sent':
    case 'nurture_scheduled':
      return {
        side: 'out', title: `Nurture email ${it.step} of 6`, badge: it.type === 'nurture_sent' ? null : MESSAGE_STATUS[it.status],
        subject: it.subject, body: it.body, note: it.source === 'fallback' ? 'Standard text (the AI draft was not used)' : it.error,
      }
    case 'nurture_event': {
      const d = it.detail || {}
      const text = {
        enrolled: 'Joined Email Nurture',
        click: d.bot ? `Link scanned by a mail filter (${LINKS[d.link] || d.link})` : `Clicked ${LINKS[d.link] || d.link} in email ${d.step}`,
        handoff: `Handed to sales: ${EXIT_REASONS[d.trigger] || d.trigger}`,
        paused: 'Nurture paused', active: 'Nurture resumed', held: 'Nurture held for review',
        exited: `Left nurture${d.reason ? `: ${EXIT_REASONS[d.reason] || d.reason}` : ''}`,
        completed: 'Finished all nurture emails',
        unsubscribed: 'Unsubscribed from nurture',
        persona: `Persona changed to ${NURTURE_PERSONAS[d.to] || d.to}`,
        track: `Moved to the ${TEMPERATURES[d.to] || d.to} track`,
        sent_to_sales: `Sent to sales again (${d.to || ''}) by ${d.by || 'someone'}`,
      }[it.kind] || it.kind
      return { side: 'event', title: text, badge: it.kind === 'handoff' ? ['good', 'Hand-off'] : null }
    }
    default:
      return { side: 'event', title: it.type }
  }
}

function TimelineItem({ it }) {
  const [open, setOpen] = useState(false)
  const d = describe(it)
  const future = it.at && new Date(it.at) > new Date()
  return (
    <li className={`journey-item journey-${d.side}`}>
      <div className="journey-when">
        <span>{it.at ? shortDateTime(it.at) : '—'}</span>
        <span className="muted">{it.at ? relativeTime(it.at) : ''}</span>
      </div>
      <div className="journey-what">
        <div className="journey-head">
          <strong>{d.title}</strong>
          {d.badge && <Badge tone={d.badge[0]}>{d.badge[1]}</Badge>}
          {future && !d.badge && <Badge tone="neutral">Scheduled</Badge>}
        </div>
        {d.subject && <div className="journey-subject">{d.subject}</div>}
        {d.note && <div className="muted">{d.note}</div>}
        {d.actions?.length > 0 && <div className="muted">What we did: {d.actions.join(' · ')}</div>}
        {d.link && <a href={d.link} target="_blank" rel="noreferrer">Meeting link ↗</a>}
        {d.body && (
          <>
            <button className="btn btn-ghost btn-sm journey-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
              {open ? 'Hide text' : 'Show text'}
            </button>
            {open && <div className="email-body journey-body">{d.body}</div>}
          </>
        )}
      </div>
    </li>
  )
}

export default function LeadJourney({ leadId }) {
  const [data, setData] = useState(null)
  const [{ busy, error }, load] = useAction(async () => setData(await api.leadJourney(leadId)))
  useEffect(() => {
    setData(null)
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leadId])

  const lead = data?.lead || {}
  const name = [lead.first_name, lead.last_name].filter(Boolean).join(' ')
  const cold = data?.cold
  const nurture = data?.nurture || []
  const items = data?.items || []
  const [coldTone, coldLabel] = cold ? ENROLLMENT_BADGE[cold.status] || ['neutral', cold.status] : []

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <a href="#" onClick={(e) => { e.preventDefault(); window.history.back() }} className="muted">← Back</a>
          <h1>{name || lead.email || 'Lead'}</h1>
          <p className="muted">
            {[lead.email, lead.job_title, lead.company].filter(Boolean).join(' · ') || ' '}
            {lead.tier && <> · Score: <strong>{lead.tier}</strong></>}
            {lead.lead_status && <> · Status: {lead.lead_status}</>}
          </p>
        </div>
        <div className="toolbar-actions">
          <button className="btn" onClick={load} disabled={busy}>{busy ? <Spinner /> : null} Refresh</button>
        </div>
      </div>
      <ErrorNote error={error} onRetry={load} />
      {!data && busy && <Spinner label="Loading this lead…" />}

      {data && (
        <div className="stats stats-4">
          <div className="stat">
            <span className="stat-label">Cold sequence</span>
            {cold ? <Badge tone={coldTone}>{coldLabel}</Badge> : <span className="muted">Not enrolled</span>}
            {cold && <span className="stat-note">Campaign: {data.campaign?.name || 'General'}</span>}
            {cold?.snoozed_until && <span className="stat-note">Back on {shortDateTime(cold.snoozed_until)}</span>}
          </div>
          <div className="stat">
            <span className="stat-label">Email Nurture</span>
            {nurture[0] ? (
              <>
                <Badge tone={(NURTURE_STATUS[nurture[0].status] || ['neutral'])[0]}>
                  {(NURTURE_STATUS[nurture[0].status] || [null, nurture[0].status])[1]}
                </Badge>
                <span className="stat-note">
                  {NURTURE_PERSONAS[nurture[0].persona]} · {TEMPERATURES[nurture[0].temperature]} · email {nurture[0].step} of 6
                  {nurture[0].exit_reason && ` · ${EXIT_REASONS[nurture[0].exit_reason] || nurture[0].exit_reason}`}
                </span>
              </>
            ) : <span className="muted">Not in nurture</span>}
          </div>
          <div className="stat">
            <span className="stat-label">Replies</span>
            <span className="stat-value">{items.filter((i) => i.type === 'inbound' && i.event_type === 'reply').length}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Meetings</span>
            <span className="stat-value">{items.filter((i) => i.type === 'meeting' && i.status !== 'cancelled').length}</span>
          </div>
        </div>
      )}

      {nurture[0]?.handoff_summary && (
        <Card title="Hand-off summary for sales">
          <p className="email-body">{nurture[0].handoff_summary}</p>
        </Card>
      )}

      {data && (
        <Card title="Timeline" subtitle="Newest first. Emails we sent, what came back, and what happened in between.">
          {items.length ? (
            <ul className="journey">{items.map((it, i) => <TimelineItem key={`${it.type}-${it.id || i}`} it={it} />)}</ul>
          ) : (
            <p className="muted">Nothing has happened with this lead yet.</p>
          )}
        </Card>
      )}
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api.js'
import { CATEGORIES, CATEGORY_ORDER, TRIAGE_STATUS, formatNumber, relativeTime, shortDateTime } from '../../labels.js'
import { BarList, StatTile } from '../Dashboard.jsx'
import { Badge, Card, ErrorNote, ReplyCheckSchedule, Spinner, useAction, confirmDialog, toast } from '../ui.jsx'
import { LeadLink } from '../LeadJourney.jsx'
import { DraftCard } from './Drafts.jsx'

const FILTERS = [['all', 'All'], ['needs_human', 'Needs you'], ...CATEGORY_ORDER.map((c) => [c, CATEGORIES[c][1]])]

function pct(n) {
  return n == null ? '—' : `${Math.round(n * 100)}%`
}

const plural = (n, one, many = `${one}s`) => `${formatNumber(n)} ${n === 1 ? one : many}`

// "Check for replies now", in plain words.
function CheckResult({ mail, sorted }) {
  const got = [
    mail.reply && plural(mail.reply, 'new reply', 'new replies'),
    mail.auto_reply && plural(mail.auto_reply, 'auto-reply', 'auto-replies'),
    mail.unsubscribe && plural(mail.unsubscribe, 'unsubscribe'),
    mail.bounce && plural(mail.bounce, 'bounce'),
  ].filter(Boolean)
  const sortedCount = Object.entries(sorted).filter(([k]) => k in CATEGORIES).reduce((a, [, v]) => a + v, 0)
  const parts = [
    got.length ? `Found ${got.join(', ')}` : 'No new replies in the inbox',
    sortedCount ? `sorted ${plural(sortedCount, 'reply', 'replies')}` : null,
    sorted.drafts_sent ? `sent ${plural(sorted.drafts_sent, 'approved answer')}` : null,
    sorted.deferred ? `${formatNumber(sorted.deferred)} will be retried (AI unavailable)` : null,
  ].filter(Boolean)
  const errors = mail.errors || []
  return (
    <div className={`note ${errors.length ? 'note-warning' : 'note-good'}`} role="status">
      <Badge tone={errors.length ? 'warning' : 'good'}>{errors.length ? 'Partly checked' : 'Checked'}</Badge>
      <div>
        <div>{parts.join(' · ')}.</div>
        {errors.map((e) => <div key={e} className="muted">Could not read {e}</div>)}
      </div>
    </div>
  )
}

// The conversation with this lead, oldest first: what we sent, what they
// wrote, what we answered.
function Thread({ leadId }) {
  const [items, setItems] = useState(null)
  const [{ busy, error }, load] = useAction(async () => {
    const j = await api.leadJourney(leadId)
    setItems((j.items || []).filter((i) => ['cold_sent', 'inbound', 'answer', 'nurture_sent'].includes(i.type)
      && (i.type !== 'answer' || ['sent', 'pending', 'approved'].includes(i.status))).reverse())
  })
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  if (!items) return busy ? <Spinner label="Loading the conversation…" /> : <ErrorNote error={error} onRetry={load} />
  return (
    <ol className="thread">
      {items.map((i) => {
        const ours = i.type !== 'inbound'
        const who = ours
          ? i.type === 'answer' ? `Our answer${i.status === 'sent' ? '' : ' (not sent yet)'}` : i.type === 'cold_sent' ? `Our email ${i.step}` : `Nurture email ${i.step}`
          : i.event_type === 'reply' ? 'They replied' : i.event_type.replace('_', ' ')
        return (
          <li key={`${i.type}-${i.id}`} className={ours ? 'thread-ours' : 'thread-theirs'}>
            <div className="thread-head"><strong>{who}</strong><span className="muted">{shortDateTime(i.at)}</span></div>
            <div className="reply-subject">{i.subject}</div>
            <div className="email-body">{ours ? i.body : i.snippet}</div>
          </li>
        )
      })}
    </ol>
  )
}

// Book a time in one click, see why none fit, and fix the lead's timezone.
function Times({ reply, onBooked }) {
  const [times, setTimes] = useState(null)
  const [tz, setTz] = useState('')
  const [notice, setNotice] = useState(null)
  const [{ busy, error }, load] = useAction(async () => {
    const t = await api.eventTimes(reply.id)
    setTimes(t)
    setTz(t.timezone || '')
  })
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const [{ busy: acting, error: actError }, act] = useAction(async (what, slot) => {
    if (what === 'tz') {
      await api.setLeadTimezone(reply.lead_id, tz.trim())
      await load()
      return null
    }
    if (!(await confirmDialog(`Book ${slot.label} with ${reply.from_email}?\n\nThe calendar invite goes out now, and a confirmation email is written for them.`))) return null
    const r = await api.bookFromCard(reply.id, slot.start)
    setNotice(`Booked ${r.booked}.${r.draft_id ? ' The confirmation for them is shown on this card (it follows the usual send rules).' : ''} ${r.note || ''}`)
    toast.success(`Meeting booked: ${r.booked}`)
    onBooked()
    return r
  })
  if (!times) return busy ? <Spinner label="Checking your calendar…" /> : <ErrorNote error={error} onRetry={load} />

  const groups = [
    ['What they proposed', times.proposed ? [times.proposed] : []],
    ['What we offered', times.offered],
    ['Next free times in their hours', times.suggestions],
  ].filter(([, list]) => list.length)
  return (
    <div className="times">
      {notice && <div className="note note-good" role="status"><Badge tone="good">Booked</Badge><span>{notice}</span></div>}
      {times.has_meeting && !notice && <p className="muted">This lead already has a meeting booked (see Meetings).</p>}
      {times.problem && <div className="note note-warning"><Badge tone="warning">No time fits</Badge><span>{times.problem}</span></div>}
      {!times.has_meeting && !notice && groups.map(([title, list]) => (
        <div key={title} className="times-group">
          <span className="did-title">{title}</span>
          <div className="times-buttons">
            {list.map((s) => (
              <button key={s.start} className={`btn btn-sm ${s.free ? 'btn-primary' : ''}`} disabled={acting || !s.free}
                onClick={() => act('book', s)} title={s.free ? `Your time: ${shortDateTime(s.start)}` : 'Not free in your calendar'}>
                {s.free ? 'Book ' : 'Busy: '}{s.label}
              </button>
            ))}
          </div>
        </div>
      ))}
      <div className="times-tz">
        <label className="muted" htmlFor={`tz-${reply.id}`}>
          Lead's timezone (their business hours: {times.business_hours})
        </label>
        <input id={`tz-${reply.id}`} value={tz} onChange={(e) => setTz(e.target.value)} placeholder="e.g. America/New_York" />
        <button className="btn btn-sm" disabled={acting || !tz.trim() || tz.trim() === times.timezone} onClick={() => act('tz')}>
          Save timezone
        </button>
      </div>
      <ErrorNote error={actError} />
    </div>
  )
}

function ReplyCard({ reply, onChanged, draft, now }) {
  const [open, setOpen] = useState(null) // 'times' | 'thread' | null
  const toggle = (what) => setOpen((o) => (o === what ? null : what))
  const cold = Boolean(reply.lead_id) && reply.event_type === 'reply'

  const category = reply.human_category || reply.category
  const [tone, label] = CATEGORIES[category] || ['neutral', 'Not classified']
  const [sTone, sLabel] = TRIAGE_STATUS[reply.triage_status] || ['neutral', reply.triage_status || '']
  const actions = reply.extracted?.actions || []
  const [{ busy, error }, rerun] = useAction(async (cat) => {
    await api.retriage(reply.id, cat)
    await api.run('triage') // act on it now instead of within the minute
    return true
  })

  const change = async (cat) => {
    const what = cat ? `Sort this reply as "${CATEGORIES[cat][1]}" and act on it` : 'Let the AI read this reply again and act on it'
    if (!(await confirmDialog(`${what}?\n\nA new answer may be drafted; it replaces any draft still waiting for this lead. Nothing is sent without the usual delay or your approval.`))) return
    if ((await rerun(cat)) !== undefined) onChanged()
  }

  return (
    <li className="reply triage-reply">
      <div className="reply-head">
        <div className="reply-who">
          <strong><LeadLink leadId={reply.lead_id}>{reply.from_email}</LeadLink></strong>
          <span className="muted">
            {reply.extracted?.source === 'nurture'
              ? <>Reply to an Email Nurture email · <a href="#nurture/leads">see Email Nurture → Leads</a></>
              : [reply.job_title, reply.company].filter(Boolean).join(' · ') || 'No job title or company'}
          </span>
        </div>
        <div className="reply-meta">
          <span className="badge-row">
            {category && <Badge tone={tone}>{label}</Badge>}
            {reply.triage_status !== 'done' && <Badge tone={sTone}>{sLabel}</Badge>}
          </span>
          <span className="muted">
            {relativeTime(reply.received_at)}
            {reply.confidence != null && !reply.human_category && ` · ${pct(reply.confidence)} sure`}
            {reply.human_category && ' · set by you'}
          </span>
        </div>
      </div>
      {reply.summary && <p className="reply-summary">{reply.summary}</p>}
      <p className="reply-subject">{reply.subject || '(no subject)'}</p>
      {reply.snippet && <p className="reply-snippet">{reply.snippet}</p>}

      {(actions.length > 0 || reply.triage_error) && (
        <div className="did">
          <span className="did-title">What happened</span>
          <ul>
            {actions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
          {reply.triage_error && <p className="field-error">{reply.triage_error}</p>}
        </div>
      )}

      {draft && <ul className="replies card-answer"><DraftCard draft={draft} now={now} onChanged={onChanged} compact /></ul>}

      {open === 'times' && <Times reply={reply} onBooked={onChanged} />}
      {open === 'thread' && <Thread leadId={reply.lead_id} />}

      <div className="reply-actions">
        {cold && (
          <button className="btn btn-sm" onClick={() => toggle('times')} aria-expanded={open === 'times'}>
            {open === 'times' ? 'Hide times' : 'Book a time'}
          </button>
        )}
        {reply.lead_id && (
          <button className="btn btn-ghost btn-sm" onClick={() => toggle('thread')} aria-expanded={open === 'thread'}>
            {open === 'thread' ? 'Hide conversation' : 'Conversation'}
          </button>
        )}
        <label className="inline-select">
          <span className="muted">Wrong category?</span>
          <select
            value=""
            disabled={busy || reply.triage_status === 'processing'}
            onChange={(e) => e.target.value && change(e.target.value)}
            aria-label="Change category and re-run"
          >
            <option value="">{category ? 'Change it…' : 'Set it…'}</option>
            {CATEGORY_ORDER.map((c) => (
              <option key={c} value={c} disabled={c === category}>
                {CATEGORIES[c][1]}
              </option>
            ))}
          </select>
        </label>
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => change(null)}
          title="The AI reads this reply again from scratch, e.g. after it failed or after you fixed the lead's details">
          {busy ? <Spinner /> : null} {busy ? 'Reading again…' : 'Read again'}
        </button>
        {reply.gmail_link && (
          <a className="btn btn-sm" href={reply.gmail_link} target="_blank" rel="noreferrer">
            Open in Gmail ↗
          </a>
        )}
      </div>
      <ErrorNote error={error} />
    </li>
  )
}

export default function TriageReplies() {
  const [filter, setFilter] = useState('all')
  const [stats, setStats] = useState(null)
  const [schedule, setSchedule] = useState(null)
  const [replies, setReplies] = useState(null)
  const [drafts, setDrafts] = useState({})
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])
  const [{ busy, error }, load] = useAction(async () => {
    const cat = CATEGORY_ORDER.includes(filter) ? filter : null
    const [s, r, h, d] = await Promise.all([
      api.triageStats(), api.triageEvents(cat), api.health(), api.drafts('all', 200).catch(() => ({ drafts: [] })),
    ])
    setStats(s)
    setReplies(r.events || [])
    // Each reply's newest answer, shown and handled right on its card.
    const byEvent = {}
    for (const x of d.drafts || []) {
      if (!byEvent[x.event_id] || x.created_at > byEvent[x.event_id].created_at) byEvent[x.event_id] = x
    }
    setDrafts(byEvent)
    setSchedule(h.schedule || {})
  })
  // Fetch new mail first, then sort it: what "check now" means to a person.
  const [{ busy: running, error: runError, result: runResult }, runNow] = useAction(async () => {
    const mail = (await api.run('poller')).result || {}
    const sorted = (await api.run('triage')).result || {}
    return { mail, sorted }
  })

  const refresh = useCallback(() => load(), [load])
  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  const missing = error?.message?.includes('not found') && error.message.includes('triage')
  const shown = (replies || []).filter((r) => filter !== 'needs_human' || ['needs_human', 'failed'].includes(r.triage_status))
  const cats = stats?.categories || {}
  const total = Object.values(cats).reduce((a, b) => a + b, 0)
  const waiting = (stats?.drafts?.pending || 0) + (stats?.drafts?.approved || 0)
  const needsYou = (stats?.triage?.needs_human || 0) + (stats?.triage?.failed || 0)

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Replies</h1>
          <p className="muted">
            Every reply is read and sorted automatically. Interested leads get meeting times, "not now"
            comes back in 60 days, referrals are enrolled, and unsubscribes are blocked everywhere.
          </p>
        </div>
        <div className="toolbar-actions">
          <ReplyCheckSchedule schedule={schedule} />
          <button className="btn" onClick={() => runNow().then(refresh)} disabled={running}
            title="Fetch new mail from the inbox and sort it now, instead of waiting for the automatic check">
            {running ? <Spinner /> : null} {running ? 'Checking…' : 'Check for replies now'}
          </button>
        </div>
      </div>

      {missing ? (
        <div className="note note-warning">
          <Badge tone="warning">Setup</Badge>
          <span>
            Run <code>supabase/migrations/0006_triage.sql</code> in the Supabase SQL Editor to turn on reply triage.
          </span>
        </div>
      ) : (
        <ErrorNote error={error?.status === 0 ? null : error} onRetry={load} />
      )}
      <ErrorNote error={runError} />
      {runResult && <CheckResult {...runResult} />}

      <div className="stats">
        <StatTile
          label="Reply → meeting rate"
          value={stats ? pct(stats.reply_to_meeting_rate) : null}
          note={stats && `${formatNumber(stats.meeting_leads)} of ${formatNumber(stats.replied_leads)} who replied`}
        />
        <StatTile label="Meetings coming up" value={stats?.meetings_upcoming} />
        <StatTile label="Drafts waiting" value={stats ? waiting : null} note={stats && 'approve, edit or let them auto-send'} />
        <StatTile label="Need you" value={stats ? needsYou : null} note={stats && 'unclear or failed replies'} />
        <StatTile label="Coming back later" value={stats?.snoozed} note={stats && '"not now" leads'} />
      </div>

      {total > 0 && (
        <Card title="Replies by category" subtitle={`${formatNumber(total)} triaged replies`}>
          <BarList rows={CATEGORY_ORDER.map((c) => [c, CATEGORIES[c][1]])} counts={cats} total={total} />
        </Card>
      )}

      <div className="chips" role="tablist" aria-label="Filter replies">
        {FILTERS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={filter === id}
            className={`chip ${filter === id ? 'is-active' : ''}`} onClick={() => setFilter(id)}>
            {label}
          </button>
        ))}
        {busy && <Spinner />}
      </div>

      {replies && !missing && (
        <Card>
          {shown.length ? (
            <ul className="replies">
              {shown.map((r) => (
                <ReplyCard key={r.id} reply={r} onChanged={refresh} draft={drafts[r.id]} now={now} />
              ))}
            </ul>
          ) : (
            <p className="muted">Nothing here. Replies appear within minutes of arriving.</p>
          )}
        </Card>
      )}
    </div>
  )
}

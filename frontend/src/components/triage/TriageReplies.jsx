import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api.js'
import { CATEGORIES, CATEGORY_ORDER, TRIAGE_STATUS, formatNumber, relativeTime, shortDateTime } from '../../labels.js'
import { StatTile } from '../Dashboard.jsx'
import { Badge, ErrorNote, ReplyCheckSchedule, Spinner, useAction, confirmDialog, toast } from '../ui.jsx'
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

// Their message without the quoted earlier email ("On Mon ... wrote:" and
// the "> " lines), which is shown only on request.
function splitQuoted(text) {
  const lines = (text || '').split('\n')
  const at = lines.findIndex((l, i) => /^>/.test(l.trim()) || (/^On .+/.test(l.trim()) && /wrote:\s*$/.test(
    [l, lines[i + 1] || ''].join(' ').trim())))
  if (at <= 0) return [text || '', '']
  return [lines.slice(0, at).join('\n').trim(), lines.slice(at).join('\n').trim()]
}

const replyCategory = (r) => r.human_category || r.category
// A person already answered it (approved or sent): it needs nobody now.
const answered = (draft) => ['approved', 'sent'].includes(draft?.status)
const unresolved = (r, draft) => ['needs_human', 'failed'].includes(r.triage_status) && !answered(draft)
const needsYou = (r, draft) => unresolved(r, draft) || draft?.status === 'pending'

// One row of the list: who, what, how urgent.
function ReplyRow({ reply, draft, active, onSelect }) {
  const [tone, label] = CATEGORIES[replyCategory(reply)] || ['neutral', 'Not sorted']
  const urgent = unresolved(reply, draft)
  return (
    <li id={`row-${reply.id}`}>
      <button className={`inbox-row ${active ? 'is-active' : ''} ${urgent || draft?.status === 'pending' ? 'is-attention' : ''}`}
        onClick={onSelect} aria-current={active ? 'true' : undefined}>
        <span className="inbox-avatar" aria-hidden="true">{(reply.from_email || '?')[0].toUpperCase()}</span>
        <span className="inbox-main">
          <span className="inbox-line">
            <span className="inbox-from">{reply.from_email}</span>
            <span className="inbox-time">{relativeTime(reply.received_at)}</span>
          </span>
          <span className="inbox-summary">{reply.summary || reply.snippet || reply.subject || '(empty)'}</span>
          <span className="inbox-tags">
            <Badge tone={tone}>{label}</Badge>
            {urgent && <Badge tone="warning">Needs you</Badge>}
            {draft?.status === 'pending' && <Badge tone="warning">Answer waiting</Badge>}
            {draft?.status === 'sent' && <span className="inbox-flag">Answered</span>}
          </span>
        </span>
      </button>
    </li>
  )
}

// "What happened", short: a technical error in brackets folds behind
// "details" ("booking failed (Graph 500: {...}); a person should book it").
function WhatHappened({ actions, error }) {
  if (!actions.length && !error) return null
  return (
    <div className="did">
      <span className="did-title">What happened</span>
      <ul>
        {actions.map((a) => {
          const short = a.replace(/\s*\(.*\)/s, '')
          return (
            <li key={a}>
              {short === a ? a : (
                <details className="did-details">
                  <summary>{short} <span className="muted">· details</span></summary>
                  <code className="did-raw">{a}</code>
                </details>
              )}
            </li>
          )
        })}
      </ul>
      {error && <p className="field-error">{error}</p>}
    </div>
  )
}

// The selected reply, in full, with everything you can do about it.
function ReplyDetail({ reply, draft, onChanged, now }) {
  const [tab, setTab] = useState('reply') // reply | thread | times
  const [showQuoted, setShowQuoted] = useState(false)
  useEffect(() => { setTab('reply'); setShowQuoted(false) }, [reply.id])
  const cold = Boolean(reply.lead_id) && reply.event_type === 'reply'
  const category = replyCategory(reply)
  const [tone, label] = CATEGORIES[category] || ['neutral', 'Not sorted']
  const [sTone, sLabel] = TRIAGE_STATUS[reply.triage_status] || ['neutral', reply.triage_status || '']
  const [message, quoted] = splitQuoted(reply.snippet)
  const [{ busy, error }, rerun] = useAction(async (cat) => {
    await api.retriage(reply.id, cat)
    await api.run('triage') // act on it now instead of within the minute
    return true
  })
  const [{ busy: handling, error: handleError }, markHandled] = useAction(async () => {
    await api.markHandled(reply.id)
    toast.success('Marked as handled')
    onChanged()
  })
  const change = async (cat) => {
    const what = cat ? `Sort this reply as "${CATEGORIES[cat][1]}" and act on it` : 'Let the AI read this reply again and act on it'
    if (!(await confirmDialog(`${what}?\n\nA new answer may be drafted; it replaces any draft still waiting for this lead. Nothing is sent without the usual delay or your approval.`))) return
    if ((await rerun(cat)) !== undefined) { toast.success('Done. The reply was read again.'); onChanged() }
  }

  return (
    <article className="inbox-detail">
      <header className="detail-head">
        <span className="inbox-avatar lg" aria-hidden="true">{(reply.from_email || '?')[0].toUpperCase()}</span>
        <div className="detail-who">
          <strong><LeadLink leadId={reply.lead_id}>{reply.from_email}</LeadLink></strong>
          <span className="muted">
            {reply.extracted?.source === 'nurture'
              ? <>Reply to an Email Nurture email · <a href="#nurture/leads">Email Nurture → Leads</a></>
              : [reply.job_title, reply.company].filter(Boolean).join(' · ') || 'No job title or company'}
          </span>
        </div>
        <div className="detail-meta">
          <span className="badge-row">
            {category && <Badge tone={tone}>{label}</Badge>}
            {reply.triage_status !== 'done' && !answered(draft) && <Badge tone={sTone}>{sLabel}</Badge>}
          </span>
          <span className="muted">
            {shortDateTime(reply.received_at)}
            {reply.confidence != null && !reply.human_category && ` · ${pct(reply.confidence)} sure`}
            {reply.human_category && ' · set by you'}
          </span>
        </div>
      </header>

      <div className="detail-toolbar">
        <div className="seg" role="tablist" aria-label="Show">
          <button role="tab" aria-selected={tab === 'reply'} className={tab === 'reply' ? 'is-active' : ''} onClick={() => setTab('reply')}>Reply</button>
          {reply.lead_id && <button role="tab" aria-selected={tab === 'thread'} className={tab === 'thread' ? 'is-active' : ''} onClick={() => setTab('thread')}>Conversation</button>}
          {cold && <button role="tab" aria-selected={tab === 'times'} className={tab === 'times' ? 'is-active' : ''} onClick={() => setTab('times')}>Book a time</button>}
        </div>
        <div className="detail-tools">
          <select value="" disabled={busy || reply.triage_status === 'processing'}
            onChange={(e) => e.target.value && change(e.target.value)} aria-label="Change category and re-run">
            <option value="">{category ? 'Wrong category?' : 'Set category…'}</option>
            {CATEGORY_ORDER.map((c) => (
              <option key={c} value={c} disabled={c === category}>{CATEGORIES[c][1]}</option>
            ))}
          </select>
          <button className="btn btn-sm" disabled={busy} onClick={() => change(null)}
            title="The AI reads this reply again from scratch, e.g. after it failed or after you fixed the lead's details">
            {busy ? <Spinner /> : null} Read again
          </button>
          {unresolved(reply, draft) && (
            <button className="btn btn-sm btn-primary" disabled={handling} onClick={() => markHandled()}
              title="You dealt with it (for example outside the app): it stops showing as Needs you">
              {handling ? <Spinner /> : null} Mark as handled
            </button>
          )}
          {reply.gmail_link && (
            <a className="btn btn-sm" href={reply.gmail_link} target="_blank" rel="noreferrer">Open in Gmail ↗</a>
          )}
        </div>
      </div>
      <ErrorNote error={error || handleError} />

      {tab === 'reply' && (
        <div className="detail-body">
          {reply.summary && <p className="detail-summary">{reply.summary}</p>}
          <section className="their-message">
            <span className="did-title">They wrote</span>
            <p className="reply-subject">{reply.subject || '(no subject)'}</p>
            <p className="email-body">{message || '(empty)'}</p>
            {quoted && (
              <button className="link-btn" onClick={() => setShowQuoted(!showQuoted)}>
                {showQuoted ? 'Hide earlier email' : '··· Show earlier email'}
              </button>
            )}
            {showQuoted && <p className="email-body quoted">{quoted}</p>}
          </section>
          <WhatHappened actions={reply.extracted?.actions || []} error={reply.triage_error} />
          {draft
            ? <ul className="replies card-answer"><DraftCard draft={draft} now={now} onChanged={onChanged} compact /></ul>
            : <p className="muted">No answer for this reply{category === 'not_interested' || category === 'unsubscribe' ? ' (none is needed)' : ''}.</p>}
        </div>
      )}
      {tab === 'thread' && <Thread key={reply.id} leadId={reply.lead_id} />}
      {tab === 'times' && <Times key={reply.id} reply={reply} onBooked={onChanged} />}
    </article>
  )
}

export default function TriageReplies() {
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [stats, setStats] = useState(null)
  const [schedule, setSchedule] = useState(null)
  const [replies, setReplies] = useState(null)
  const [drafts, setDrafts] = useState({})
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])
  // All recent replies load once; filters and search work on them instantly.
  const [{ busy, error }, load] = useAction(async () => {
    const [s, r, h, d] = await Promise.all([
      api.triageStats(), api.triageEvents(null, 200), api.health(), api.drafts('all', 200).catch(() => ({ drafts: [] })),
    ])
    setStats(s)
    setReplies(r.events || [])
    // Each reply's newest answer.
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
  }, [])

  const all = replies || []
  const counts = { all: all.length, needs_human: all.filter((r) => needsYou(r, drafts[r.id])).length }
  for (const c of CATEGORY_ORDER) counts[c] = all.filter((r) => replyCategory(r) === c).length
  const q = query.trim().toLowerCase()
  const shown = all.filter((r) => {
    if (filter === 'needs_human' && !needsYou(r, drafts[r.id])) return false
    if (CATEGORY_ORDER.includes(filter) && replyCategory(r) !== filter) return false
    if (!q) return true
    return [r.from_email, r.company, r.job_title, r.subject, r.summary, r.snippet]
      .some((v) => (v || '').toLowerCase().includes(q))
  })
  const selected = shown.find((r) => r.id === selectedId) || shown[0] || null

  // ↑/↓ (or j/k) move through the list, unless you are typing.
  useEffect(() => {
    const onKey = (e) => {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return
      const step = { ArrowDown: 1, j: 1, ArrowUp: -1, k: -1 }[e.key]
      if (!step || !shown.length) return
      e.preventDefault()
      const i = Math.max(0, shown.findIndex((r) => r.id === selected?.id))
      const next = shown[Math.min(shown.length - 1, Math.max(0, i + step))]
      setSelectedId(next.id)
      document.getElementById(`row-${next.id}`)?.scrollIntoView({ block: 'nearest' })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [shown, selected])

  const missing = error?.message?.includes('not found') && error.message.includes('triage')
  const waiting = (stats?.drafts?.pending || 0) + (stats?.drafts?.approved || 0)

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Replies</h1>
          <p className="muted">Every reply is read and sorted automatically. Pick one to see it, its answer and what you can do.</p>
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
          <span>Run <code>supabase/migrations/0006_triage.sql</code> in the Supabase SQL Editor to turn on reply triage.</span>
        </div>
      ) : (
        <ErrorNote error={error?.status === 0 ? null : error} onRetry={load} />
      )}
      <ErrorNote error={runError} />
      {runResult && <CheckResult {...runResult} />}

      <div className="stats">
        <StatTile label="Reply → meeting" value={stats ? pct(stats.reply_to_meeting_rate) : null}
          note={stats && `${formatNumber(stats.meeting_leads)} of ${formatNumber(stats.replied_leads)} who replied`} />
        <StatTile label="Meetings coming up" value={stats?.meetings_upcoming} />
        <StatTile label="Answers waiting" value={stats ? waiting : null} note={stats && 'approve, edit or let them auto-send'} />
        <StatTile label="Need you" value={replies ? counts.needs_human : null} note="unclear, failed or waiting for you" />
        <StatTile label="Coming back later" value={stats?.snoozed} note={stats && '"not now" leads'} />
      </div>

      {replies && !missing && (
        <div className="inbox">
          <aside className="inbox-list" aria-label="Replies">
            <div className="inbox-search">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4" /></svg>
              <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder="Search email, company, words…" aria-label="Search replies" />
              {busy && <Spinner />}
            </div>
            <div className="chips inbox-chips" role="tablist" aria-label="Filter replies">
              {FILTERS.filter(([id]) => id === 'all' || id === 'needs_human' || counts[id]).map(([id, label]) => (
                <button key={id} role="tab" aria-selected={filter === id}
                  className={`chip chip-sm ${filter === id ? 'is-active' : ''}`} onClick={() => setFilter(id)}>
                  {label} <span className="chip-count">{formatNumber(counts[id] || 0)}</span>
                </button>
              ))}
            </div>
            {shown.length ? (
              <ul className="inbox-rows">
                {shown.map((r) => (
                  <ReplyRow key={r.id} reply={r} draft={drafts[r.id]} active={selected?.id === r.id} onSelect={() => setSelectedId(r.id)} />
                ))}
              </ul>
            ) : (
              <p className="muted inbox-empty">
                {q ? `No reply matches "${query}".` : 'Nothing here. Replies appear within minutes of arriving.'}
              </p>
            )}
            <p className="muted inbox-foot">{formatNumber(shown.length)} of {formatNumber(all.length)} recent replies · ↑ ↓ to move</p>
          </aside>
          <section className="inbox-pane" aria-label="Selected reply">
            {selected
              ? <ReplyDetail reply={selected} draft={drafts[selected.id]} onChanged={refresh} now={now} />
              : <p className="muted inbox-empty">Select a reply.</p>}
          </section>
        </div>
      )}
    </div>
  )
}

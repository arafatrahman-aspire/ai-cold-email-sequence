import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { enrollOutcome, formatNumber } from '../labels.js'
import { LeadLink } from './LeadJourney.jsx'
import { CheckList, TagInput, Toggle } from './settingsControls.jsx'
import { AutoTextarea, Badge, Card, ErrorNote, Field, Spinner, useAction } from './ui.jsx'

const STATUS = {
  draft: ['neutral', 'Not started'],
  active: ['good', 'Running'],
  paused: ['warning', 'Paused'],
  ended: ['neutral', 'Ended'],
}
const TIERS = ['Hot', 'Warm', 'Cold']

const blank = () => ({
  name: '',
  audience: { titles: [], industries: [], countries: [], sources: [], tiers: [] },
  playbook: { angle: '', value_points: [], proof: [], offer: '', cta: '', tone: '', banned_claims: [] },
  auto_add: false,
})

// Lists edited as one item per line.
const toLines = (list) => (list || []).join('\n')
const fromLines = (text) => text.split('\n').map((s) => s.trim()).filter(Boolean)

function Group({ label, hint, children }) {
  return (
    <div className="field" role="group" aria-label={label}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  )
}

const pct = (n, d) => (d ? `${Math.round((n / d) * 100)}%` : '—')

function Results({ r }) {
  return (
    <div className="campaign-numbers">
      <span><strong>{formatNumber(r.leads)}</strong> leads</span>
      <span><strong>{formatNumber(r.emails_sent)}</strong> emails sent</span>
      <span><strong>{formatNumber(r.replied)}</strong> replied ({pct(r.replied, r.leads)})</span>
      <span><strong>{formatNumber(r.interested)}</strong> interested</span>
      <span><strong>{formatNumber(r.meetings)}</strong> meetings</span>
      {(r.bounced > 0 || r.unsubscribed > 0) && (
        <span className="muted">{formatNumber(r.bounced)} bounced · {formatNumber(r.unsubscribed)} unsubscribed</span>
      )}
    </div>
  )
}

function Editor({ campaign, onSaved, onCancel }) {
  const [d, setD] = useState(() => (campaign ? {
    name: campaign.name,
    audience: { ...blank().audience, ...campaign.audience },
    playbook: { ...blank().playbook, ...campaign.playbook },
    auto_add: campaign.auto_add,
  } : blank()))
  const setA = (k, v) => setD((x) => ({ ...x, audience: { ...x.audience, [k]: v } }))
  const setP = (k, v) => setD((x) => ({ ...x, playbook: { ...x.playbook, [k]: v } }))
  const [{ busy, error }, save] = useAction(async () => {
    const saved = campaign ? await api.saveCampaign(campaign.id, d) : await api.createCampaign(d)
    onSaved(saved)
    return saved
  })
  const p = d.playbook
  return (
    <Card title={campaign ? `Edit “${campaign.name}”` : 'New campaign'}
      subtitle="Who it is for, and what the AI should say. Changes apply to emails written from now on.">
      <div className="campaign-form">
        <Field label="Name"><input value={d.name} maxLength={120} onChange={(e) => setD({ ...d, name: e.target.value })}
          placeholder="e.g. Q4 healthcare phishing push" /></Field>

        <h3>Audience</h3>
        <p className="muted">Leads matching all the filled-in filters (any of the values in each). Leave a filter empty to not filter on it.</p>
        <Group label="Job title contains"><TagInput value={d.audience.titles} onChange={(v) => setA('titles', v)} placeholder="CISO, Head of IT, Enter" /></Group>
        <Group label="Industry contains"><TagInput value={d.audience.industries} onChange={(v) => setA('industries', v)} placeholder="Healthcare, Finance, Enter" /></Group>
        <Group label="Country contains"><TagInput value={d.audience.countries} onChange={(v) => setA('countries', v)} placeholder="United States, Enter" /></Group>
        <Group label="Lead source is"><TagInput value={d.audience.sources} onChange={(v) => setA('sources', v)} placeholder="apollo, webform, Enter" /></Group>
        <Group label="Lead score"><CheckList options={TIERS} value={d.audience.tiers} onChange={(v) => setA('tiers', v)} /></Group>
        <div className="campaign-toggle">
          <Toggle label="Keep adding new matching leads while the campaign runs" checked={d.auto_add} onChange={(v) => setD({ ...d, auto_add: v })} />
          <span>Keep adding new matching leads while the campaign runs (up to 25 every 5 minutes)</span>
        </div>

        <h3>Playbook</h3>
        <p className="muted">The AI follows this on top of its usual rules (no invented facts, short plain emails). Leave anything empty to let it decide.</p>
        <Field label="Angle" hint="The one theme of this campaign.">
          <AutoTextarea minRows={2} maxLength={500} value={p.angle} onChange={(e) => setP('angle', e.target.value)}
            placeholder="e.g. HIPAA audit season: proving staff training without the spreadsheet chase" />
        </Field>
        <Field label="Points worth making" hint="One per line. The AI picks what fits each email.">
          <AutoTextarea minRows={3} value={toLines(p.value_points)} onChange={(e) => setP('value_points', fromLines(e.target.value))} />
        </Field>
        <Field label="Proof the AI may cite" hint="One per line: case studies, numbers you can stand behind. Nothing else is used as proof.">
          <AutoTextarea minRows={2} value={toLines(p.proof)} onChange={(e) => setP('proof', fromLines(e.target.value))} />
        </Field>
        <div className="campaign-row">
          <Field label="Offer"><input value={p.offer} maxLength={300} onChange={(e) => setP('offer', e.target.value)} placeholder="e.g. a free phishing baseline test" /></Field>
          <Field label="Call to action"><input value={p.cta} maxLength={300} onChange={(e) => setP('cta', e.target.value)} placeholder="e.g. a 20-minute call" /></Field>
          <Field label="Tone"><input value={p.tone} maxLength={300} onChange={(e) => setP('tone', e.target.value)} placeholder="e.g. calm, peer-to-peer" /></Field>
        </div>
        <Field label="Never say" hint="One per line. An email containing any of these is rewritten, then held for review.">
          <AutoTextarea minRows={2} value={toLines(p.banned_claims)} onChange={(e) => setP('banned_claims', fromLines(e.target.value))}
            placeholder={'guaranteed compliance\n100% secure'} />
        </Field>
      </div>
      <ErrorNote error={error} />
      <div className="form-actions">
        <button className="btn btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || !d.name.trim()} onClick={() => save()}>
          {busy ? <Spinner /> : null} {campaign ? 'Save changes' : 'Create campaign'}
        </button>
      </div>
    </Card>
  )
}

function Preview({ campaign }) {
  const [{ busy, error, result }, run] = useAction(() => api.previewCampaign(campaign.id))
  return (
    <div className="campaign-preview">
      <button className="btn btn-sm" disabled={busy} onClick={() => run()}>
        {busy ? <Spinner /> : null} {busy ? 'Writing a sample…' : result ? 'Write another sample' : 'Preview a sample sequence'}
      </button>
      <ErrorNote error={error} />
      {result && (
        <div className="stack">
          <p className="muted">
            Sample for {[result.lead.first_name, result.lead.last_name].filter(Boolean).join(' ') || result.lead.email}
            {result.lead.job_title ? ` (${result.lead.job_title})` : ''}. Nothing is saved or sent.
            {result.status !== 'ok' && ' The AI could not meet every rule; a real lead would be held for review.'}
          </p>
          {result.validation_errors.length > 0 && <p className="field-error">{result.validation_errors.join(' · ')}</p>}
          {result.emails.map((e) => (
            <div key={e.step_number} className="journey-body">
              <strong>Email {e.step_number}: {e.subject}</strong>
              <div className="email-body">{e.body}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Audience({ campaign, onChanged }) {
  const [data, setData] = useState(null)
  const [picked, setPicked] = useState(() => new Set())
  const [notice, setNotice] = useState(null)
  const [{ busy, error }, load] = useAction(async () => setData(await api.campaignAudience(campaign.id)))
  useEffect(() => { load() }, [campaign.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const [{ busy: adding, error: addError }, add] = useAction(async (all) => {
    const n = all ? data.total : picked.size
    if (!window.confirm(`Add ${n} lead${n === 1 ? '' : 's'} to “${campaign.name}”?\n\nTheir emails are written within 5 minutes${campaign.status === 'active' ? ' and sent on schedule.' : ', and wait until you start the campaign.'}`)) return null
    const r = await api.enrollCampaign(campaign.id, all ? { all_matching: true } : { lead_ids: [...picked] })
    const refused = r.results.filter((x) => x.outcome !== 'enrolled')
    setNotice({ added: r.enrolled, refused })
    setPicked(new Set())
    await load()
    onChanged()
    return r
  })
  if (!data) return busy ? <Spinner label="Finding matching leads…" /> : <ErrorNote error={error} onRetry={load} />
  const toggle = (id) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  return (
    <div className="stack">
      {notice && (
        <div className={`note ${notice.refused.length ? 'note-warning' : 'note-good'}`} role="status">
          <Badge tone={notice.refused.length ? 'warning' : 'good'}>Added {notice.added}</Badge>
          <span>
            {notice.refused.length > 0 && `${notice.refused.length} could not join: `}
            {[...new Set(notice.refused.map((x) => enrollOutcome(x.outcome)[1]))].join(', ')}
          </span>
        </div>
      )}
      <ErrorNote error={addError} />
      <div className="reply-actions">
        <span className="muted">{formatNumber(data.total)} matching lead{data.total === 1 ? '' : 's'} not in any cold sequence yet</span>
        <div className="draft-buttons">
          <button className="btn btn-sm" disabled={adding || !picked.size} onClick={() => add(false)}>Add {picked.size || ''} selected</button>
          <button className="btn btn-primary btn-sm" disabled={adding || !data.total} onClick={() => add(true)}>
            {adding ? <Spinner /> : null} Add all {data.total > 500 ? 'first 500' : formatNumber(data.total)}
          </button>
        </div>
      </div>
      {data.leads.length > 0 && (
        <div className="table-wrap">
          <table className="lead-table">
            <thead><tr><th className="col-check" /><th>Lead</th><th>Role</th><th>Industry · country</th><th>Source · score</th></tr></thead>
            <tbody>
              {data.leads.map((l) => (
                <tr key={l.id} className={picked.has(l.id) ? 'is-selected' : undefined}>
                  <td className="col-check"><input type="checkbox" checked={picked.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Select ${l.email}`} /></td>
                  <td><strong><LeadLink leadId={l.id}>{[l.first_name, l.last_name].filter(Boolean).join(' ') || l.email}</LeadLink></strong><div className="muted">{l.email}</div></td>
                  <td>{l.job_title || <span className="muted">—</span>}<div className="muted">{l.company || ''}</div></td>
                  <td>{[l.industry, l.country].filter(Boolean).join(' · ') || <span className="muted">—</span>}</td>
                  <td>{l.source || '—'}<div className="muted">{l.tier || ''}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.total > data.leads.length && <p className="muted">Showing the newest {data.leads.length}. “Add all” adds every match (up to 500 at a time).</p>}
        </div>
      )}
    </div>
  )
}

function CampaignCard({ c, onEdit, onChanged }) {
  const [open, setOpen] = useState(null) // 'audience' | 'preview'
  const [tone, label] = STATUS[c.status] || ['neutral', c.status]
  const [{ busy, error }, act] = useAction(async (action) => {
    const words = {
      start: `Start “${c.name}”? Its leads' emails go out on schedule from now on.`,
      pause: `Pause “${c.name}”? Nothing more is sent until you resume; replies are still handled.`,
      resume: `Resume “${c.name}”?`,
      end: `End “${c.name}” for good? Emails not sent yet are cancelled for its leads. Sent emails and replies stay.`,
    }[action]
    if (!window.confirm(words)) return null
    const r = await api.campaignAction(c.id, action)
    onChanged()
    return r
  })
  const toggle = (what) => setOpen((o) => (o === what ? null : what))
  return (
    <li className="draft">
      <div className="reply-head">
        <div className="reply-who">
          <strong>{c.name}</strong>
          <span className="muted">{c.playbook?.angle || 'No angle set: the AI uses each persona’s usual angle.'}</span>
        </div>
        <div className="reply-meta">
          <Badge tone={tone}>{label}</Badge>
          {c.auto_add && c.status === 'active' && <span className="muted">adding new matches</span>}
        </div>
      </div>
      <Results r={c.results} />
      {open === 'audience' && <Audience campaign={c} onChanged={onChanged} />}
      {open === 'preview' && <Preview campaign={c} />}
      <div className="reply-actions">
        <div className="draft-buttons">
          {c.status !== 'ended' && (
            <>
              <button className="btn btn-sm" onClick={() => toggle('audience')} aria-expanded={open === 'audience'}>
                {open === 'audience' ? 'Hide leads' : 'Add leads'}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => toggle('preview')} aria-expanded={open === 'preview'}>Preview</button>
              <button className="btn btn-ghost btn-sm" onClick={onEdit}>Edit</button>
            </>
          )}
        </div>
        <div className="draft-buttons">
          {c.status === 'draft' && <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act('start')}>Start</button>}
          {c.status === 'active' && <button className="btn btn-sm" disabled={busy} onClick={() => act('pause')}>Pause</button>}
          {c.status === 'paused' && <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act('resume')}>Resume</button>}
          {c.status !== 'ended' && <button className="btn btn-ghost btn-sm btn-remove" disabled={busy} onClick={() => act('end')}>End</button>}
        </div>
      </div>
      <ErrorNote error={error} />
    </li>
  )
}

export default function Campaigns() {
  const [data, setData] = useState(null)
  const [editing, setEditing] = useState(null) // null | 'new' | campaign
  const [{ busy, error }, load] = useAction(async () => setData(await api.campaigns()))
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const missing = error?.message?.includes('campaign') && error.message.includes('not found')

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Campaigns</h1>
          <p className="muted">
            Each campaign has its own audience, angle and results. Add leads, preview what the AI writes, then start it.
            Leads added before you start are written but not sent.
          </p>
        </div>
        <div className="toolbar-actions">
          <button className="btn" onClick={load} disabled={busy}>{busy ? <Spinner /> : null} Refresh</button>
          {!editing && !missing && <button className="btn btn-primary" onClick={() => setEditing('new')}>New campaign</button>}
        </div>
      </div>
      {missing ? (
        <div className="note note-warning"><Badge tone="warning">Setup</Badge>
          <span>Run <code>supabase/migrations/0017_campaigns.sql</code> in the Supabase SQL Editor to turn on campaigns.</span></div>
      ) : <ErrorNote error={error} onRetry={load} />}

      {editing && (
        <Editor campaign={editing === 'new' ? null : editing} onCancel={() => setEditing(null)}
          onSaved={() => { setEditing(null); load() }} />
      )}

      {data && (
        <Card>
          <ul className="replies">
            {data.campaigns.map((c) => (
              <CampaignCard key={c.id} c={c} onEdit={() => setEditing(c)} onChanged={load} />
            ))}
            <li className="draft">
              <div className="reply-head">
                <div className="reply-who">
                  <strong>General</strong>
                  <span className="muted">Leads enrolled outside a campaign (Enroll tab, automatic enrollment). They are written with each persona’s usual angle.</span>
                </div>
                <div className="reply-meta"><Badge tone="good">Always on</Badge></div>
              </div>
              <Results r={data.general.results} />
            </li>
          </ul>
          {data.campaigns.length === 0 && <p className="muted">No campaigns yet. Everything so far is in General.</p>}
        </Card>
      )}
    </div>
  )
}

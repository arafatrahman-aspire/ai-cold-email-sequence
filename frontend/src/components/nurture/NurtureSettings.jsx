import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { HourSelect, NumberInput, Row, TagInput, Toggle, WeekdayPicker, describeDays, hourLabel } from '../settingsControls.jsx'
import { Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'
import { NurtureStatus } from './NurtureDashboard.jsx'

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
const isInt = (n, lo, hi) => Number.isInteger(n) && n >= lo && n <= hi
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function validate(d) {
  const e = {}
  for (const t of ['warm', 'cold']) {
    const days = d.cadence[t]
    if (days.length !== 6 || days.some((x) => !isInt(x, 0, 120)) || days.some((x, i) => i && x <= days[i - 1])) {
      e[`cadence_${t}`] = 'Six whole days, each later than the one before (0 to 120).'
    }
  }
  if (!(d.test_mode.minutes_per_day > 0 && d.test_mode.minutes_per_day <= 60)) e.minutes = 'Use 0.1 to 60 minutes.'
  if (d.test_mode.allow_list.some((a) => !EMAIL.test(a))) e.allow = 'Every entry must be an email address.'
  if (!isInt(d.cooldown_days, 0, 730)) e.cooldown = 'Use 0 to 730 days.'
  if (!(d.persona_min_confidence >= 0 && d.persona_min_confidence <= 1)) e.confidence = 'Use 0 to 100%.'
  if (!(d.judge_min_score >= 0 && d.judge_min_score <= 1)) e.judge = 'Use 0 to 100%.'
  if (!isInt(d.daily_cap, 1, 2000)) e.cap = 'Use 1 to 2000.'
  if (!isInt(d.batch_size, 1, 100)) e.batch = 'Use 1 to 100.'
  if (!isInt(d.ooo_delay_days, 1, 60)) e.ooo = 'Use 1 to 60 days.'
  if (!isInt(d.bot_click_seconds, 0, 3600)) e.bot = 'Use 0 to 3600 seconds.'
  const w = d.sending_window
  if (w.start_hour >= w.end_hour) e.window = 'The window must end after it starts.'
  if (!w.weekdays.length) e.days = 'Pick at least one day.'
  if (!(d.approval.sample_rate >= 0 && d.approval.sample_rate <= 1)) e.sample = 'Use 0 to 100%.'
  for (const k of ['sales_email', 'alert_email']) if (d[k] && !EMAIL.test(d[k])) e[k] = 'Not an email address.'
  for (const k of ['demo_url', 'pricing_url', 'logo_url']) {
    const v = d.branding[k]
    if (v && !/^https?:\/\/\S+$/.test(v)) e[k] = 'Must start with https://'
  }
  return e
}

function CadenceRow({ label, value, onChange, error }) {
  return (
    <Row label={label} error={error} description="Day of each email, counted from the day they join.">
      <div className="cadence">
        {value.map((v, i) => (
          <label key={i} className="cadence-step">
            <span className="muted">#{i + 1}</span>
            <input type="number" min={0} max={120} value={v ?? ''} style={{ width: 64 }}
              onChange={(e) => onChange(value.map((x, j) => (j === i ? (e.target.value === '' ? '' : Number(e.target.value)) : x)))} />
          </label>
        ))}
      </div>
    </Row>
  )
}

function TextRow({ label, description, value, onChange, error, placeholder, type = 'text' }) {
  return (
    <Row label={label} description={description} error={error}>
      <input type={type} value={value || ''} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </Row>
  )
}

export default function NurtureSettings() {
  const [original, setOriginal] = useState(null)
  const [draft, setDraft] = useState(null)
  const [status, setStatus] = useState(null)
  const [{ busy: loading, error: loadError }, load] = useAction(async () => {
    const [res, st] = await Promise.all([api.nurtureSettings(), api.nurtureStatus()])
    setOriginal(res.settings)
    setDraft(res.settings)
    setStatus(st)
  })
  const [{ busy: saving, error: saveError, result: saved }, save] = useAction(async () => {
    const changes = Object.fromEntries(Object.entries(draft).filter(([k, v]) => !same(v, original[k])))
    const res = await api.nurtureSaveSettings(changes)
    setOriginal(res.settings)
    setDraft(res.settings)
    setStatus(await api.nurtureStatus())
    return true
  })
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!draft) {
    return (
      <div className="stack">
        <h1>Nurture settings</h1>
        {loading && <Spinner />}
        <ErrorNote error={loadError} onRetry={load} />
      </div>
    )
  }

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }))
  const setIn = (key, patch) => setDraft((d) => ({ ...d, [key]: { ...d[key], ...patch } }))
  const errors = validate(draft)
  const dirty = !same(draft, original)
  const w = draft.sending_window
  const pct = (v) => (v === '' ? '' : Math.round(v * 100))
  const fromPct = (v) => (v === '' ? '' : v / 100)

  return (
    <div className="stack settings-page">
      <div className="toolbar">
        <div>
          <h1>Nurture settings</h1>
          <p className="muted">Changes apply within a minute of saving; the pause switch on the next cycle.</p>
        </div>
      </div>
      <NurtureStatus status={status} />

      <Card className={`kill-switch ${draft.paused ? 'is-off' : ''}`}>
        <div className="kill-row">
          <div>
            <div className="kill-title">
              <h2>Email Nurture</h2>
              <Badge tone={draft.paused ? 'warning' : 'good'}>{draft.paused ? 'Paused' : 'Running'}</Badge>
            </div>
            <p className="muted">
              {draft.paused
                ? 'No emails are written or sent. Score changes are still noted, and replies are still handled.'
                : 'Emails are written about a day ahead and sent inside the sending window.'}
            </p>
          </div>
          <Toggle label="Nurture running" checked={!draft.paused} onChange={(v) => set({ paused: !v })} />
        </div>
      </Card>

      <Card title="Test mode" subtitle="Runs a whole sequence fast, to real inboxes you control.">
        <div className="set-rows">
          <Row label="Test mode" description="Days become minutes, the sending window is ignored, and only the addresses below are enrolled or emailed. Other leads wait until test mode is off.">
            <Toggle label="Test mode" checked={draft.test_mode.enabled} onChange={(v) => setIn('test_mode', { enabled: v })} />
          </Row>
          <Row label="One day lasts" error={errors.minutes} description="1 minute runs a 30-day sequence in about half an hour.">
            <NumberInput value={draft.test_mode.minutes_per_day} min={0.1} max={60} suffix="minutes"
              onChange={(v) => setIn('test_mode', { minutes_per_day: v })} />
          </Row>
          <Row label="Test addresses" error={errors.allow}>
            <TagInput value={draft.test_mode.allow_list} placeholder="you@company.com, Enter"
              onChange={(v) => setIn('test_mode', { allow_list: v.map((a) => a.toLowerCase()) })} />
          </Row>
        </div>
      </Card>

      <Card title="Who is enrolled">
        <div className="set-rows">
          <Row label="Enroll on score change" description="When a lead's score becomes Warm or Cold. Off: only the Enroll button on Leads enrolls.">
            <Toggle label="Enroll on score change" checked={draft.auto_enroll} onChange={(v) => set({ auto_enroll: v })} />
          </Row>
          <Row label="Cooldown after nurture" error={errors.cooldown} description="A lead who finished or left nurture is not enrolled again for this long.">
            <NumberInput value={draft.cooldown_days} min={0} max={730} suffix="days" onChange={(v) => set({ cooldown_days: v })} />
          </Row>
          <Row label="Persona confidence needed" error={errors.confidence}
            description="When the AI is less sure than this about a job title, the lead gets IT Manager and is flagged for review.">
            <NumberInput value={pct(draft.persona_min_confidence)} min={0} max={100} suffix="%"
              onChange={(v) => set({ persona_min_confidence: fromPct(v) })} />
          </Row>
          <Row label="Never nurture lead statuses" description="Shared lead statuses that keep a lead out (customers are always kept out).">
            <TagInput value={draft.blocked_lead_statuses} placeholder="Status, Enter" onChange={(v) => set({ blocked_lead_statuses: v })} />
          </Row>
          <Row label="Skip leads in another email journey" description="Leads already in the other project's email journey (email_nurture_state).">
            <Toggle label="Skip other journeys" checked={draft.skip_if_in_email_nurture} onChange={(v) => set({ skip_if_in_email_nurture: v })} />
          </Row>
        </div>
      </Card>

      <Card title="Cadence" subtitle="Six emails per lead. Moving between Warm and Cold switches to the other track from the next email.">
        <div className="set-rows">
          <CadenceRow label="Warm leads" value={draft.cadence.warm} error={errors.cadence_warm} onChange={(v) => setIn('cadence', { warm: v })} />
          <CadenceRow label="Cold leads" value={draft.cadence.cold} error={errors.cadence_cold} onChange={(v) => setIn('cadence', { cold: v })} />
        </div>
      </Card>

      <Card title="Sending" subtitle="Sent from nurture's own account (NURTURE_FROM_EMAIL in .env), never from the cold inboxes.">
        <div className="set-rows">
          <Row label="Sending window" error={errors.window}
            description={`In each lead's own timezone: ${describeDays(w.weekdays)}, ${hourLabel(w.start_hour)}–${hourLabel(w.end_hour)}.`}>
            <span className="inline-select">
              <HourSelect value={w.start_hour} label="From" onChange={(v) => setIn('sending_window', { start_hour: v })} />
              <span className="muted">to</span>
              <HourSelect value={w.end_hour} label="Until" from={1} to={24} onChange={(v) => setIn('sending_window', { end_hour: v })} />
            </span>
          </Row>
          <Row label="Days" error={errors.days}>
            <WeekdayPicker value={w.weekdays} label="Sending days" onChange={(v) => setIn('sending_window', { weekdays: v })} />
          </Row>
          <TextRow label="Timezone when unknown" description="IANA name, e.g. America/New_York or Asia/Dhaka."
            value={draft.default_timezone} onChange={(v) => set({ default_timezone: v })} />
          <Row label="Daily limit" error={errors.cap} description="Nurture emails per day across all leads. Keep it low for a new Gmail account.">
            <NumberInput value={draft.daily_cap} min={1} max={2000} suffix="emails" onChange={(v) => set({ daily_cap: v })} />
          </Row>
          <Row label="Per cycle" error={errors.batch} description="Emails written or sent each minute.">
            <NumberInput value={draft.batch_size} min={1} max={100} onChange={(v) => set({ batch_size: v })} />
          </Row>
        </div>
      </Card>

      <Card title="Review" subtitle="Whether AI drafts wait for a person before they go out.">
        <div className="set-rows">
          <Row label="Approval" description="Pilot mode holds every AI draft until approved. Fallback emails are pre-approved.">
            <select value={draft.approval.mode} onChange={(e) => setIn('approval', { mode: e.target.value })}>
              <option value="none">Send on schedule (review optional)</option>
              <option value="sample">A random sample waits for approval</option>
              <option value="all">Pilot mode: every AI draft waits</option>
            </select>
          </Row>
          {draft.approval.mode === 'sample' && (
            <Row label="Sample size" error={errors.sample}>
              <NumberInput value={pct(draft.approval.sample_rate)} min={0} max={100} suffix="% of drafts"
                onChange={(v) => setIn('approval', { sample_rate: fromPct(v) })} />
            </Row>
          )}
        </div>
      </Card>

      <Card title="Writing rules" subtitle="Checked on every AI draft. A draft that fails twice is replaced by the fallback email.">
        <div className="set-rows">
          <Row label="Banned phrases" description="Prices, discounts and per-seat costs are always blocked as well.">
            <TagInput value={draft.banned_phrases} placeholder="Phrase, Enter" onChange={(v) => set({ banned_phrases: v })} />
          </Row>
          <Row label="Reviewer score needed" error={errors.judge} description="A second AI scores grounding, persona fit, tone and repetition.">
            <NumberInput value={pct(draft.judge_min_score)} min={0} max={100} suffix="%" onChange={(v) => set({ judge_min_score: fromPct(v) })} />
          </Row>
        </div>
      </Card>

      <Card title="Branding and links" subtitle="The layout around every email and where its buttons lead.">
        <div className="set-rows">
          <TextRow label="Company name" value={draft.branding.company_name} onChange={(v) => setIn('branding', { company_name: v })} />
          <TextRow label="Postal address" description="Required in marketing email footers." value={draft.branding.company_address}
            onChange={(v) => setIn('branding', { company_address: v })} />
          <TextRow label="Sender name" value={draft.branding.sender_name} onChange={(v) => setIn('branding', { sender_name: v })} />
          <TextRow label="Sender title" value={draft.branding.sender_title} onChange={(v) => setIn('branding', { sender_title: v })} />
          <TextRow label="Logo URL" error={errors.logo_url} description="An https:// image; leave empty for no logo."
            value={draft.branding.logo_url} onChange={(v) => setIn('branding', { logo_url: v })} />
          <TextRow label="Demo link" error={errors.demo_url} description="Empty: the Cal.com booking link (CALCOM_BOOKING_URL)."
            value={draft.branding.demo_url} onChange={(v) => setIn('branding', { demo_url: v })} placeholder="https://cal.com/…" />
          <TextRow label="Pricing link" error={errors.pricing_url} value={draft.branding.pricing_url}
            onChange={(v) => setIn('branding', { pricing_url: v })} placeholder="https://…/pricing" />
        </div>
      </Card>

      <Card title="Hand-offs and alerts">
        <div className="set-rows">
          <TextRow label="Sales email" type="email" error={errors.sales_email}
            description="Gets each hand-off with an AI summary. Empty: the reply alert address."
            value={draft.sales_email} onChange={(v) => set({ sales_email: v.trim() })} />
          <TextRow label="Alert email" type="email" error={errors.alert_email}
            description="Job failures and a high fallback rate. Empty: the reply alert address."
            value={draft.alert_email} onChange={(v) => set({ alert_email: v.trim() })} />
          <Row label="Alert when fallbacks exceed" description="Share of emails in the last 24 hours (with at least 10 written).">
            <NumberInput value={pct(draft.fallback_alert_rate)} min={0} max={100} suffix="%" onChange={(v) => set({ fallback_alert_rate: fromPct(v) })} />
          </Row>
          <Row label="Out-of-office delay" error={errors.ooo} description="An away reply moves the next email at least this far.">
            <NumberInput value={draft.ooo_delay_days} min={1} max={60} suffix="days" onChange={(v) => set({ ooo_delay_days: v })} />
          </Row>
          <Row label="Ignore clicks sooner than" error={errors.bot}
            description="Mail scanners open links right after delivery; those clicks never trigger a hand-off.">
            <NumberInput value={draft.bot_click_seconds} min={0} max={3600} suffix="seconds after sending" onChange={(v) => set({ bot_click_seconds: v })} />
          </Row>
        </div>
      </Card>

      {status && (
        <Card title="Sending account" subtitle="Set in .env; restart the backend after changing it.">
          <dl className="kv">
            <dt>From</dt><dd>{status.from_email || <span className="muted">not set (NURTURE_FROM_EMAIL)</span>}</dd>
            <dt>Replies go to</dt><dd>{status.reply_to || <span className="muted">no inbox</span>} <span className="muted">(read by Reply Triage)</span></dd>
            <dt>Links and unsubscribe</dt><dd>{status.public_url || <span className="muted">not set (NURTURE_PUBLIC_URL)</span>}</dd>
          </dl>
        </Card>
      )}

      {(dirty || saveError || saved) && (
        <div className={`save-bar ${dirty ? 'is-dirty' : ''}`} role="status">
          <span>
            {saveError ? <span className="field-error">Could not save: {saveError.message}</span>
              : dirty ? (Object.keys(errors).length ? <span className="field-error">Fix the highlighted fields to save.</span> : 'Unsaved changes')
                : <Badge tone="good">Saved</Badge>}
          </span>
          {dirty && (
            <div className="save-actions">
              <button className="btn btn-ghost btn-sm" disabled={saving} onClick={() => setDraft(original)}>Discard</button>
              <button className="btn btn-primary btn-sm" disabled={saving || Object.keys(errors).length > 0} onClick={() => save()}>
                {saving ? <Spinner /> : null} Save changes
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

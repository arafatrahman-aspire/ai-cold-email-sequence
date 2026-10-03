import { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import {
  CheckList,
  GapsEditor,
  HourSelect,
  NumberInput,
  RegionsEditor,
  Row,
  TagInput,
  Toggle,
  WeekdayPicker,
  describeDays,
  hourLabel,
  regionKeyError,
  regionsToRows,
  rowsToRegions,
} from './settingsControls.jsx'
import { Badge, Card, ErrorNote, Spinner, useAction } from './ui.jsx'

// Mirrors the backend's defaults, used when a setting is not in the table yet.
const DEFAULTS = {
  business_hours: { start_hour: 9, end_hour: 17, weekdays: [0, 1, 2, 3, 4] },
  // Same as the seed in supabase/migrations/0001_init.sql.
  business_hours_by_region: Object.fromEntries(
    ['BD', 'SA', 'IL', 'EG', 'QA', 'KW', 'OM', 'BH', 'JO', 'IQ', 'DZ'].map((c) => [c, { weekdays: [6, 0, 1, 2, 3] }]),
  ),
  step_gaps_business_days: [0, 3, 7, 12],
  default_daily_cap: 15,
  intake_batch_size: 10,
  send_batch_size: 25,
  max_send_attempts: 3,
  llm_max_body_chars: 2200,
  llm_min_body_chars: 120,
  contactable_lead_statuses: ['New', 'No Answer', 'Busy', 'Call Later', 'In Progress'],
  skip_if_in_email_nurture: true,
  auto_enroll: { enabled: false, sources: [], batch_size: 50 },
  reply_alert_email: '',
}
const KNOWN_KEYS = new Set([...Object.keys(DEFAULTS), 'sequence_enabled'])

// public.leads.status values (supabase/existing_schema.sql).
const LEAD_STATUSES = [
  'New', 'Interested', 'Not Interested', 'Busy', 'Call Later', 'Wrong Number',
  'Already Enrolled', 'Budget Issue', 'Wants Employer Approval', 'Payment Pending',
  'Paid', 'Escalated to Human', 'In Progress', 'No Answer', 'DNC',
  'Consultation Booked', 'Lost / Closed',
]

// Statuses that almost always mean "don't cold-email this person".
const RISKY_STATUSES = ['DNC', 'Not Interested', 'Wrong Number', 'Paid', 'Lost / Closed', 'Already Enrolled']

const HOUR_PRESETS = [
  ['Mon–Fri, 9–5', { start_hour: 9, end_hour: 17, weekdays: [0, 1, 2, 3, 4] }],
  ['Mon–Fri, 8–6', { start_hour: 8, end_hour: 18, weekdays: [0, 1, 2, 3, 4] }],
  ['Mornings only', { start_hour: 8, end_hour: 12, weekdays: [0, 1, 2, 3, 4] }],
]

const SECTIONS = [
  ['set-schedule', 'Schedule'],
  ['set-limits', 'Sending limits'],
  ['set-who', 'Who can be emailed'],
  ['set-ai', 'AI drafting'],
  ['set-notify', 'Notifications'],
]

const words = (chars) => Math.round((Number(chars) || 0) / 6)

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
const isInt = (n, min, max) => Number.isInteger(n) && n >= min && n <= max

// The region map is edited as a list; everything else is edited as stored.
const toDraft = (values) => ({
  ...values,
  business_hours_by_region: regionsToRows(values.business_hours_by_region),
})
const fromDraft = (draft) => ({
  ...draft,
  business_hours_by_region: rowsToRegions(draft.business_hours_by_region),
})

function validate(d) {
  const e = {}
  const bh = d.business_hours
  if (!(bh.start_hour < bh.end_hour)) e.business_hours = 'The end time must be after the start time.'
  else if (!bh.weekdays?.length) e.business_hours = 'Pick at least one work day.'

  const gaps = d.step_gaps_business_days
  if (!gaps.length) e.step_gaps_business_days = 'The sequence needs at least one email.'
  else if (!gaps.every((g) => isInt(g, 0, 90))) e.step_gaps_business_days = 'Use whole numbers from 0 to 90.'
  else if (gaps.some((g, i) => i > 0 && g <= gaps[i - 1]))
    e.step_gaps_business_days = 'Wait at least 1 business day between emails.'

  const rows = d.business_hours_by_region
  const keys = rows.map((r) => r.key)
  for (const { key, ov } of rows) {
    const keyErr = regionKeyError(key, keys)
    const start = ov.start_hour ?? bh.start_hour
    const end = ov.end_hour ?? bh.end_hour
    if (keyErr) e.business_hours_by_region = 'Fix the highlighted region.'
    else if (!(start < end)) e.business_hours_by_region = `${key}: the end time must be after the start time.`
    else if (Array.isArray(ov.weekdays) && !ov.weekdays.length)
      e.business_hours_by_region = `${key}: pick at least one work day.`
  }

  if (!isInt(d.default_daily_cap, 1, 2000)) e.default_daily_cap = 'Use a whole number from 1 to 2000.'
  if (!isInt(d.max_send_attempts, 1, 10)) e.max_send_attempts = 'Use a whole number from 1 to 10.'
  if (!isInt(d.send_batch_size, 1, 100)) e.send_batch_size = 'Use a whole number from 1 to 100.'
  if (!isInt(d.intake_batch_size, 1, 100)) e.intake_batch_size = 'Use a whole number from 1 to 100.'
  if (!isInt(d.llm_min_body_chars, 20, 5000) || !isInt(d.llm_max_body_chars, 50, 10000))
    e.body_chars = 'Use whole numbers (minimum 20, maximum up to 10000).'
  else if (d.llm_min_body_chars >= d.llm_max_body_chars) e.body_chars = 'The minimum must be below the maximum.'

  if (!d.contactable_lead_statuses.length) e.contactable_lead_statuses = 'Pick at least one, or nobody can be emailed.'
  const ae = d.auto_enroll
  if (ae.enabled && !ae.sources?.length) e.auto_enroll = 'Add at least one source, or turn auto-enroll off.'
  else if (!isInt(ae.batch_size, 1, 500)) e.auto_enroll = 'Leads per run: use a whole number from 1 to 500.'

  const email = String(d.reply_alert_email || '').trim()
  if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) e.reply_alert_email = 'That does not look like an email address.'
  return e
}

function KillSwitch({ enabled, onSaved }) {
  const [{ busy, error }, save] = useAction((value) => api.putSetting('sequence_enabled', value))
  return (
    <Card className={`kill-switch ${enabled ? '' : 'is-off'}`}>
      <div className="kill-row">
        <div>
          <div className="kill-title">
            <h2>Sending</h2>
            <Badge tone={enabled ? 'good' : 'warning'}>{enabled ? 'On' : 'Paused'}</Badge>
          </div>
          <p className="muted">
            {enabled
              ? 'Due emails go out on schedule. Turn off to stop all sending immediately; this saves right away.'
              : 'All sending is paused. Drafting and reply checks still run. Turn on to resume.'}
          </p>
        </div>
        <Toggle
          label="Sending"
          checked={enabled}
          disabled={busy}
          onChange={async (v) => {
            if ((await save(v)) !== undefined) onSaved()
          }}
        />
      </div>
      <ErrorNote error={error} />
    </Card>
  )
}

function Section({ id, title, subtitle, children }) {
  return (
    <section id={id} className="set-anchor">
      <Card title={title} subtitle={subtitle} className="set-section">
        <div className="set-rows">{children}</div>
      </Card>
    </section>
  )
}

function AdvancedEditor({ settingKey, value, description, onChange }) {
  const [text, setText] = useState(JSON.stringify(value, null, 2))
  const [error, setError] = useState(null)
  useEffect(() => setText(JSON.stringify(value, null, 2)), [value])
  return (
    <Row label={<code>{settingKey}</code>} description={description} error={error}>
      <textarea
        className="code"
        spellCheck={false}
        rows={Math.min(8, text.split('\n').length)}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          try {
            onChange(JSON.parse(e.target.value))
            setError(null)
          } catch {
            setError('Not valid JSON yet.')
          }
        }}
      />
    </Row>
  )
}

export default function Settings() {
  const [original, setOriginal] = useState(null)
  const [descriptions, setDescriptions] = useState({})
  const [draft, setDraft] = useState(null)
  const [savedAt, setSavedAt] = useState(null)

  const [{ busy: loading, error: loadError }, load] = useAction(async () => {
    const res = await api.settings()
    const values = { ...DEFAULTS }
    const desc = {}
    for (const s of res.settings || []) {
      values[s.key] = s.value
      desc[s.key] = s.description
    }
    setOriginal(values)
    setDescriptions(desc)
    setDraft(toDraft(values))
  })

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const current = useMemo(() => (draft ? fromDraft(draft) : null), [draft])
  const dirtyKeys = useMemo(
    () =>
      current
        ? Object.keys(current).filter((k) => k !== 'sequence_enabled' && !same(current[k], original[k]))
        : [],
    [current, original],
  )
  const errors = useMemo(() => (draft ? validate(draft) : {}), [draft])
  const hasErrors = Object.keys(errors).length > 0

  const [{ busy: saving, error: saveError }, save] = useAction(async () => {
    for (const key of dirtyKeys) {
      await api.putSetting(key, current[key])
    }
    setSavedAt(new Date())
    await load()
  })

  // Warn before leaving with unsaved changes.
  useEffect(() => {
    if (!dirtyKeys.length) return undefined
    const warn = (e) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirtyKeys.length])

  if (!draft) {
    return (
      <div className="stack">
        <div className="toolbar">
          <h1>Settings</h1>
          {loading && <Spinner />}
        </div>
        <ErrorNote error={loadError} onRetry={load} />
      </div>
    )
  }

  const set = (key) => (value) => setDraft((d) => ({ ...d, [key]: value }))
  // Offer "Reset to default" only when the current value differs.
  const resetter = (key) =>
    same(key === 'business_hours_by_region' ? rowsToRegions(draft[key]) : draft[key], DEFAULTS[key])
      ? undefined
      : () => set(key)(key === 'business_hours_by_region' ? regionsToRows(DEFAULTS[key]) : DEFAULTS[key])
  const riskyPicked = draft.contactable_lead_statuses.filter((x) => RISKY_STATUSES.includes(x))
  const bh = draft.business_hours
  const setBH = (patch) => set('business_hours')({ ...bh, ...patch })
  const gaps = draft.step_gaps_business_days
  const ae = draft.auto_enroll
  const setAE = (patch) => set('auto_enroll')({ ...ae, ...patch })
  const lastGap = Number(gaps[gaps.length - 1]) || 0
  const advancedKeys = Object.keys(draft).filter((k) => !KNOWN_KEYS.has(k))

  return (
    <div className="stack settings-page">
      <div className="toolbar">
        <div>
          <h1>Settings</h1>
          <p className="muted">Changes apply within a minute of saving. No restart needed.</p>
        </div>
        {loading && <Spinner />}
      </div>
      <ErrorNote error={loadError} onRetry={load} />

      <KillSwitch enabled={original.sequence_enabled !== false} onSaved={load} />

      <nav className="set-nav" aria-label="Jump to section">
        {SECTIONS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className="chip"
            onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
          >
            {label}
          </button>
        ))}
      </nav>

      <Section id="set-schedule" title="Schedule" subtitle="When emails go out. Times are in each lead's own local time, so a lead in London gets London hours.">
        <Row
          label="Business hours"
          description={`Emails go out ${describeDays(bh.weekdays)}, ${hourLabel(bh.start_hour)}–${hourLabel(bh.end_hour)}.`}
          error={errors.business_hours}
          onReset={resetter('business_hours')}
        >
          <div className="presets">
            {HOUR_PRESETS.map(([label, value]) => (
              <button
                key={label}
                type="button"
                className={`chip chip-sm ${same(bh, value) ? 'is-active' : ''}`}
                onClick={() => set('business_hours')(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="hours">
            <HourSelect label="Start" value={bh.start_hour} onChange={(h) => setBH({ start_hour: h })} />
            <span className="muted">to</span>
            <HourSelect label="End" from={1} to={24} value={bh.end_hour} onChange={(h) => setBH({ end_hour: h })} />
          </div>
          <WeekdayPicker label="Work days" value={bh.weekdays} onChange={(d) => setBH({ weekdays: d })} />
        </Row>
        <Row
          label="Sequence"
          description={`${gaps.length} email${gaps.length === 1 ? '' : 's'} over ${lastGap} business day${lastGap === 1 ? '' : 's'}. A reply, bounce or unsubscribe stops the rest automatically.`}
          error={errors.step_gaps_business_days}
          onReset={resetter('step_gaps_business_days')}
        >
          <GapsEditor value={gaps} onChange={set('step_gaps_business_days')} weekdays={bh.weekdays} />
        </Row>
        <Row
          label="Regional differences"
          description="For countries with a different work week or hours, e.g. Bangladesh (Sun–Thu). Anything left on Default follows the business hours above."
          error={errors.business_hours_by_region}
          onReset={resetter('business_hours_by_region')}
        >
          <RegionsEditor rows={draft.business_hours_by_region} onChange={set('business_hours_by_region')} defaults={bh} />
        </Row>
      </Section>

      <Section id="set-limits" title="Sending limits" subtitle="Sending too much too fast lands you in spam. These keep each inbox's volume safe.">
        <Row
          label="Daily limit per inbox"
          description="Emails one inbox may send per day, unless the inbox has its own limit. Recommended: 10–20 for a new inbox, raised slowly over weeks."
          error={errors.default_daily_cap}
          warning={draft.default_daily_cap > 50 ? 'Over 50 a day from one inbox is risky unless it has been warmed up.' : null}
          onReset={resetter('default_daily_cap')}
        >
          <NumberInput value={draft.default_daily_cap} min={1} max={2000} suffix="emails / day" onChange={set('default_daily_cap')} />
        </Row>
        <Row label="Retries" description="If sending fails (e.g. a network hiccup), try this many times before giving up." error={errors.max_send_attempts} onReset={resetter('max_send_attempts')}>
          <NumberInput value={draft.max_send_attempts} min={1} max={10} suffix="attempts" onChange={set('max_send_attempts')} />
        </Row>
        <Row label="Emails per run" description="How many due emails one sending run (every 3 minutes) handles. Rarely needs changing." error={errors.send_batch_size} onReset={resetter('send_batch_size')}>
          <NumberInput value={draft.send_batch_size} min={1} max={100} suffix="per run" onChange={set('send_batch_size')} />
        </Row>
      </Section>

      <Section id="set-who" title="Who can be emailed" subtitle="Leads come from the shared leads table; these rules decide who is allowed in.">
        <Row
          label="Allowed lead statuses"
          description="Only leads with one of these statuses can be enrolled. If another project changes a lead to anything else (DNC, Paid, …), its emails stop."
          error={errors.contactable_lead_statuses}
          warning={
            riskyPicked.length
              ? `${riskyPicked.join(', ')} usually means the person should not get cold email.`
              : null
          }
          onReset={resetter('contactable_lead_statuses')}
        >
          <CheckList options={LEAD_STATUSES} value={draft.contactable_lead_statuses} onChange={set('contactable_lead_statuses')} />
        </Row>
        <Row
          label="Skip leads in another email journey"
          description="Don't enroll a lead who already has an email-nurture journey, so nobody gets two campaigns at once."
        >
          <Toggle label="Skip leads in another email journey" checked={draft.skip_if_in_email_nurture === true} onChange={set('skip_if_in_email_nurture')} />
        </Row>
        <Row
          label="Auto-enroll"
          description="Enroll allowed leads from the listed sources automatically, every drafting run. When off, leads only come in through the Enroll page."
          error={errors.auto_enroll}
        >
          <div className="stack-sm">
            <label className="toggle-line">
              <Toggle label="Auto-enroll" checked={ae.enabled === true} onChange={(v) => setAE({ enabled: v })} />
              <span>{ae.enabled ? 'On' : 'Off'}</span>
            </label>
            {ae.enabled && (
              <>
                <span className="field-label">Lead sources (the leads table's source value)</span>
                <TagInput value={ae.sources} onChange={(v) => setAE({ sources: v })} placeholder="Type a source and press Enter" />
                <NumberInput value={ae.batch_size} min={1} max={500} suffix="leads per run" onChange={(v) => setAE({ batch_size: v })} />
              </>
            )}
          </div>
        </Row>
      </Section>

      <Section id="set-ai" title="AI drafting" subtitle="Quality checks each drafted email must pass before it can be sent.">
        <Row
          label="Email length"
          description={`About ${words(draft.llm_min_body_chars)}–${words(draft.llm_max_body_chars)} words. Emails outside this range are rewritten, then flagged for review. Short emails get more replies.`}
          error={errors.body_chars}
          onReset={
            draft.llm_min_body_chars !== DEFAULTS.llm_min_body_chars || draft.llm_max_body_chars !== DEFAULTS.llm_max_body_chars
              ? () => setDraft((d) => ({ ...d, llm_min_body_chars: DEFAULTS.llm_min_body_chars, llm_max_body_chars: DEFAULTS.llm_max_body_chars }))
              : undefined
          }
        >
          <div className="hours">
            <NumberInput value={draft.llm_min_body_chars} min={20} max={5000} width={90} onChange={set('llm_min_body_chars')} />
            <span className="muted">to</span>
            <NumberInput value={draft.llm_max_body_chars} min={50} max={10000} width={90} suffix="characters" onChange={set('llm_max_body_chars')} />
          </div>
        </Row>
        <Row label="Leads per drafting run" description="How many new leads one drafting run (every 5 minutes) writes emails for. Each costs one AI call." error={errors.intake_batch_size} onReset={resetter('intake_batch_size')}>
          <NumberInput value={draft.intake_batch_size} min={1} max={100} suffix="per run" onChange={set('intake_batch_size')} />
        </Row>
      </Section>

      <Section id="set-notify" title="Notifications">
        <Row
          label="Reply alerts"
          description="Gets an email with the prospect's message for every new reply. Leave empty for no alerts. Test it from the Replies page."
          error={errors.reply_alert_email}
        >
          <input
            type="email"
            value={draft.reply_alert_email || ''}
            onChange={(e) => set('reply_alert_email')(e.target.value)}
            placeholder="you@company.com"
          />
        </Row>
      </Section>

      {advancedKeys.length > 0 && (
        <details className="card advanced">
          <summary>Advanced ({advancedKeys.length})</summary>
          <div className="set-rows">
            {advancedKeys.map((k) => (
              <AdvancedEditor key={k} settingKey={k} value={draft[k]} description={descriptions[k]} onChange={set(k)} />
            ))}
          </div>
        </details>
      )}

      {(dirtyKeys.length > 0 || saveError || savedAt) && (
        <div className={`save-bar ${dirtyKeys.length ? 'is-dirty' : ''}`} role="status">
          <span>
            {saveError ? (
              <span className="field-error">Could not save: {saveError.message}</span>
            ) : dirtyKeys.length ? (
              hasErrors ? (
                <span className="field-error">Fix the highlighted fields to save.</span>
              ) : (
                `${dirtyKeys.length} unsaved change${dirtyKeys.length === 1 ? '' : 's'}`
              )
            ) : (
              <>
                <Badge tone="good">Saved</Badge> {savedAt.toLocaleTimeString()}
              </>
            )}
          </span>
          {dirtyKeys.length > 0 && (
            <div className="save-actions">
              <button className="btn btn-ghost btn-sm" disabled={saving} onClick={() => setDraft(toDraft(original))}>
                Discard
              </button>
              <button className="btn btn-primary btn-sm" disabled={saving || hasErrors} onClick={() => save()}>
                {saving ? <Spinner /> : null} Save changes
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

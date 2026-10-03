import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { DRAFT_KINDS } from '../../labels.js'
import { AlertSettings } from '../Replies.jsx'
import { NumberInput, Row, Toggle } from '../settingsControls.jsx'
import { Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

const DEFAULTS = {
  enabled: true,
  auto_send_delay_minutes: 120,
  auto_send_kinds: Object.keys(DRAFT_KINDS),
  min_confidence: 0.7,
  not_now_days: 60,
  ooo_default_days: 7,
  meeting: { slots_to_offer: 2, days_ahead: 7, min_notice_hours: 12 },
}

const isInt = (n, lo, hi) => Number.isInteger(n) && n >= lo && n <= hi
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)

function validate(d) {
  const e = {}
  if (!isInt(d.auto_send_delay_minutes, 0, 10080)) e.delay = 'Use whole minutes from 0 to 10080 (a week).'
  const c = Math.round(d.min_confidence * 100)
  if (!isInt(c, 0, 100)) e.confidence = 'Use a percentage from 0 to 100.'
  if (!isInt(d.not_now_days, 7, 365)) e.not_now = 'Use 7 to 365 days.'
  if (!isInt(d.ooo_default_days, 1, 60)) e.ooo = 'Use 1 to 60 days.'
  const m = d.meeting
  if (!isInt(m.slots_to_offer, 1, 5)) e.slots = 'Offer 1 to 5 times.'
  if (!isInt(m.days_ahead, 1, 30)) e.days = 'Look 1 to 30 days ahead.'
  if (!isInt(m.min_notice_hours, 0, 168)) e.notice = 'Use 0 to 168 hours.'
  return e
}

function CalendarCard() {
  const [tz, setTz] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  const [{ result: status, error: statusError }, loadStatus] = useAction(() => api.calendarStatus())
  const [{ busy, error, result }, check] = useAction(() => api.calendarSlots(tz))
  useEffect(() => {
    loadStatus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <Card title="Calendar" subtitle="Where meeting times come from and bookings go. Set CALENDAR_PROVIDER in .env (calcom, fake or none).">
      {status && (
        <div className="note">
          <Badge tone={status.ready ? 'good' : status.error ? 'critical' : 'neutral'}>
            {status.ready ? 'Connected' : status.error ? 'Problem' : 'Off'}
          </Badge>
          <span>
            Provider: <strong>{status.provider}</strong>
            {status.booking_link && <> · booking link <a href={status.booking_link} target="_blank" rel="noreferrer">{status.booking_link}</a></>}
            {status.error && <> · {status.error}</>}
            {!status.ready && !status.error && ' · interested leads are asked which times suit them instead'}
          </span>
        </div>
      )}
      <ErrorNote error={statusError} />
      {status?.ready && (
        <>
          <form className="inline-form calendar-check" onSubmit={(e) => { e.preventDefault(); check() }}>
            <input value={tz} onChange={(e) => setTz(e.target.value)} aria-label="Lead timezone" placeholder="Lead timezone, e.g. Europe/London" />
            <button className="btn" disabled={busy}>{busy ? <Spinner /> : null} Check free times</button>
          </form>
          <ErrorNote error={error} />
          {result && (
            <div className="did">
              <span className="did-title">
                {result.free_count} free slot{result.free_count === 1 ? '' : 's'} in the next 7 days. A lead in {tz} would be offered:
              </span>
              <ul>
                {result.would_offer.length ? result.would_offer.map((s) => <li key={s}>{s}</li>) : <li>nothing inside their business hours</li>}
              </ul>
            </div>
          )}
        </>
      )}
    </Card>
  )
}

export default function TriageSettings() {
  const [original, setOriginal] = useState(null)
  const [draft, setDraft] = useState(null)
  const [{ busy: loading, error: loadError }, load] = useAction(async () => {
    const res = await api.settings()
    const stored = res.settings?.find((s) => s.key === 'triage')?.value || {}
    const merged = { ...DEFAULTS, ...stored, meeting: { ...DEFAULTS.meeting, ...(stored.meeting || {}) } }
    setOriginal(merged)
    setDraft(merged)
  })
  const [{ busy: saving, error: saveError, result: saved }, save] = useAction(async () => {
    await api.putSetting('triage', draft)
    setOriginal(draft)
    return true
  })
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!draft) {
    return (
      <div className="stack">
        <h1>Triage settings</h1>
        {loading && <Spinner />}
        <ErrorNote error={loadError} onRetry={load} />
      </div>
    )
  }

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }))
  const setMeeting = (patch) => setDraft((d) => ({ ...d, meeting: { ...d.meeting, ...patch } }))
  const errors = validate(draft)
  const dirty = !same(draft, original)
  const kinds = new Set(draft.auto_send_kinds)
  const toggleKind = (k) => set({ auto_send_kinds: kinds.has(k) ? draft.auto_send_kinds.filter((x) => x !== k) : [...draft.auto_send_kinds, k] })
  const hours = (draft.auto_send_delay_minutes / 60).toFixed(draft.auto_send_delay_minutes % 60 ? 1 : 0)

  return (
    <div className="stack settings-page">
      <div className="toolbar">
        <div>
          <h1>Triage settings</h1>
          <p className="muted">How replies are handled. Changes apply within a minute of saving.</p>
        </div>
      </div>

      <Card className={`kill-switch ${draft.enabled ? '' : 'is-off'}`}>
        <div className="kill-row">
          <div>
            <div className="kill-title">
              <h2>Reply triage</h2>
              <Badge tone={draft.enabled ? 'good' : 'warning'}>{draft.enabled ? 'On' : 'Paused'}</Badge>
            </div>
            <p className="muted">
              {draft.enabled
                ? 'New replies are classified and acted on; drafts send as configured.'
                : 'Replies still stop sequences and alert you, but are not classified and no drafts send.'}
            </p>
          </div>
          <Toggle label="Reply triage" checked={draft.enabled} onChange={(v) => set({ enabled: v })} />
        </div>
      </Card>

      <Card title="Drafts" subtitle="Every reply to a prospect starts as a draft you can approve, edit or reject.">
        <div className="set-rows">
          <Row label="Auto-send after" error={errors.delay}
            description={`If nobody approves or rejects a draft, it sends ${hours} hour${hours === '1' ? '' : 's'} after it was written, moved into the lead's business hours.`}>
            <NumberInput value={draft.auto_send_delay_minutes} min={0} max={10080} suffix="minutes"
              onChange={(v) => set({ auto_send_delay_minutes: v })} />
          </Row>
          <Row label="Drafts that may auto-send" description="Unticked kinds always wait for a person.">
            <div className="checklist">
              {Object.entries(DRAFT_KINDS).map(([k, label]) => (
                <label key={k} className={`check-pill ${kinds.has(k) ? 'is-on' : ''}`}>
                  <input type="checkbox" checked={kinds.has(k)} onChange={() => toggleKind(k)} />
                  {label}
                </label>
              ))}
            </div>
          </Row>
          <Row label="Confidence needed" error={errors.confidence}
            description="Below this, the AI's reading is treated as a guess: nothing auto-sends, and no booking, unsubscribe or snooze happens until you confirm.">
            <NumberInput value={Math.round(draft.min_confidence * 100)} min={0} max={100} suffix="%"
              onChange={(v) => set({ min_confidence: v === '' ? '' : v / 100 })} />
          </Row>
        </div>
      </Card>

      <Card title="Categories">
        <div className="set-rows">
          <Row label='"Not now" comes back after' error={errors.not_now}
            description="The lead is paused, then gets a fresh sequence automatically.">
            <NumberInput value={draft.not_now_days} min={7} max={365} suffix="days" onChange={(v) => set({ not_now_days: v })} />
          </Row>
          <Row label="Out of office without a date" error={errors.ooo}
            description="When an away message gives a return date, emails resume the business day after. Without one, they wait this long.">
            <NumberInput value={draft.ooo_default_days} min={1} max={60} suffix="days" onChange={(v) => set({ ooo_default_days: v })} />
          </Row>
        </div>
      </Card>

      <Card title="Meetings" subtitle="Times offered to interested leads, always inside their own business hours.">
        <div className="set-rows">
          <Row label="Times to offer" error={errors.slots}>
            <NumberInput value={draft.meeting.slots_to_offer} min={1} max={5} suffix="options, on different days" onChange={(v) => setMeeting({ slots_to_offer: v })} />
          </Row>
          <Row label="Look ahead" error={errors.days}>
            <NumberInput value={draft.meeting.days_ahead} min={1} max={30} suffix="days" onChange={(v) => setMeeting({ days_ahead: v })} />
          </Row>
          <Row label="Minimum notice" error={errors.notice} description="No slot sooner than this after the reply is drafted.">
            <NumberInput value={draft.meeting.min_notice_hours} min={0} max={168} suffix="hours" onChange={(v) => setMeeting({ min_notice_hours: v })} />
          </Row>
        </div>
      </Card>

      <CalendarCard />
      <AlertSettings />

      {(dirty || saveError || saved) && (
        <div className={`save-bar ${dirty ? 'is-dirty' : ''}`} role="status">
          <span>
            {saveError ? <span className="field-error">Could not save: {saveError.message}</span>
              : dirty ? (Object.keys(errors).length ? <span className="field-error">Fix the highlighted fields to save.</span> : 'Unsaved changes')
                : <><Badge tone="good">Saved</Badge></>}
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

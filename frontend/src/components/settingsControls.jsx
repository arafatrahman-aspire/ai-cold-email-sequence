import { useState } from 'react'

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] // 0 = Monday

export const hourLabel = (h) => `${String(h).padStart(2, '0')}:00`

// "Mon–Fri", "Sun–Thu", or "Mon, Wed, Fri"
export function describeDays(days) {
  const set = [...new Set(days)].sort((a, b) => a - b)
  if (!set.length) return 'no days'
  if (set.length === 7) return 'every day'
  // Treat the week as circular so Sun–Thu (6,0,1,2,3) reads as a range.
  for (let start = 0; start < 7; start++) {
    const run = Array.from({ length: set.length }, (_, i) => (start + i) % 7)
    if (run.every((d) => set.includes(d))) {
      return set.length === 1 ? WEEKDAYS[start] : `${WEEKDAYS[run[0]]}–${WEEKDAYS[run[run.length - 1]]}`
    }
  }
  return set.map((d) => WEEKDAYS[d]).join(', ')
}

export function Row({ label, description, children, error, warning, onReset }) {
  return (
    <div className={`set-row ${error ? 'has-error' : ''}`}>
      <div className="set-row-text">
        <span className="set-row-label">{label}</span>
        {description && <span className="set-row-desc">{description}</span>}
        {onReset && (
          <button type="button" className="link-btn" onClick={onReset}>
            Reset to default
          </button>
        )}
      </div>
      <div className="set-row-control">
        {children}
        {error && <span className="field-error">{error}</span>}
        {!error && warning && <span className="field-warning">⚠ {warning}</span>}
      </div>
    </div>
  )
}

export function Toggle({ checked, onChange, label, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`toggle ${checked ? 'is-on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span className="toggle-knob" />
    </button>
  )
}

export function NumberInput({ value, onChange, min, max, suffix, width = 110 }) {
  return (
    <span className="num-input">
      <input
        type="number"
        min={min}
        max={max}
        value={value ?? ''}
        style={{ width }}
        onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
      />
      {suffix && <span className="muted">{suffix}</span>}
    </span>
  )
}

export function HourSelect({ value, onChange, from = 0, to = 23, allowDefault, label }) {
  const hours = []
  for (let h = from; h <= to; h++) hours.push(h)
  return (
    <select
      aria-label={label}
      className="hour-select"
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
    >
      {allowDefault && <option value="">Default</option>}
      {hours.map((h) => (
        <option key={h} value={h}>
          {h === 24 ? '24:00 (midnight)' : hourLabel(h)}
        </option>
      ))}
    </select>
  )
}

export function WeekdayPicker({ value, onChange, label }) {
  const set = new Set(value || [])
  return (
    <div className="weekdays" role="group" aria-label={label}>
      {WEEKDAYS.map((name, d) => (
        <button
          key={name}
          type="button"
          aria-pressed={set.has(d)}
          className={`day ${set.has(d) ? 'is-on' : ''}`}
          onClick={() => {
            const next = new Set(set)
            if (next.has(d)) next.delete(d)
            else next.add(d)
            onChange([...next].sort((a, b) => a - b))
          }}
        >
          {name}
        </button>
      ))}
    </div>
  )
}

// Stored as business-day offsets from the start ([0, 3, 7, 12]) but edited as
// waits between emails ([0, 3, 4, 5]), which is how people think about it.
const toWaits = (gaps) => gaps.map((g, i) => (i === 0 ? g : g - gaps[i - 1]))
const toGaps = (waits) => waits.reduce((acc, w, i) => [...acc, (i ? acc[i - 1] : 0) + (Number(w) || 0)], [])

function addBusinessDays(date, n, weekdays) {
  // JS getDay(): 0 = Sunday; settings use 0 = Monday.
  const isWork = (d) => weekdays.includes((d.getDay() + 6) % 7)
  const d = new Date(date)
  while (!isWork(d)) d.setDate(d.getDate() + 1)
  for (let left = n; left > 0; ) {
    d.setDate(d.getDate() + 1)
    if (isWork(d)) left--
  }
  return d
}

export function GapsEditor({ value, onChange, weekdays }) {
  const gaps = Array.isArray(value) ? value : []
  const waits = toWaits(gaps)
  // Keep the delay field open while it is being edited, even if briefly 0.
  const [delayFirst, setDelayFirst] = useState(waits[0] > 0)
  const setWait = (i, v) => onChange(toGaps(waits.map((w, j) => (j === i ? (v === '' ? '' : v) : w))))
  const today = new Date()
  const days = weekdays?.length ? weekdays : [0, 1, 2, 3, 4]
  const fmt = (d) => d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })

  return (
    <div className="gaps">
      {waits.map((w, i) => (
        <div className="gap-row" key={i}>
          <span className="gap-num">{i + 1}</span>
          {i === 0 ? (
            <span className="gap-text">
              First email{' '}
              {delayFirst || w > 0 ? (
                <>
                  after <NumberInput value={w} min={0} max={30} width={64} onChange={(v) => setWait(0, v)} /> business days
                  <button
                    type="button"
                    className="link-btn"
                    onClick={() => {
                      setDelayFirst(false)
                      setWait(0, 0)
                    }}
                  >
                    no delay
                  </button>
                </>
              ) : (
                <>
                  <strong>as soon as possible</strong>
                  <button
                    type="button"
                    className="link-btn"
                    onClick={() => {
                      setDelayFirst(true)
                      setWait(0, 1)
                    }}
                  >
                    delay it
                  </button>
                </>
              )}
            </span>
          ) : (
            <span className="gap-text">
              Wait <NumberInput value={w} min={1} max={30} width={64} onChange={(v) => setWait(i, v)} /> business days
              after email {i}
            </span>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-icon"
            aria-label={`Remove email ${i + 1}`}
            disabled={gaps.length <= 1}
            onClick={() => onChange(toGaps(waits.filter((_, j) => j !== i)))}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        className="btn btn-ghost btn-sm add-btn"
        disabled={gaps.length >= 8}
        onClick={() => onChange(toGaps([...waits, 4]))}
      >
        + Add email
      </button>
      {gaps.every((g) => Number.isInteger(g)) && (
        <div className="timeline" aria-label="Example schedule">
          <span className="muted">Example for a lead enrolled today:</span>
          <ol>
            {gaps.map((g, i) => (
              <li key={i}>
                <span className="timeline-dot" aria-hidden="true" />
                Email {i + 1} · {fmt(addBusinessDays(today, g, days))}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}

// Per-region overrides, edited as a list of {key, ov} rows so two rows may
// briefly share a key while typing; validation catches it before saving.
export const regionsToRows = (obj) => Object.entries(obj || {}).map(([key, ov]) => ({ key, ov: ov || {} }))
// Country codes are stored upper-case ("bd" -> "BD"); timezones as typed.
export const normalizeRegionKey = (key) => {
  const k = key.trim()
  return /^[A-Za-z]{2}$/.test(k) ? k.toUpperCase() : k
}
export const rowsToRegions = (rows) => Object.fromEntries(rows.map((r) => [normalizeRegionKey(r.key), r.ov]))

const REGION_NAMES = (() => {
  try {
    return new Intl.DisplayNames(['en'], { type: 'region', fallback: 'none' })
  } catch {
    return null
  }
})()

// Every ISO country code the browser can name, e.g. ["BD", "Bangladesh"].
const COUNTRIES = (() => {
  if (!REGION_NAMES) return []
  const out = []
  const A = 65
  for (let i = 0; i < 26; i++)
    for (let j = 0; j < 26; j++) {
      const code = String.fromCharCode(A + i, A + j)
      if (['EU', 'EZ', 'UN', 'ZZ', 'QO'].includes(code)) continue
      let name
      try {
        name = REGION_NAMES.of(code)
      } catch {
        name = undefined
      }
      if (name && name !== code) out.push([code, name])
    }
  return out.sort((x, y) => x[1].localeCompare(y[1]))
})()

export function regionName(key) {
  const k = normalizeRegionKey(key)
  if (/^[A-Z]{2}$/.test(k)) return COUNTRIES.find(([c]) => c === k)?.[1] || null
  return k.includes('/') ? `Timezone ${k.replace(/_/g, ' ')}` : null
}

// Accept "Bangladesh" as well as "BD".
function resolveRegionInput(text) {
  const t = text.trim().toLowerCase()
  const byName = COUNTRIES.find(([, name]) => name.toLowerCase() === t)
  return byName ? byName[0] : text
}

const TIMEZONES = (() => {
  try {
    return new Set(Intl.supportedValuesOf('timeZone'))
  } catch {
    return null
  }
})()

export function regionKeyError(key, allKeys) {
  const k = normalizeRegionKey(key)
  if (!k) return 'Pick a country or timezone'
  if (allKeys.filter((x) => normalizeRegionKey(x) === k).length > 1) return `${k} is listed twice`
  if (/^[A-Z]{2}$/.test(k)) return null
  if (TIMEZONES && !TIMEZONES.has(k) && k !== 'UTC') {
    return 'Pick a country from the list, or a timezone like America/Phoenix'
  }
  return null
}

export function RegionsEditor({ rows, onChange, defaults }) {
  const update = (index, patch) => onChange(rows.map((r, i) => (i === index ? { ...r, ...patch } : r)))
  const keys = rows.map((r) => r.key)

  return (
    <div className="regions">
      {rows.length === 0 && <p className="muted">No regional changes: every lead uses the default hours.</p>}
      {rows.map(({ key, ov }, i) => {
        const days = ov.weekdays
        const error = regionKeyError(key, keys)
        return (
          <div className={`region ${error ? 'has-error' : ''}`} key={i}>
            <div className="region-head">
              <input
                className="region-key"
                value={key}
                list="region-options"
                aria-label="Country or timezone"
                aria-invalid={Boolean(error)}
                onChange={(e) => update(i, { key: resolveRegionInput(e.target.value) })}
                placeholder="Country (e.g. Bangladesh) or timezone"
              />
              <span className="region-summary">
                <strong>{regionName(key) || ' '}</strong>
                <span className="muted">
                  {describeDays(days ?? defaults.weekdays)},{' '}
                  {hourLabel(ov.start_hour ?? defaults.start_hour)}–{hourLabel(ov.end_hour ?? defaults.end_hour)}
                </span>
              </span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label={`Remove ${key || 'region'}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
            {error && <span className="field-error">{error}</span>}
            <div className="region-body">
              <label className="region-field">
                <span className="field-label">From</span>
                <HourSelect
                  allowDefault
                  label="Start hour"
                  value={ov.start_hour}
                  onChange={(h) => update(i, { ov: clean({ ...ov, start_hour: h }) })}
                />
              </label>
              <label className="region-field">
                <span className="field-label">Until</span>
                <HourSelect
                  allowDefault
                  from={1}
                  to={24}
                  label="End hour"
                  value={ov.end_hour}
                  onChange={(h) => update(i, { ov: clean({ ...ov, end_hour: h }) })}
                />
              </label>
              <div className="region-field region-days">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={Array.isArray(days)}
                    onChange={(e) =>
                      update(i, { ov: clean({ ...ov, weekdays: e.target.checked ? [...defaults.weekdays] : null }) })
                    }
                  />
                  <span>Different work days</span>
                </label>
                {Array.isArray(days) && (
                  <WeekdayPicker
                    label={`Work days for ${key}`}
                    value={days}
                    onChange={(d) => update(i, { ov: { ...ov, weekdays: d } })}
                  />
                )}
              </div>
            </div>
          </div>
        )
      })}
      <button type="button" className="btn btn-ghost btn-sm add-btn" onClick={() => onChange([...rows, { key: '', ov: {} }])}>
        + Add country or timezone
      </button>
      <datalist id="region-options">
        {COUNTRIES.map(([code, name]) => (
          <option key={code} value={code} label={name}>
            {name}
          </option>
        ))}
        {TIMEZONES && [...TIMEZONES].map((tz) => <option key={tz} value={tz} />)}
      </datalist>
    </div>
  )
}

// Drop "use the default" fields so the stored override stays minimal.
function clean(ov) {
  return Object.fromEntries(Object.entries(ov).filter(([, v]) => v !== null && v !== undefined))
}

export function CheckList({ options, value, onChange }) {
  const set = new Set(value || [])
  const extra = (value || []).filter((v) => !options.includes(v))
  return (
    <div className="checklist">
      {[...options, ...extra].map((opt) => (
        <label key={opt} className={`check-pill ${set.has(opt) ? 'is-on' : ''}`}>
          <input
            type="checkbox"
            checked={set.has(opt)}
            // Keep the existing order (append / remove only), so ticking and
            // unticking an option leaves the value exactly as it was.
            onChange={(e) =>
              onChange(e.target.checked ? [...(value || []), opt] : (value || []).filter((v) => v !== opt))
            }
          />
          {opt}
        </label>
      ))}
    </div>
  )
}

export function TagInput({ value, onChange, placeholder }) {
  const [text, setText] = useState('')
  const tags = value || []
  const add = () => {
    const t = text.trim()
    if (t && !tags.includes(t)) onChange([...tags, t])
    setText('')
  }
  return (
    <div className="tags">
      {tags.map((t) => (
        <span className="tag" key={t}>
          {t}
          <button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(tags.filter((x) => x !== t))}>
            ×
          </button>
        </span>
      ))}
      <input
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onBlur={add}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            add()
          } else if (e.key === 'Backspace' && !text && tags.length) {
            onChange(tags.slice(0, -1))
          }
        }}
      />
    </div>
  )
}

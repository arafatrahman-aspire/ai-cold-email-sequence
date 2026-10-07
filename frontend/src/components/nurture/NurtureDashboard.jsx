import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { formatNumber, NURTURE_PERSONAS, TEMPERATURES } from '../../labels.js'
import { StatTile } from '../Dashboard.jsx'
import { Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

const RANGES = [
  [30, 'Last 30 days'],
  [90, 'Last 90 days'],
  [365, 'Last year'],
]

const pct = (v, digits = 0) => (v == null ? '—' : `${(v * 100).toFixed(digits)}%`)
const day = (d) => d.toISOString().slice(0, 10)

// Anything that stops nurture from sending, and the modes that change what it does.
export function NurtureStatus({ status }) {
  if (!status) return null
  return (
    <>
      {status.paused && (
        <div className="note note-warning" role="status">
          <Badge tone="warning">Paused</Badge>
          <span>Nothing is written or sent until you switch it back on in Settings.</span>
        </div>
      )}
      {status.test_mode?.enabled && (
        <div className="note note-info" role="status">
          <Badge tone="info">Test mode</Badge>
          <span>
            A day is {status.test_mode.minutes_per_day} minute{status.test_mode.minutes_per_day === 1 ? '' : 's'}; only{' '}
            {status.test_mode.allow_list?.length ? status.test_mode.allow_list.join(', ') : 'allow-listed addresses (none yet)'}{' '}
            receive email.
          </span>
        </div>
      )}
      {status.problems?.length > 0 && (
        <div className="note note-critical" role="alert">
          <Badge tone="critical">Not sending</Badge>
          <ul className="plain-list">{status.problems.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
      {status.warnings?.length > 0 && (
        <div className="note note-warning">
          <Badge tone="warning">Check</Badge>
          <ul className="plain-list">{status.warnings.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
    </>
  )
}

// An email is marked weakest only with enough sends to mean something (5),
// and only when it is clearly below the best one.
const MIN_SENDS = 5

function StepTable({ steps }) {
  const rates = steps.map((s) => (s.sent ? s.clicks / s.sent : null))
  const known = steps.filter((s) => s.sent >= MIN_SENDS).map((s) => s.clicks / s.sent)
  const weakest = known.length > 1 && Math.min(...known) < Math.max(...known) ? Math.min(...known) : null
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Email</th><th className="num">Sent</th><th className="num">Clicked</th><th className="num">Click rate</th><th className="num">Unsubscribed after</th></tr>
        </thead>
        <tbody>
          {steps.map((s, i) => (
            <tr key={s.step}>
              <td>
                Email {s.step}
                {weakest != null && s.sent >= MIN_SENDS && rates[i] === weakest && <> <Badge tone="warning">Weakest</Badge></>}
              </td>
              <td className="num">{formatNumber(s.sent)}</td>
              <td className="num">{formatNumber(s.clicks)}</td>
              <td className="num">{pct(rates[i], 1)}</td>
              <td className="num">{formatNumber(s.unsubscribes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Breakdown({ rows }) {
  if (!rows.length) return <p className="muted">Nobody enrolled in this period.</p>
  const sorted = [...rows].sort((a, b) => `${a.persona}${a.temperature}`.localeCompare(`${b.persona}${b.temperature}`))
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Track</th><th className="num">Enrolled</th><th className="num">In nurture</th><th className="num">Handed off</th><th className="num">Completed</th><th className="num">Exited</th></tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={`${r.persona}-${r.temperature}`}>
              <td className="nowrap">{NURTURE_PERSONAS[r.persona] || r.persona} · {TEMPERATURES[r.temperature] || r.temperature}</td>
              <td className="num">{formatNumber(r.enrolled)}</td>
              <td className="num">{formatNumber(r.live)}</td>
              <td className="num">{formatNumber(r.handed_off)}</td>
              <td className="num">{formatNumber(r.completed)}</td>
              <td className="num">{formatNumber(r.exited)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function NurtureDashboard() {
  const [days, setDays] = useState(90)
  const [custom, setCustom] = useState(null) // {start, end} as YYYY-MM-DD
  const [data, setData] = useState(null)
  const [{ busy, error }, load] = useAction(async () => {
    let start
    let end
    if (custom) {
      start = new Date(`${custom.start}T00:00:00`)
      end = new Date(`${custom.end}T00:00:00`)
      end.setDate(end.getDate() + 1)
    } else {
      end = new Date()
      end.setDate(end.getDate() + 1)
      start = new Date()
      start.setDate(start.getDate() - days)
    }
    const [stats, status] = await Promise.all([
      api.nurtureStats(start.toISOString(), end.toISOString()),
      api.nurtureStatus(),
    ])
    setData({ stats, status })
  })
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, custom])

  const k = data?.stats?.kpis
  const ai = data?.stats?.ai
  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Email Nurture</h1>
          <p className="muted">Six helpful emails over about 30 days for Warm and Cold leads, handed to sales the moment they are ready.</p>
        </div>
        <button className="btn" onClick={() => load()} disabled={busy}>{busy ? <Spinner /> : null} Refresh</button>
      </div>

      <NurtureStatus status={data?.status} />
      <ErrorNote error={error} onRetry={load} />

      <div className="range-bar">
        <div className="chips" role="tablist" aria-label="Date range">
          {RANGES.map(([n, label]) => (
            <button key={n} role="tab" aria-selected={!custom && days === n}
              className={`chip ${!custom && days === n ? 'is-active' : ''}`}
              onClick={() => { setCustom(null); setDays(n) }}>
              {label}
            </button>
          ))}
        </div>
        <form className="range-custom" onSubmit={(e) => {
          e.preventDefault()
          const f = new FormData(e.currentTarget)
          if (f.get('start') && f.get('end')) setCustom({ start: f.get('start'), end: f.get('end') })
        }}>
          <input type="date" name="start" aria-label="From" defaultValue={day(new Date(Date.now() - days * 864e5))} />
          <span className="muted">to</span>
          <input type="date" name="end" aria-label="To" defaultValue={day(new Date())} />
          <button className="btn btn-sm">Apply</button>
        </form>
      </div>

      <div className="stats">
        <StatTile label="In nurture now" value={k?.active} note={k && `${formatNumber(k.needs_review)} need review`} />
        <StatTile label="Handed off" value={k?.handed_off} note={k && `of ${formatNumber(k.enrolled)} enrolled`} />
        <StatTile label="Completed" value={k?.completed} note="all 6 emails" />
        <StatTile label="Unsubscribed / bounced" value={k?.unsubscribed_bounced} />
        <StatTile label="Nurture → demo" value={k ? pct(k.demo_rate, 1) : null}
          note={k && `${formatNumber(k.demos)} demo${k.demos === 1 ? '' : 's'} within 45 days of enrolling`} />
      </div>

      <div className="grid-2">
        <Card title="By persona and track" subtitle="Leads enrolled in this period.">
          {data ? <Breakdown rows={data.stats.breakdown || []} /> : <Spinner />}
        </Card>
        <Card title="Each email" subtitle="Clicks are people, not mail scanners. A low click rate marks the email to rewrite.">
          {data ? <StepTable steps={data.stats.steps || []} /> : <Spinner />}
        </Card>
      </div>

      <Card title="AI health" subtitle="How often the AI's drafts pass the checks and the reviewer.">
        <div className="stats stats-4">
          <StatTile label="Fallback rate" value={ai ? pct(ai.fallback_rate) : null}
            note={ai && `${formatNumber(ai.fallbacks)} of ${formatNumber(ai.written)} emails used the pre-approved text`} />
          <StatTile label="Average judge score" value={ai?.avg_judge_score == null ? (ai ? '—' : null) : Number(ai.avg_judge_score).toFixed(2)}
            note="0 to 1; drafts below the threshold are rewritten" />
          <StatTile label="Rewritten" value={ai ? pct(ai.regeneration_rate) : null} note="needed a second attempt" />
          <StatTile label="AI cost" value={ai ? `$${Number(ai.cost_usd || 0).toFixed(2)}` : null}
            note={ai && `${formatNumber(ai.tokens_in + ai.tokens_out)} tokens`} />
        </div>
      </Card>
    </div>
  )
}

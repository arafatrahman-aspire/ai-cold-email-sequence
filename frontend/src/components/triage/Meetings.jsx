import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { formatNumber, relativeTime, shortDateTime } from '../../labels.js'
import { StatTile } from '../Dashboard.jsx'
import { Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

function MeetingRow({ m }) {
  const upcoming = new Date(m.start_at) > new Date()
  return (
    <tr>
      <td>
        <strong>{shortDateTime(m.start_at)}</strong>
        <div className="muted">{relativeTime(m.start_at)}</div>
      </td>
      <td>
        {m.attendee_email}
        <div className="muted">{[m.job_title, m.company].filter(Boolean).join(' · ')}</div>
      </td>
      <td>
        {m.status === 'cancelled' ? <Badge tone="neutral">Cancelled</Badge>
          : upcoming ? <Badge tone="good">Upcoming</Badge> : <Badge tone="neutral">Past</Badge>}
      </td>
      <td>
        {m.meeting_url ? (
          <a href={m.meeting_url} target="_blank" rel="noreferrer">Join link ↗</a>
        ) : (
          <span className="muted">—</span>
        )}
        <div className="muted">via {m.provider}</div>
      </td>
    </tr>
  )
}

export function Meetings() {
  const [data, setData] = useState(null)
  const [{ busy, error }, load] = useAction(async () => {
    const [m, s] = await Promise.all([api.meetings(), api.triageStats()])
    setData({ meetings: m.meetings || [], stats: s })
  })
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const stats = data?.stats
  const rate = stats?.reply_to_meeting_rate
  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Meetings</h1>
          <p className="muted">Booked when an interested lead picks one of the offered times. The calendar sends the invite.</p>
        </div>
        <button className="btn" onClick={() => load()} disabled={busy}>
          {busy ? <Spinner /> : null} Refresh
        </button>
      </div>
      <ErrorNote error={error?.status === 0 ? null : error} onRetry={load} />
      <div className="stats stats-3">
        <StatTile label="Reply → meeting rate" value={stats ? (rate == null ? '—' : `${Math.round(rate * 100)}%`) : null}
          note={stats && `${formatNumber(stats.meeting_leads)} of ${formatNumber(stats.replied_leads)} who replied`} />
        <StatTile label="Upcoming" value={stats?.meetings_upcoming} />
        <StatTile label="Booked in total" value={data ? data.meetings.filter((m) => m.status === 'booked').length : null} />
      </div>
      {data && (
        <Card>
          {data.meetings.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>When (your time)</th><th>With</th><th>Status</th><th>Link</th></tr>
                </thead>
                <tbody>
                  {data.meetings.map((m) => <MeetingRow key={m.id} m={m} />)}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">No meetings booked yet.</p>
          )}
        </Card>
      )}
    </div>
  )
}

export function FollowUps() {
  const [rows, setRows] = useState(null)
  const [{ busy, error }, load] = useAction(async () => setRows((await api.snoozed()).snoozed || []))
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Follow-ups</h1>
          <p className="muted">
            Leads who said "not now". Each gets a fresh sequence automatically on the date shown, unless they have
            unsubscribed since.
          </p>
        </div>
        <button className="btn" onClick={() => load()} disabled={busy}>
          {busy ? <Spinner /> : null} Refresh
        </button>
      </div>
      <ErrorNote error={error?.status === 0 ? null : error} onRetry={load} />
      {rows && (
        <Card>
          {rows.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Lead</th><th>Comes back</th><th>What they said</th></tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.lead_id}>
                      <td>
                        {r.email}
                        <div className="muted">{[r.job_title, r.company].filter(Boolean).join(' · ')}</div>
                      </td>
                      <td>
                        <strong>{new Date(r.snoozed_until).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })}</strong>
                        <div className="muted">{relativeTime(r.snoozed_until)}</div>
                      </td>
                      <td className="muted">{(r.last_error || '').replace(/^not now:\s*/i, '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">Nobody is waiting to come back.</p>
          )}
        </Card>
      )}
    </div>
  )
}

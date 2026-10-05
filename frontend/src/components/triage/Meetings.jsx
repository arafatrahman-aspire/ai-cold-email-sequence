import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { formatNumber, relativeTime, shortDateTime } from '../../labels.js'
import { StatTile } from '../Dashboard.jsx'
import { Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

const SOURCE = {
  reply: 'From a reply',
  link: 'Through the booking link',
}

function MeetingStatus({ m }) {
  if (m.status === 'cancelled') return <Badge tone="neutral">Cancelled</Badge>
  if (new Date(m.start_at) <= new Date()) return <Badge tone="neutral">Past</Badge>
  if (m.status === 'pending') return <Badge tone="warning">Awaiting your confirmation</Badge>
  return <Badge tone="good">Upcoming</Badge>
}

function MeetingRow({ m }) {
  return (
    <tr>
      <td>
        <strong>{shortDateTime(m.start_at)}</strong>
        <div className="muted">{relativeTime(m.start_at)}</div>
      </td>
      <td>
        {m.attendee_name ? <strong>{m.attendee_name}</strong> : null}
        <div>{m.attendee_email || '—'}</div>
        <div className="muted">
          {m.lead_id
            ? [m.job_title, m.company].filter(Boolean).join(' · ') || 'Lead'
            : 'Not one of your leads'}
        </div>
      </td>
      <td>
        <MeetingStatus m={m} />
        <div className="muted">{SOURCE[m.source] || m.source}</div>
      </td>
      <td>
        {m.meeting_url && m.status !== 'cancelled' ? (
          <a href={m.meeting_url} target="_blank" rel="noreferrer">Join link ↗</a>
        ) : (
          <span className="muted">—</span>
        )}
        <div className="muted">via {m.provider}</div>
      </td>
    </tr>
  )
}

function SyncNote({ sync, calendar }) {
  if (calendar && !calendar.ready) {
    return (
      <div className="note note-warning">
        <Badge tone="warning">No calendar</Badge>
        <span>
          {calendar.error ||
            'CALENDAR_PROVIDER is "none": meetings cannot be booked or read. Set it to calcom in .env.'}
        </span>
      </div>
    )
  }
  if (!sync) return null
  if (sync.error) {
    return (
      <div className="note note-critical">
        <Badge tone="critical">Calendar check failed</Badge>
        <span>{sync.error}</span>
      </div>
    )
  }
  return (
    <p className="muted">
      Calendar checked {relativeTime(sync.at)}: {formatNumber(sync.seen)} booking{sync.seen === 1 ? '' : 's'} from
      yesterday on. Meetings booked through the link appear here within 5 minutes.
    </p>
  )
}

export function Meetings() {
  const [data, setData] = useState(null)
  const [{ busy, error }, load] = useAction(async () => {
    const [m, s, c] = await Promise.all([api.meetings(), api.triageStats(), api.calendarStatus().catch(() => null)])
    setData({ meetings: m.meetings || [], offers: m.open_offers || [], sync: m.sync, stats: s, calendar: c })
  })
  const [{ busy: syncing, error: syncError }, sync] = useAction(async () => {
    await api.syncCalendar()
    await load()
  })
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const stats = data?.stats
  const rate = stats?.reply_to_meeting_rate
  const active = data ? data.meetings.filter((m) => m.status !== 'cancelled') : []
  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Meetings</h1>
          <p className="muted">
            Booked when a lead picks one of the offered times, or books through the link themselves. The calendar
            sends the invite.
          </p>
        </div>
        <div className="toolbar-actions">
          <button className="btn" onClick={() => sync()} disabled={syncing || busy}>
            {syncing ? <Spinner /> : null} Check calendar now
          </button>
          <button className="btn btn-ghost" onClick={() => load()} disabled={busy}>
            {busy ? <Spinner /> : null} Refresh
          </button>
        </div>
      </div>
      <ErrorNote error={error?.status === 0 ? null : error} onRetry={load} />
      <ErrorNote error={syncError} />
      {data && <SyncNote sync={data.sync} calendar={data.calendar} />}
      <div className="stats stats-3">
        <StatTile label="Reply → meeting rate" value={stats ? (rate == null ? '—' : `${Math.round(rate * 100)}%`) : null}
          note={stats && `${formatNumber(stats.meeting_leads)} of ${formatNumber(stats.replied_leads)} who replied`} />
        <StatTile label="Upcoming" value={stats?.meetings_upcoming} />
        <StatTile label="Booked in total" value={data ? active.length : null}
          note={data && `${formatNumber(active.filter((m) => m.source === 'link').length)} through the link`} />
      </div>
      {data && (
        <Card title="Booked">
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
            <p className="muted">
              No meetings booked yet.
              {data.offers.length ? ' The leads below have been offered times and have not picked one.' : ''}
            </p>
          )}
        </Card>
      )}
      {data && data.offers.length > 0 && (
        <Card title="Waiting for them to pick a time"
          subtitle="Our last reply offered these times. Nothing is booked until they answer or use the link.">
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Lead</th><th>Times offered (your time)</th><th>Sent</th></tr>
              </thead>
              <tbody>
                {data.offers.map((o) => (
                  <tr key={o.lead_id}>
                    <td>
                      {o.email}
                      <div className="muted">{[o.job_title, o.company].filter(Boolean).join(' · ')}</div>
                    </td>
                    <td>
                      {(o.offered_slots || []).length
                        ? o.offered_slots.map((s) => <div key={s.start}>{shortDateTime(s.start)}</div>)
                        : <span className="muted">Asked for their times (no calendar slots)</span>}
                    </td>
                    <td className="muted">{relativeTime(o.sent_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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

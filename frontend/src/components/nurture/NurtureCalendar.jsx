import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { MESSAGE_STATUS, NURTURE_PERSONAS, TEMPERATURES } from '../../labels.js'
import { LeadLink } from '../LeadJourney.jsx'
import { Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

const DAY = 24 * 3600 * 1000

function mondayOf(d) {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7))
  return x
}

const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
const time = (d) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

// What goes out this week, to whom, which email. Emails not written yet are
// shown as planned: their date follows the cadence and can still move.
export default function NurtureCalendar() {
  const [weekStart, setWeekStart] = useState(() => mondayOf(new Date()))
  const [items, setItems] = useState(null)
  const [{ busy, error }, load] = useAction(async () => {
    const end = new Date(weekStart.getTime() + 7 * DAY)
    setItems((await api.nurtureSchedule(weekStart.toISOString(), end.toISOString())).items || [])
  })
  useEffect(() => {
    setItems(null)
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekStart])

  const days = [...Array(7)].map((_, i) => new Date(weekStart.getTime() + i * DAY))
  const byDay = {}
  for (const it of items || []) (byDay[dayKey(new Date(it.at))] ||= []).push(it)
  const today = dayKey(new Date())
  const shift = (n) => setWeekStart(new Date(weekStart.getTime() + n * 7 * DAY))
  const total = items?.length || 0

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Calendar</h1>
          <p className="muted">
            Every nurture email due this week, in your time. <strong>Planned</strong> ones are not written yet (they are
            written about a day ahead); a reply, a click or the daily limit can still move them.
          </p>
        </div>
        <div className="toolbar-actions">
          <button className="btn" onClick={() => shift(-1)} aria-label="Previous week">←</button>
          <button className="btn" onClick={() => setWeekStart(mondayOf(new Date()))}>This week</button>
          <button className="btn" onClick={() => shift(1)} aria-label="Next week">→</button>
        </div>
      </div>
      <ErrorNote error={error} onRetry={load} />
      <Card
        title={`${days[0].toLocaleDateString([], { day: 'numeric', month: 'short' })} – ${days[6].toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })}`}
        subtitle={items ? `${total} email${total === 1 ? '' : 's'}` : ' '}
      >
        {!items && busy ? <Spinner label="Loading the week…" /> : (
          <div className="cal-week">
            {days.map((d) => {
              const list = byDay[dayKey(d)] || []
              return (
                <div key={dayKey(d)} className={`cal-day ${dayKey(d) === today ? 'is-today' : ''}`}>
                  <div className="cal-day-head">
                    <strong>{d.toLocaleDateString([], { weekday: 'short' })}</strong>
                    <span className="muted">{d.getDate()}</span>
                  </div>
                  {list.length === 0 && <span className="muted cal-empty">—</span>}
                  {list.map((it) => {
                    const [tone, label] = it.planned ? ['neutral', 'Planned'] : MESSAGE_STATUS[it.status] || ['neutral', it.status]
                    const name = [it.first_name, it.last_name].filter(Boolean).join(' ') || it.email
                    return (
                      <div key={`${it.enrollment_id}-${it.step}`} className={`cal-item ${it.planned ? 'is-planned' : ''}`}>
                        <div className="cal-item-head">
                          <span>{time(new Date(it.at))}</span>
                          <Badge tone={tone}>{label}</Badge>
                        </div>
                        <strong><LeadLink leadId={it.lead_id}>{name}</LeadLink></strong>
                        <span className="muted">
                          Email {it.step} of 6 · {NURTURE_PERSONAS[it.persona]} · {TEMPERATURES[it.temperature]}
                        </span>
                        {it.subject && <span className="cal-subject">{it.subject}</span>}
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        )}
      </Card>
    </div>
  )
}

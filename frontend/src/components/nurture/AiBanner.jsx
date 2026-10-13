import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { Badge } from '../ui.jsx'

// Quality dropping quietly is worse than an error: say plainly when emails
// used the standard text instead of an AI draft in the last 24 hours.
export default function AiBanner() {
  const [ai, setAi] = useState(null)
  useEffect(() => {
    const end = new Date()
    const start = new Date(end.getTime() - 24 * 3600 * 1000)
    api.nurtureStats(start.toISOString(), end.toISOString())
      .then((s) => setAi(s.ai || null))
      .catch(() => setAi(null)) // the banner is a hint; the page works without it
  }, [])
  if (!ai || !ai.fallbacks) return null
  const rate = ai.written ? ai.fallbacks / ai.written : 1
  const serious = rate >= 0.3
  return (
    <div className={`note ${serious ? 'note-critical' : 'note-warning'}`} role="status">
      <Badge tone={serious ? 'critical' : 'warning'}>{serious ? 'AI unavailable' : 'Standard text used'}</Badge>
      <span>
        {ai.fallbacks} of {ai.written} nurture email{ai.written === 1 ? '' : 's'} written in the last 24 hours used the
        pre-approved standard text instead of an AI draft
        {serious ? ': the AI is probably down or out of quota, so ask an admin to check it. ' : '. '}
        Standard text is pre-approved and sends on schedule; open Review to adjust any of them first.
      </span>
    </div>
  )
}

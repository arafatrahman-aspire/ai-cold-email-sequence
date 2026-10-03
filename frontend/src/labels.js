// Human wording for the backend's status values, in lifecycle order.

export const ENROLLMENT_ACTIVE = [
  ['ready_for_outreach', 'Waiting to draft'],
  ['processing', 'Drafting'],
  ['sequence_ready', 'Scheduled'],
  ['sending', 'Sending'],
  ['sent', 'Sequence finished'],
]

export const ENROLLMENT_OUTCOMES = [
  ['replied', 'Replied'],
  ['bounced', 'Bounced'],
  ['unsubscribed', 'Unsubscribed'],
  ['stopped', 'Stopped (lead status)'],
  ['manual_review', 'Needs review'],
  ['failed', 'Failed'],
]

export const EMAIL_STATUSES = [
  ['pending', 'Scheduled'],
  ['sending', 'Sending now'],
  ['sent', 'Sent'],
  ['cancelled', 'Cancelled'],
  ['failed', 'Failed'],
]

export const PERSONAS = {
  ciso: 'Security & risk',
  it: 'IT & infrastructure',
  hr: 'HR & people',
}

// Enrollment outcome -> [tone, label] for result badges.
export function enrollOutcome(outcome) {
  if (outcome === 'enrolled') return ['good', 'Enrolled']
  if (outcome === 'already_enrolled') return ['neutral', 'Already enrolled']
  if (outcome === 'not_found') return ['critical', 'Lead not found']
  if (outcome === 'no_email') return ['warning', 'No email address']
  if (outcome === 'suppressed') return ['warning', 'On suppression list']
  if (outcome === 'in_email_nurture') return ['warning', 'In another email journey']
  if (outcome?.startsWith('not_contactable')) {
    const status = outcome.match(/\((.*)\)/)?.[1]
    return ['warning', `Not contactable${status ? ` (${status})` : ''}`]
  }
  return ['neutral', outcome || 'Unknown']
}

export const formatNumber = (n) => new Intl.NumberFormat().format(n ?? 0)

// Reply triage categories -> [badge tone, label]. Tone is backed by an icon
// and the label, never colour alone.
export const CATEGORIES = {
  interested: ['good', 'Interested'],
  not_now: ['neutral', 'Not now'],
  wrong_person: ['neutral', 'Wrong person'],
  objection: ['warning', 'Objection'],
  out_of_office: ['neutral', 'Out of office'],
  unsubscribe: ['critical', 'Unsubscribe'],
  other: ['neutral', 'Unclear'],
}
export const CATEGORY_ORDER = ['interested', 'not_now', 'wrong_person', 'objection', 'out_of_office', 'unsubscribe', 'other']

export const DRAFT_KINDS = {
  meeting_offer: 'Meeting times',
  booking_confirmation: 'Booking confirmation',
  slot_unavailable: 'New times (slot taken)',
  objection_reply: 'Objection reply',
  not_now_ack: '"Not now" reply',
  referral_ack: 'Referral thanks',
  referral_ask: 'Ask for right contact',
}

export const TRIAGE_STATUS = {
  pending: ['neutral', 'Waiting for triage'],
  processing: ['neutral', 'Waiting for AI'],
  done: ['good', 'Handled'],
  needs_human: ['warning', 'Needs you'],
  failed: ['critical', 'Failed'],
  skipped: ['neutral', 'Not triaged'],
}

export function relativeTime(date, now = Date.now()) {
  const mins = Math.round((new Date(date) - now) / 60000)
  const abs = Math.abs(mins)
  const span = abs < 60 ? `${abs} min` : abs < 48 * 60 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} days`
  return mins >= 0 ? `in ${span}` : `${span} ago`
}

export const shortDateTime = (d) =>
  new Date(d).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

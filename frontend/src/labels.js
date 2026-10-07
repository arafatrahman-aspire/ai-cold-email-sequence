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
  ['stopped', 'Stopped'],
  ['manual_review', 'Needs review'],
  ['failed', 'Failed'],
]

// Every enrollment status -> [tone, label], for one lead's badge.
export const ENROLLMENT_BADGE = {
  ready_for_outreach: ['neutral', 'Waiting to draft'],
  processing: ['neutral', 'Drafting'],
  sequence_ready: ['good', 'In sequence'],
  sending: ['good', 'In sequence'],
  sent: ['neutral', 'Sequence finished'],
  replied: ['good', 'Replied'],
  meeting_booked: ['good', 'Meeting booked'],
  snoozed: ['neutral', 'Snoozed'],
  bounced: ['warning', 'Bounced'],
  unsubscribed: ['warning', 'Unsubscribed'],
  stopped: ['neutral', 'Stopped'],
  manual_review: ['warning', 'Needs review'],
  failed: ['critical', 'Failed'],
}

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
  if (outcome === 'email_already_enrolled') return ['neutral', 'Same email already enrolled']
  if (outcome === 'in_nurture') return ['neutral', 'In Email Nurture (one sequence at a time)']
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

// --- Email Nurture -------------------------------------------------------------

export const NURTURE_PERSONAS = { ciso: 'CISO', it: 'IT Manager', hr: 'HR & Compliance' }
export const TEMPERATURES = { warm: 'Warm', cold: 'Cold' }

export const NURTURE_STATUS = {
  active: ['good', 'Active'],
  paused: ['neutral', 'Paused'],
  held: ['warning', 'Held'],
  handed_off: ['good', 'Handed off'],
  completed: ['neutral', 'Completed'],
  exited: ['neutral', 'Exited'],
}

export const EXIT_REASONS = {
  hot_score: 'Score became Hot',
  pricing_click: 'Clicked pricing',
  demo_click: 'Clicked demo',
  reply_interested: 'Replied with interest',
  manual: 'Handed off by a person',
  completed: 'All 6 emails sent',
  unsubscribed: 'Unsubscribed',
  bounced: 'Bounced',
  complaint: 'Spam complaint',
  not_interested: 'Not interested',
  removed: 'Removed by a person',
  suppressed: 'On the suppression list',
  lead_status: 'Lead status changed',
  customer: 'Became a customer',
}

export const MESSAGE_STATUS = {
  writing: ['neutral', 'Writing'],
  needs_approval: ['warning', 'Needs approval'],
  ready: ['neutral', 'Scheduled'],
  rejected: ['neutral', 'Rejected'],
  sending: ['neutral', 'Sending'],
  sent: ['good', 'Sent'],
  failed: ['critical', 'Failed'],
  cancelled: ['neutral', 'Cancelled'],
}

export const NURTURE_OUTCOMES = {
  enrolled: 'Enrolled',
  in_nurture: 'Already in nurture',
  in_cold_sequence: 'In the cold sequence',
  cooldown: 'Finished nurture recently (cooldown)',
  suppressed: 'Unsubscribed or bounced',
  customer: 'Already a customer',
  no_email: 'No email address',
  not_found: 'Lead not found',
  in_email_nurture: 'In another email journey',
  test_mode_not_allowed: 'Not on the test allow-list',
}
export const nurtureOutcome = (o) =>
  NURTURE_OUTCOMES[o] || (o?.startsWith('lead_status') ? `Lead status ${o.replace('lead_status ', '')}` : o)

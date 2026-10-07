"""Email Nurture (third module): 6 AI-written emails over ~30 days for Warm
and Cold leads, with a hand-off to sales the moment a lead is ready.

  options     settings with defaults (cold_email.settings key "nurture")
  persona     CISO / IT Manager / HR & Compliance routing
  cadence     send dates (business hours, or minutes in test mode)
  enroll      score changes -> enrollments, reconciliation, manual enroll
  safety      cleaning untrusted lead data before it reaches a prompt
  writer      AI generation, checks, judge, fallback
  render      placeholders -> tracked links, text and HTML layout
  sending     the nurture mail account and the send loop
  exits       hand-off to sales, unsubscribe, bounce, completion
  replies     poller and Reply Triage integration
  api         console and public (click / unsubscribe) endpoints

Nothing here changes how the cold sequence or Reply Triage behave for their
own leads; the integration points are listed in those modules.
"""

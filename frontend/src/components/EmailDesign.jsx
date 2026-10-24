import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Toggle } from './settingsControls.jsx'
import { EmailFrame } from './EmailPreview.jsx'
import { Card, ErrorNote, Field, Spinner, useAction } from './ui.jsx'

const KINDS = [
  ['cold', 'Cold email'],
  ['reply', 'Answer to a reply'],
  ['nurture', 'Nurture email'],
  ['handoff', 'Hand-off to sales'],
  ['alert', 'New-reply alert'],
]

const WHY = {
  cold: 'Plain: a personal email with a proper signature, no banner or images. Card: the same text in a white card with your company name above it. Heavily designed cold email lands in Promotions and gets fewer replies, so neither has images or buttons.',
  reply: 'Same personal look, without the opt-out line (they wrote to us).',
  nurture: 'The branded layout: logo, card, one clear button, address and unsubscribe in the footer.',
  handoff: 'What sales receives: why the lead is ready, the facts, and a button to the lead.',
  alert: 'What the team receives when a lead replies.',
}

// The look of every email: the signature and the colour are set once here.
export default function EmailDesign() {
  const [style, setStyle] = useState(null)
  const [saved, setSaved] = useState(null)
  const [kind, setKind] = useState('cold')
  const [{ error: loadError }, loadStyle] = useAction(async () => {
    const s = (await api.emailDesign()).style
    setStyle(s)
    setSaved(s)
  })
  const [{ busy: rendering, error: renderError, result: email }, render] = useAction(() => api.renderEmail({ kind }))
  const [{ busy: saving, error: saveError }, save] = useAction(async () => {
    const s = (await api.saveEmailDesign(style)).style
    setStyle(s)
    setSaved(s)
    await render()
  })
  useEffect(() => { loadStyle() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { render() }, [kind]) // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = JSON.stringify(style) !== JSON.stringify(saved)
  const set = (k) => (e) => setStyle({ ...style, [k]: e.target.value })

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Email design</h1>
          <p className="muted">
            How every email looks: cold emails, answers to replies, nurture emails and the team's notifications.
            Every email goes out as designed HTML plus a plain-text version.
          </p>
        </div>
      </div>
      <ErrorNote error={loadError} onRetry={loadStyle} />

      <div className="design-grid">
        <Card title="Signature and look" subtitle="Used by every email. The sender's name is the inbox's display name unless you set one here.">
          {!style ? <Spinner /> : (
            <div className="campaign-form">
              <div className="campaign-row">
                <Field label="Name" hint="Empty: the sending inbox's name"><input value={style.name} onChange={set('name')} /></Field>
                <Field label="Job title"><input value={style.title} onChange={set('title')} placeholder="Account Executive" /></Field>
                <Field label="Company"><input value={style.company} onChange={set('company')} /></Field>
              </div>
              <div className="campaign-row">
                <Field label="Website"><input value={style.website} onChange={set('website')} placeholder="aspiretss.com" /></Field>
                <Field label="Phone"><input value={style.phone} onChange={set('phone')} /></Field>
                <Field label="Brand colour">
                  <span className="color-field">
                    <input type="color" value={style.accent} onChange={set('accent')} aria-label="Brand colour" />
                    <input value={style.accent} onChange={set('accent')} maxLength={7} />
                  </span>
                </Field>
              </div>
              <Field label="Company address" hint="Shown in the nurture footer (required for marketing email in many countries)">
                <input value={style.address} onChange={set('address')} />
              </Field>
              <div className="campaign-row">
                <Field label="Layout (cold emails and answers)" hint="Plain: a bare personal email. Card: the same text in a white card with your company name above it.">
                  <select value={style.layout || 'plain'} onChange={set('layout')}>
                    <option value="plain">Plain, like a personal email</option>
                    <option value="card">Card, more designed</option>
                  </select>
                </Field>
                <Field label="Sign-off (cold emails)"><input value={style.sign_off} onChange={set('sign_off')} placeholder="Best," /></Field>
              </div>
              <Field label="Opt-out line (cold emails)" hint="One quiet line under the signature. Empty: none (the unsubscribe header is always sent).">
                <input value={style.opt_out} onChange={set('opt_out')} />
              </Field>
              <div className="campaign-toggle">
                <Toggle label="Send designed HTML" checked={style.html} onChange={(v) => setStyle({ ...style, html: v })} />
                <span>Send cold emails and answers as designed HTML (off: plain text only)</span>
              </div>
              <ErrorNote error={saveError} />
              <div className="form-actions">
                {dirty && <button className="btn btn-ghost" onClick={() => setStyle(saved)} disabled={saving}>Undo</button>}
                <button className="btn btn-primary" onClick={() => save()} disabled={saving || !dirty || !/^#[0-9a-fA-F]{6}$/.test(style.accent)}>
                  {saving ? <Spinner /> : null} Save
                </button>
              </div>
            </div>
          )}
        </Card>

        <Card title="Preview" subtitle={WHY[kind]}>
          <div className="chips" role="tablist" aria-label="Which email">
            {KINDS.map(([k, l]) => (
              <button key={k} role="tab" aria-selected={kind === k} className={`chip chip-sm ${kind === k ? 'is-active' : ''}`} onClick={() => setKind(k)}>{l}</button>
            ))}
          </div>
          {dirty && <p className="muted">Save to see your changes in the preview.</p>}
          <ErrorNote error={renderError} onRetry={render} />
          {rendering && !email ? <Spinner label="Rendering…" /> : <EmailFrame email={email} />}
        </Card>
      </div>
    </div>
  )
}

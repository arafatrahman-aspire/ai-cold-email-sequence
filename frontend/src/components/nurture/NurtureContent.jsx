import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { NURTURE_PERSONAS, TEMPERATURES } from '../../labels.js'
import { CheckList } from '../settingsControls.jsx'
import { AutoTextarea, Badge, Card, ErrorNote, Field, Spinner, useAction } from '../ui.jsx'

const STAGES = ['awareness', 'consideration', 'decision']
const KINDS = ['guide', 'case_study', 'checklist', 'report', 'webinar', 'blog', 'roi']
const CTA = { soft: 'Soft (resource first)', demo: 'Demo', pricing: 'Pricing' }
const SECTIONS = [['briefs', 'Step briefs'], ['fallbacks', 'Fallback emails'], ['resources', 'Resource library']]

// Like Field, but not a <label>: for groups of checkboxes.
function Group({ label, hint, children }) {
  return (
    <div className="field" role="group" aria-label={label}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  )
}

function Picker({ value, options, onChange, label }) {
  return (
    <div className="chips" role="tablist" aria-label={label}>
      {Object.entries(options).map(([k, l]) => (
        <button key={k} role="tab" aria-selected={value === k} className={`chip chip-sm ${value === k ? 'is-active' : ''}`}
          onClick={() => onChange(k)}>{l}</button>
      ))}
    </div>
  )
}

function BriefCard({ brief, onSaved }) {
  const [d, setD] = useState(brief)
  useEffect(() => setD(brief), [brief])
  const dirty = JSON.stringify(d) !== JSON.stringify(brief)
  const [{ busy, error, result }, save] = useAction(async () => {
    const r = await api.nurtureSaveBrief({
      persona: d.persona, temperature: d.temperature, step: d.step, goal: d.goal, angle: d.angle,
      allowed_stages: d.allowed_stages, cta_emphasis: d.cta_emphasis, max_words: Number(d.max_words),
    })
    onSaved()
    return r
  })
  const set = (patch) => setD((x) => ({ ...x, ...patch }))
  return (
    <div className="content-card">
      <div className="content-head">
        <strong>Email {d.step}</strong>
        <span className="muted">version {brief.version}{brief.updated_by && brief.updated_by !== 'seed' ? ` · ${brief.updated_by}` : ''}</span>
      </div>
      <Field label="Goal"><AutoTextarea minRows={2} value={d.goal} onChange={(e) => set({ goal: e.target.value })} /></Field>
      <Field label="Angle"><AutoTextarea minRows={2} value={d.angle} onChange={(e) => set({ angle: e.target.value })} /></Field>
      <div className="content-row">
        <Group label="Resources from stages">
          <CheckList options={STAGES} value={d.allowed_stages} onChange={(v) => set({ allowed_stages: v })} />
        </Group>
        <Field label="Call to action">
          <select value={d.cta_emphasis} onChange={(e) => set({ cta_emphasis: e.target.value })}>
            {Object.entries(CTA).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Field>
        <Field label="Max words">
          <input type="number" min={40} max={400} value={d.max_words} onChange={(e) => set({ max_words: e.target.value })} style={{ width: 90 }} />
        </Field>
      </div>
      <ErrorNote error={error} />
      {(dirty || result) && (
        <div className="content-actions">
          {result && !dirty && <Badge tone="good">Saved as version {result.version}</Badge>}
          {dirty && <button className="btn btn-ghost btn-sm" onClick={() => setD(brief)}>Discard</button>}
          {dirty && <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => save()}>{busy ? <Spinner /> : null} Save</button>}
        </div>
      )}
    </div>
  )
}

function Briefs() {
  const [persona, setPersona] = useState('ciso')
  const [temp, setTemp] = useState('cold')
  const [briefs, setBriefs] = useState(null)
  const [{ error }, load] = useAction(async () => setBriefs((await api.nurtureBriefs()).briefs || []))
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const shown = (briefs || []).filter((b) => b.persona === persona && b.temperature === temp)
  return (
    <Card title="Step briefs" subtitle="What each email should do, per persona and track. The AI writes every email from its brief; a saved change applies to emails written from then on.">
      <div className="content-pickers">
        <Picker label="Persona" value={persona} options={NURTURE_PERSONAS} onChange={setPersona} />
        <Picker label="Track" value={temp} options={TEMPERATURES} onChange={setTemp} />
      </div>
      <ErrorNote error={error} onRetry={load} />
      {!briefs ? <Spinner /> : shown.length ? (
        <div className="content-grid">{shown.map((b) => <BriefCard key={`${b.persona}-${b.temperature}-${b.step}`} brief={b} onSaved={load} />)}</div>
      ) : null}
    </Card>
  )
}

function FallbackCard({ fb, onSaved }) {
  const [d, setD] = useState(fb)
  useEffect(() => setD(fb), [fb])
  const dirty = d.subject !== fb.subject || d.preheader !== fb.preheader || d.body !== fb.body
  const [{ busy, error, result }, save] = useAction(async () => {
    const r = await api.nurtureSaveFallback({ persona: d.persona, step: d.step, subject: d.subject, preheader: d.preheader, body: d.body })
    onSaved()
    return r
  })
  const set = (patch) => setD((x) => ({ ...x, ...patch }))
  return (
    <div className="content-card">
      <div className="content-head"><strong>Email {d.step}</strong><span className="muted">version {fb.version}</span></div>
      <Field label="Subject"><input value={d.subject} onChange={(e) => set({ subject: e.target.value })} /></Field>
      <Field label="Preview text"><input value={d.preheader} onChange={(e) => set({ preheader: e.target.value })} /></Field>
      <Field label="Body"><AutoTextarea minRows={6} value={d.body} onChange={(e) => set({ body: e.target.value })} /></Field>
      <ErrorNote error={error} />
      {(dirty || result) && (
        <div className="content-actions">
          {result && !dirty && <Badge tone="good">Saved as version {result.version}</Badge>}
          {dirty && <button className="btn btn-ghost btn-sm" onClick={() => setD(fb)}>Discard</button>}
          {dirty && <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => save()}>{busy ? <Spinner /> : null} Save</button>}
        </div>
      )}
    </div>
  )
}

function Fallbacks() {
  const [persona, setPersona] = useState('ciso')
  const [rows, setRows] = useState(null)
  const [{ error }, load] = useAction(async () => setRows((await api.nurtureFallbacks()).fallbacks || []))
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <Card title="Fallback emails" subtitle="Pre-approved text used when the AI's draft fails its checks twice, so the email still goes out on time. Must include {{CTA_DEMO}} and {{CTA_PRICING}}; no web addresses.">
      <div className="content-pickers"><Picker label="Persona" value={persona} options={NURTURE_PERSONAS} onChange={setPersona} /></div>
      <ErrorNote error={error} onRetry={load} />
      {!rows ? <Spinner /> : (
        <div className="content-grid">
          {rows.filter((f) => f.persona === persona).map((f) => <FallbackCard key={`${f.persona}-${f.step}`} fb={f} onSaved={load} />)}
        </div>
      )}
    </Card>
  )
}

const emptyResource = { title: '', url: 'https://', description: '', kind: 'guide', personas: [], stages: [], approved: false }

function ResourceForm({ initial, onSave, onCancel, busy, error }) {
  const [r, setR] = useState(initial)
  const set = (patch) => setR((x) => ({ ...x, ...patch }))
  return (
    <form className="content-card" onSubmit={(e) => { e.preventDefault(); onSave(r) }}>
      <div className="content-row">
        <Field label="Title"><input required value={r.title} onChange={(e) => set({ title: e.target.value })} /></Field>
        <Field label="Link"><input required type="url" value={r.url} onChange={(e) => set({ url: e.target.value })} /></Field>
        <Field label="Kind">
          <select value={r.kind} onChange={(e) => set({ kind: e.target.value })}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
        </Field>
      </div>
      <Field label="What it says" hint="The AI may only claim what this describes, so be accurate.">
        <AutoTextarea minRows={2} value={r.description} onChange={(e) => set({ description: e.target.value })} />
      </Field>
      <div className="content-row">
        <Group label="For personas" hint="None ticked = every persona">
          <div className="checklist">
            {Object.entries(NURTURE_PERSONAS).map(([k, l]) => (
              <label key={k} className={`check-pill ${r.personas.includes(k) ? 'is-on' : ''}`}>
                <input type="checkbox" checked={r.personas.includes(k)}
                  onChange={(e) => set({ personas: e.target.checked ? [...r.personas, k] : r.personas.filter((x) => x !== k) })} />
                {l}
              </label>
            ))}
          </div>
        </Group>
        <Group label="Stages"><CheckList options={STAGES} value={r.stages} onChange={(v) => set({ stages: v })} /></Group>
        <Group label="Approved" hint="Only approved resources are offered to the AI">
          <label className="check-pill-inline"><input type="checkbox" checked={r.approved} onChange={(e) => set({ approved: e.target.checked })} /> Approved</label>
        </Group>
      </div>
      <ErrorNote error={error} />
      <div className="content-actions">
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        <button className="btn btn-primary btn-sm" disabled={busy}>{busy ? <Spinner /> : null} Save</button>
      </div>
    </form>
  )
}

function Resources() {
  const [rows, setRows] = useState(null)
  const [editing, setEditing] = useState(null) // 'new' | id
  const [{ error }, load] = useAction(async () => setRows((await api.nurtureResources()).resources || []))
  const [{ busy, error: saveError }, save] = useAction(async (r) => {
    const body = { title: r.title, url: r.url, description: r.description, kind: r.kind, personas: r.personas, stages: r.stages, approved: r.approved }
    if (editing === 'new') await api.nurtureAddResource(body)
    else await api.nurtureEditResource(editing, body)
    setEditing(null)
    await load()
  })
  const [{ busy: removing }, remove] = useAction(async (r) => {
    if (!window.confirm(`Remove "${r.title}" from the library? Emails already sent keep their links.`)) return
    await api.nurtureDeleteResource(r.id)
    await load()
  })
  const [{ busy: tagging, error: tagError, result: tagged }, autoTag] = useAction(async () => {
    const r = await api.nurtureAutoTag()
    await load()
    return r
  })
  const [{ busy: accepting }, accept] = useAction(async (r) => {
    const s = r.suggestion
    await api.nurtureEditResource(r.id, { title: r.title, url: r.url, description: r.description, kind: s.kind || r.kind,
      personas: s.personas, stages: s.stages, approved: true })
    await load()
  })
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <Card title="Resource library" subtitle="Guides, case studies and other content the emails may link to. The AI picks from approved resources that match the lead's persona and the step's stages, and never repeats one."
      actions={
        <div className="toolbar-actions">
          <button className="btn btn-sm" disabled={tagging} onClick={() => autoTag()}
            title="The AI suggests personas and stages for resources not approved yet; you accept each suggestion.">
            {tagging ? <Spinner /> : null} Suggest tags with AI
          </button>
          <button className="btn btn-primary btn-sm" onClick={() => setEditing('new')}>Add resource</button>
        </div>
      }>
      <ErrorNote error={error || tagError} onRetry={load} />
      {tagged && <p className="muted">Suggested tags for {tagged.suggested} resource(s).{tagged.errors?.length ? ` ${tagged.errors.join('; ')}` : ''}</p>}
      {editing === 'new' && <ResourceForm initial={emptyResource} onSave={save} onCancel={() => setEditing(null)} busy={busy} error={saveError} />}
      {!rows ? <Spinner /> : rows.length ? (
        <ul className="replies">
          {rows.map((r) => editing === r.id ? (
            <li key={r.id}><ResourceForm initial={r} onSave={save} onCancel={() => setEditing(null)} busy={busy} error={saveError} /></li>
          ) : (
            <li key={r.id} className="reply">
              <div className="reply-head">
                <div className="reply-who">
                  <strong>{r.title}</strong>
                  <a className="muted" href={r.url} target="_blank" rel="noreferrer">{r.url}</a>
                </div>
                <div className="reply-meta">
                  <Badge tone={r.approved ? 'good' : 'warning'}>{r.approved ? 'Approved' : 'Not approved'}</Badge>
                  <span className="muted">
                    {r.kind} · {r.personas.length ? r.personas.map((p) => NURTURE_PERSONAS[p]).join(', ') : 'every persona'} · {r.stages.join(', ') || 'no stage'}
                  </span>
                </div>
              </div>
              {r.description && <p className="muted">{r.description}</p>}
              {r.suggestion && (
                <div className="note note-info">
                  <Badge tone="info">AI suggests</Badge>
                  <span>
                    {(r.suggestion.personas || []).map((p) => NURTURE_PERSONAS[p]).join(', ') || 'every persona'} ·{' '}
                    {(r.suggestion.stages || []).join(', ')} · {r.suggestion.kind}
                    {r.suggestion.reason && <> ({r.suggestion.reason})</>}
                  </span>
                  <button className="btn btn-sm" disabled={accepting} onClick={() => accept(r)}>Accept and approve</button>
                </div>
              )}
              <div className="content-actions">
                <button className="btn btn-ghost btn-sm" onClick={() => setEditing(r.id)}>Edit</button>
                <button className="btn btn-ghost btn-sm btn-remove" disabled={removing} onClick={() => remove(r)}>Remove</button>
              </div>
            </li>
          ))}
        </ul>
      ) : <p className="muted">No resources yet. Without any, emails link only to the demo and pricing pages.</p>}
    </Card>
  )
}

export default function NurtureContent() {
  const [section, setSection] = useState('briefs')
  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Content</h1>
          <p className="muted">Everything the AI writes from, and the safety net when it fails. Changes are versioned and logged with each email.</p>
        </div>
      </div>
      <div className="chips" role="tablist" aria-label="Content">
        {SECTIONS.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={section === k} className={`chip ${section === k ? 'is-active' : ''}`} onClick={() => setSection(k)}>{l}</button>
        ))}
      </div>
      {section === 'briefs' && <Briefs />}
      {section === 'fallbacks' && <Fallbacks />}
      {section === 'resources' && <Resources />}
    </div>
  )
}

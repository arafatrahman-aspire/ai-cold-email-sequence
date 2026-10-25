import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { AutoTextarea, Badge, Card, ErrorNote, Spinner, useAction, toast } from '../ui.jsx'
import { forgetSnippets } from './Drafts.jsx'

const EXAMPLES = [
  ['Pricing question', 'Pricing depends on the number of employees and the modules you need. I can send a short quote once I know your headcount.'],
  ['"Send me more info"', 'Happy to. Here is a two-page overview of how we run phishing simulations and training: <link>. A 20-minute call is the quickest way to see it on your own data.'],
]

// Approved wording the AI reuses when a reply asks the same thing, and that
// people can insert while editing an answer.
export default function AnswerLibrary() {
  const [saved, setSaved] = useState(null)
  const [items, setItems] = useState([])
  const [{ busy, error }, load] = useAction(async () => {
    const r = await api.snippets()
    setSaved(r.snippets || [])
    setItems(r.snippets || [])
  })
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const [{ busy: saving, error: saveError }, save] = useAction(async () => {
    const r = await api.saveSnippets(items.map(({ id, title, text }) => ({ id, title: title.trim(), text: text.trim() })))
    setSaved(r.snippets)
    setItems(r.snippets)
    forgetSnippets()
    toast.success('Answer library saved')
    return true
  })

  const dirty = JSON.stringify(items) !== JSON.stringify(saved)
  const valid = items.every((s) => s.title.trim() && s.text.trim())
  const update = (i, key, value) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, [key]: value } : x)))

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Answer library</h1>
          <p className="muted">
            Approved answers to common questions. When a reply asks the same thing, the AI uses this wording; you can
            also insert one while editing an answer in Drafts or on a reply card. The AI also learns from answers you
            edit before sending.
          </p>
        </div>
      </div>
      <ErrorNote error={error} onRetry={load} />
      {!saved && busy && <Spinner label="Loading…" />}

      {saved && (
        <Card>
          {items.length === 0 && (
            <p className="muted">No answers yet. Add the questions prospects ask most: pricing, a security questionnaire, "send me info".</p>
          )}
          <ul className="replies">
            {items.map((s, i) => (
              <li key={s.id || `new-${i}`} className="draft">
                <div className="nurture-edit">
                  <input aria-label="Question or topic" placeholder="Question or topic, e.g. Pricing" value={s.title}
                    maxLength={100} onChange={(e) => update(i, 'title', e.target.value)} />
                  <AutoTextarea aria-label="Approved answer" minRows={3} maxLength={2000} value={s.text}
                    placeholder="The answer, as you would write it" onChange={(e) => update(i, 'text', e.target.value)} />
                </div>
                <div className="reply-actions">
                  <span className="muted">Do not include meeting times; the calendar offers those.</span>
                  <button className="btn btn-ghost btn-sm btn-remove" onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <div className="form-actions">
            <button className="btn btn-ghost" disabled={items.length >= 50}
              onClick={() => setItems((xs) => [...xs, { id: null, title: '', text: '' }])}>
              + Add an answer
            </button>
            {items.length === 0 && (
              <button className="btn btn-ghost" onClick={() => setItems(EXAMPLES.map(([title, text]) => ({ id: null, title, text })))}>
                Start from two examples
              </button>
            )}
            {dirty && <button className="btn btn-ghost" onClick={() => setItems(saved)}>Undo changes</button>}
            <button className="btn btn-primary" disabled={!dirty || !valid || saving} onClick={() => save()}>
              {saving ? <Spinner /> : null} Save library
            </button>
          </div>
          {dirty && !valid && <p className="field-error">Every answer needs a topic and text.</p>}
          {!dirty && saved.length > 0 && <p className="muted"><Badge tone="good">Saved</Badge> The AI uses these from the next reply.</p>}
          <ErrorNote error={saveError} />
        </Card>
      )}
    </div>
  )
}

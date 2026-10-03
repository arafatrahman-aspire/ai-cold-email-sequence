import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { CATEGORIES, CATEGORY_ORDER, relativeTime } from '../../labels.js'
import { BarList, StatTile } from '../Dashboard.jsx'
import { Badge, Card, ErrorNote, Spinner, useAction } from '../ui.jsx'

export default function Accuracy() {
  const [report, setReport] = useState(null)
  const [{ error: loadError }, load] = useAction(async () => setReport((await api.evaluation()).report || null))
  const [{ busy, error }, run] = useAction(async () => {
    const r = await api.evaluate()
    setReport(r)
  })
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const correct = Object.fromEntries(
    Object.entries(report?.by_category || {}).map(([k, v]) => [k, v.correct]),
  )
  const ex = report?.extraction

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>Accuracy</h1>
          <p className="muted">
            Runs 50 labelled example replies through the classifier and checks it gets at least 95% right. Uses
            about 50 AI calls and takes a minute or two.
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => run()} disabled={busy}>
          {busy ? <Spinner label="Checking 50 replies…" /> : 'Run accuracy check'}
        </button>
      </div>
      <ErrorNote error={error || (loadError?.status === 0 ? null : loadError)} />

      {!report && !busy && <p className="muted">No check has been run yet.</p>}

      {report && (
        <>
          <div className="note">
            <Badge tone={report.passed ? 'good' : 'critical'}>{report.passed ? 'Passed' : 'Below target'}</Badge>
            <span>
              {report.correct} of {report.total} correct ({Math.round(report.accuracy * 100)}%; target{' '}
              {Math.round(report.target * 100)}%) · {report.model || 'unknown model'} · ran {relativeTime(report.ran_at)} in{' '}
              {report.duration_seconds}s
            </span>
          </div>
          <div className="stats stats-3">
            <StatTile label="Category accuracy" value={`${Math.round(report.accuracy * 100)}%`} note={`${report.correct} / ${report.total}`} />
            <StatTile label="Details extracted" value={ex?.checked ? `${Math.round((ex.correct / ex.checked) * 100)}%` : '—'}
              note={ex && `${ex.correct} / ${ex.checked} referral emails, return dates, slot picks`} />
            <StatTile label="Mistakes" value={report.mistakes.length} />
          </div>
          <Card title="Correct by category" subtitle="Bars show how many of each category were classified correctly">
            <BarList
              rows={CATEGORY_ORDER.filter((c) => report.by_category[c]?.total).map((c) => [c, `${CATEGORIES[c][1]} (of ${report.by_category[c].total})`])}
              counts={correct}
              total={report.total}
              max={Math.max(1, ...Object.values(report.by_category).map((v) => v.total))}
            />
          </Card>
          {(report.mistakes.length > 0 || ex?.failures?.length > 0) && (
            <Card title="What it got wrong">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Reply</th><th>Expected</th><th>Got</th></tr>
                  </thead>
                  <tbody>
                    {report.mistakes.map((m) => (
                      <tr key={m.id}>
                        <td>
                          <code>{m.id}</code> {m.body}
                          {m.error && <div className="field-error">{m.error}</div>}
                        </td>
                        <td>{CATEGORIES[m.expected]?.[1] || m.expected}</td>
                        <td>{m.got ? `${CATEGORIES[m.got]?.[1] || m.got} (${Math.round(m.confidence * 100)}%)` : 'error'}</td>
                      </tr>
                    ))}
                    {ex?.failures?.map((f) => (
                      <tr key={`${f.id}-${f.check}`}>
                        <td><code>{f.id}</code> detail missed</td>
                        <td>{f.check}</td>
                        <td className="muted">{JSON.stringify(f.extracted)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  )
}

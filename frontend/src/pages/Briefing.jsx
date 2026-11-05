import { useEffect, useState } from 'react'
import { api } from '../api'
import { AIBadge, Card, ErrorBox, Loading, Markdown, fmt, useApi } from '../components/ui'

export default function Briefing() {
  const list = useApi('/briefings')
  const [id, setId] = useState(null)
  const [doc, setDoc] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => {
    const path = id ? `/briefings/${id}` : '/briefings/latest'
    api.get(path).then(setDoc).catch((e) => setErr(e.message))
  }, [id])
  const gen = async () => {
    setBusy(true); setErr('')
    try { const r = await api.post('/briefings/generate', { email: false }); await list.reload(); setId(r.id) } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const sch = list.data?.schedule
  return (
    <div className="grid lg:grid-cols-[260px_minmax(0,1fr)] gap-4 items-start">
      <Card title="Briefings" subtitle={sch ? `Auto-generated weekdays at ${sch.time} (${sch.timezone})${sch.enabled ? '' : ' (scheduler off)'}${list.data.email ? ' · emailed' : ''}` : ''}>
        <button className="btn btn-primary w-full mb-3" disabled={busy} onClick={gen}>{busy ? 'Generating… (~30s)' : 'Generate now'}</button>
        {list.loading && <Loading />}
        <div className="space-y-1 max-h-[70vh] overflow-auto">
          {list.data?.items.map((b) => (
            <button key={b.id} onClick={() => setId(b.id)} className={`w-full text-left px-2 py-1.5 rounded text-sm ${doc?.id === b.id ? 'bg-panel2' : 'hover:bg-panel2'}`}>
              <div className="font-mono text-xs">{b.for_date}</div>
              <div className="text-[11px] text-muted">{fmt.datetime(b.created_at)} · {b.generator === 'template' ? 'template' : 'AI'}{b.emailed ? ' · emailed' : ''}</div>
            </button>
          ))}
        </div>
      </Card>
      <div className="space-y-3 min-w-0">
        {err && <ErrorBox error={err} />}
        {doc?.none && <Card><div className="text-sm text-muted">No briefing yet. Click <b>Generate now</b>, or wait for the scheduled run.</div></Card>}
        {doc?.content_md && (
          <Card right={doc.generator !== 'template' ? <div className="flex items-center gap-2"><AIBadge /><span className="text-xs text-muted font-mono">{doc.generator}</span></div> : <span className="label">template (add OPENAI_API_KEY for AI prose)</span>}>
            <Markdown text={doc.content_md} />
            <p className="text-xs text-muted mt-4 border-t border-line pt-2">Every number comes from MarketLens's data snapshot taken {fmt.datetime(doc.facts?.data_as_of?.generated_at)} (prices as of {doc.facts?.data_as_of?.prices}); news claims link to their sources. Not investment advice.</p>
          </Card>
        )}
      </div>
    </div>
  )
}

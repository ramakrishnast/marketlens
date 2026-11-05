import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import LineChart from '../components/LineChart.jsx'
import { AIBadge, Card, Markdown, NeedsKey, useApi } from '../components/ui'

const COLORS = ['#4ade80', '#60a5fa', '#f59e0b', '#e879f9', '#f87171', '#2dd4bf']

function Visual({ v }) {
  const series = useMemo(() => v.type === 'chart' ? v.series.map((s, i) => ({ ...s, color: COLORS[i % COLORS.length] })) : null, [v])
  if (v.type === 'chart') return (
    <div className="card p-3 my-2"><div className="text-sm font-medium mb-2">{v.title}</div><LineChart series={series} height={240} percent={v.percent} /></div>
  )
  return (
    <div className="card p-3 my-2 overflow-x-auto"><div className="text-sm font-medium mb-2">{v.title}</div>
      <table className="data text-sm"><thead><tr>{v.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{v.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c === null ? '—' : String(c)}</td>)}</tr>)}</tbody></table></div>
  )
}

function Answer({ m }) {
  const [open, setOpen] = useState(false)
  if (m.needs_key) return <NeedsKey what="Ask AI" />
  if (m.error) return <div className="text-down text-sm">{m.error}</div>
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2"><AIBadge /><span className="text-[11px] text-muted font-mono">{m.model}</span></div>
      <Markdown text={m.answer} />
      {m.visuals?.map((v, i) => <Visual key={i} v={v} />)}
      {(m.sources?.length > 0 || m.tool_calls?.length > 0) && (
        <div className="text-xs border-t border-line pt-2">
          <button className="text-muted hover:text-fg" onClick={() => setOpen(!open)}>{open ? '▾' : '▸'} {m.tool_calls.length} data lookups · {m.sources.length} sources · data as of</button>
          {open && (
            <div className="mt-2 grid md:grid-cols-3 gap-3">
              <div><div className="label mb-1">Tools called</div>{m.tool_calls.map((t, i) => <div key={i} className="font-mono text-[11px]">{t.ok ? '✓' : '✗'} {t.name}({Object.values(t.args).filter((x) => x !== null).map((x) => JSON.stringify(x)).join(', ')})</div>)}</div>
              <div><div className="label mb-1">Sources</div>{m.sources.map((s, i) => <div key={i}>{s.url ? <a className="underline" href={s.url} target="_blank" rel="noreferrer">{s.label}</a> : s.label}</div>)}</div>
              <div><div className="label mb-1">Data as of</div>{Object.entries(m.as_of || {}).map(([k, v]) => <div key={k} className="font-mono text-[11px] break-all">{k}: {typeof v === 'object' ? JSON.stringify(v) : String(v)}</div>)}</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function load() {
  try { return JSON.parse(localStorage.getItem('ml-chat')) || [] } catch { return [] }
}

export default function Chat() {
  const { data: meta } = useApi('/chat/meta')
  const [msgs, setMsgs] = useState(load)
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef(null)
  useEffect(() => { try { localStorage.setItem('ml-chat', JSON.stringify(msgs.slice(-30))) } catch { /* ignore */ } end.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs])
  const send = async (text) => {
    const question = (text ?? q).trim()
    if (!question || busy) return
    setQ('')
    const history = msgs.filter((m) => m.role === 'user' || (m.role === 'assistant' && m.answer)).map((m) => ({ role: m.role, content: m.role === 'user' ? m.content : m.answer }))
    setMsgs((x) => [...x, { role: 'user', content: question }])
    setBusy(true)
    try { const r = await api.post('/chat', { question, history }); setMsgs((x) => [...x, { role: 'assistant', ...r }]) } catch (e) { setMsgs((x) => [...x, { role: 'assistant', error: e.message }]) }
    setBusy(false)
  }
  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">Ask AI</h1>
        <span className="text-xs text-muted">Answers use MarketLens data via tool calls and cite their sources. Not investment advice.</span>
        {msgs.length > 0 && <button className="btn text-xs ml-auto" onClick={() => setMsgs([])}>Clear</button>}
      </div>
      {meta && !meta.enabled && <NeedsKey what="Ask AI" />}
      {msgs.length === 0 && meta?.enabled && (
        <div className="grid sm:grid-cols-2 gap-2">
          {meta.examples.map((e) => <button key={e} className="card p-3 text-left text-sm hover:border-accent" onClick={() => send(e)}>{e}</button>)}
        </div>
      )}
      <div className="space-y-4">
        {msgs.map((m, i) => m.role === 'user' ? (
          <div key={i} className="flex justify-end"><div className="bg-panel2 border border-line rounded-2xl rounded-br-sm px-4 py-2 text-sm max-w-[80%]">{m.content}</div></div>
        ) : (
          <Card key={i}><Answer m={m} /></Card>
        ))}
        {busy && <div className="text-sm text-muted animate-pulse">Looking up data and writing an answer…</div>}
        <div ref={end} />
      </div>
      <div className="sticky bottom-3 card p-2 flex gap-2 shadow-lg">
        <textarea className="input flex-1 resize-none h-11" placeholder="Ask about prices, fundamentals, news, signals, Congress trades…" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
        <button className="btn btn-primary px-4" disabled={busy || !q.trim()} onClick={() => send()}>Ask</button>
      </div>
    </div>
  )
}

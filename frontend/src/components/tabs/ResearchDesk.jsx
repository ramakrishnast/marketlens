import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { AIBadge, Card, Markdown, NeedsKey, fmt } from '../ui'

const STEPS = { starting: 'Starting', analysts: 'Analyst team writing reports (5 in parallel)', debate: 'Bull vs bear debate', risk: 'Risk lenses', memo: 'Research manager writing the memo' }

export default function ResearchDesk({ ticker }) {
  const [job, setJob] = useState(null)
  const [view, setView] = useState('memo')
  const timer = useRef()
  const poll = async () => {
    const j = await api.get(`/ticker/${encodeURIComponent(ticker)}/research`)
    setJob(j)
    if (j.status === 'running') timer.current = setTimeout(poll, 2500)
  }
  useEffect(() => { poll(); return () => clearTimeout(timer.current) }, [ticker]) // eslint-disable-line react-hooks/exhaustive-deps
  const start = async (force = false) => {
    const j = await api.post(`/ticker/${encodeURIComponent(ticker)}/research`, { rounds: 1, force })
    setJob(j)
    if (j.status === 'running') timer.current = setTimeout(poll, 2500)
  }
  const r = job?.result
  return (
    <div className="space-y-4">
      <Card title="AI Research Desk" subtitle="A team of AI agents researches the stock from MarketLens data: five analysts, then a bull-vs-bear debate, then three risk perspectives, then a scenario memo. Pattern adapted from TradingAgents (Apache-2.0), without its trading/rating step."
        right={r?.memo ? <div className="flex gap-2 items-center"><AIBadge /><button className="btn text-xs" onClick={() => start(true)}>Re-run</button></div> : null}>
        {(job?.needs_key || job?.ai_enabled === false) && <NeedsKey what="The Research Desk" />}
        {job?.status === 'none' && job.ai_enabled && <button className="btn btn-primary" onClick={() => start()}>Run research desk for {ticker} (~10 AI calls, ~1 min)</button>}
        {job?.status === 'running' && (
          <div className="space-y-2 text-sm">
            {Object.entries(STEPS).map(([k, label]) => {
              const order = Object.keys(STEPS)
              const done = order.indexOf(k) < order.indexOf(job.step)
              const now = k === job.step
              return <div key={k} className={done ? 'text-up' : now ? 'animate-pulse' : 'text-muted'}>{done ? '✓' : now ? '●' : '○'} {label}</div>
            })}
          </div>
        )}
        {job?.status === 'error' && <div className="text-down text-sm">{r?.error}</div>}
        {r?.memo && (
          <div>
            <div className="flex gap-1 mb-3 flex-wrap">
              {[['memo', 'Memo'], ['analysts', 'Analyst reports'], ['debate', 'Bull vs bear'], ['risk', 'Risk lenses']].map(([k, l]) => (
                <button key={k} className={`btn text-xs ${view === k ? 'btn-on' : ''}`} onClick={() => setView(k)}>{l}</button>))}
              <span className="text-xs text-muted ml-auto self-center">{r.model} · {fmt.date(r.date)}{r.cached ? ' · cached' : ''}</span>
            </div>
            {view === 'memo' && <Markdown text={r.memo} />}
            {view === 'analysts' && <div className="grid lg:grid-cols-2 gap-3">{Object.entries(r.reports).map(([k, v]) => (
              <div key={k} className="card p-3"><div className="font-semibold text-sm mb-1">{r.analyst_titles[k]}</div><Markdown text={v} /></div>))}</div>}
            {view === 'debate' && <div className="space-y-3">{r.debate.map((d, i) => (
              <div key={i} className={`card p-3 border-l-4 ${d.side === 'bull' ? 'border-l-up' : 'border-l-down'}`}><div className={`label mb-1 ${d.side === 'bull' ? 'text-up' : 'text-down'}`}>{d.side} researcher · round {d.round}</div><Markdown text={d.text} /></div>))}</div>}
            {view === 'risk' && <div className="grid lg:grid-cols-3 gap-3">{Object.entries(r.risk).map(([k, v]) => (
              <div key={k} className="card p-3"><div className="label mb-1">{k}</div><Markdown text={v} /></div>))}</div>}
            <p className="text-xs text-muted mt-3 border-t border-line pt-2">AI-generated research from MarketLens's cached data. It can be wrong and is not investment advice; there is deliberately no buy/sell/hold rating.</p>
          </div>
        )}
      </Card>
    </div>
  )
}

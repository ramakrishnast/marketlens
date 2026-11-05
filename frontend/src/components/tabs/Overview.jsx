import { useEffect, useState } from 'react'
import { Card, Disclaimer, ErrorBox, Loading, fmt, useApi } from '../ui'

const BIAS = {
  bullish: 'text-up border-up/40',
  bearish: 'text-down border-down/40',
  neutral: 'text-warn border-warn/40',
}

function BiasTag({ bias }) {
  return <span className={`label border rounded px-1.5 py-0.5 normal-case ${BIAS[bias] || ''}`}>{bias}</span>
}

export function SignalPanel({ ticker, setMarkers }) {
  const { data, error, loading, reload } = useApi(`/ticker/${encodeURIComponent(ticker)}/signals?days=365`)
  const [showAll, setShowAll] = useState(false)
  useEffect(() => { if (data && setMarkers) setMarkers(data.markers) }, [data, setMarkers])
  if (loading) return <Card title="Signals"><Loading /></Card>
  if (error) return <ErrorBox error={error} onRetry={reload} />
  const evts = showAll ? data.events : data.events.slice(0, 10)
  return (
    <Card title="Technical signals" subtitle="Rule-based flags on daily closes. Turn on 'Signal markers' above the chart to see them plotted.">
      <div className="space-y-1.5 mb-4">
        {data.state.map((s) => (
          <div key={s.id} className="flex items-start gap-2 text-sm">
            <span className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${s.bias === 'bullish' ? 'bg-up' : s.bias === 'bearish' ? 'bg-down' : 'bg-muted'}`} />
            <span>{s.text}{s.since && <span className="text-muted"> · since {fmt.date(s.since)}</span>}</span>
          </div>
        ))}
      </div>
      <div className="label mb-2">Triggered in the last 12 months</div>
      {data.events.length === 0 && <div className="text-sm text-muted">No signals triggered.</div>}
      <div className="divide-y divide-line">
        {evts.map((e, i) => (
          <div key={i} className="py-2 flex items-start gap-3 text-sm">
            <span className="font-mono text-xs text-muted w-20 shrink-0 pt-0.5">{e.date}</span>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap"><span className="font-medium">{e.label}</span><BiasTag bias={e.bias} /></div>
              <div className="text-xs text-muted">Rule: {e.rule}. Close {fmt.num(e.close)}{e.volume_ratio ? ` · ${e.volume_ratio}× avg volume, day ${fmt.pct(e.day_change_pct)}` : ''}{e.rsi ? ` · RSI ${e.rsi}` : ''}</div>
            </div>
          </div>
        ))}
      </div>
      {data.events.length > 10 && <button className="btn text-xs mt-2" onClick={() => setShowAll(!showAll)}>{showAll ? 'Show fewer' : `Show all ${data.events.length}`}</button>}
      <p className="text-xs text-muted mt-3">{data.note}</p>
    </Card>
  )
}

function Catalysts({ items }) {
  if (!items?.length) return <div className="text-sm text-muted">No dated catalysts in the next 45 days.</div>
  return (
    <ol className="relative border-l border-line ml-2 space-y-3">
      {items.map((c, i) => {
        const days = Math.round((new Date(c.date + 'T12:00:00') - new Date()) / 86400000)
        return (
          <li key={i} className="ml-4">
            <span className={`absolute -left-1.5 mt-1 w-3 h-3 rounded-full border-2 border-panel ${c.kind === 'earnings' ? 'bg-accent' : c.kind === 'macro' ? 'bg-warn' : 'bg-muted'}`} />
            <div className="text-sm"><span className="font-mono text-xs text-muted mr-2">{fmt.date(c.date)} · in {days}d</span>{c.url ? <a className="hover:underline" href={c.url} target="_blank" rel="noreferrer">{c.event}</a> : c.event}</div>
            <div className="text-xs text-muted">{c.source}</div>
          </li>
        )
      })}
    </ol>
  )
}

function Levels({ levels, price }) {
  const rows = [...levels]
  const idx = rows.findIndex((l) => l.price < price)
  const at = idx === -1 ? rows.length : idx
  rows.splice(at, 0, { current: true, price })
  return (
    <div className="text-sm divide-y divide-line">
      {rows.map((l, i) => l.current ? (
        <div key="cur" className="py-1.5 flex items-center gap-3 font-semibold"><span className="num w-20 text-accent">{fmt.num(l.price)}</span><span className="text-accent">◀ last close</span></div>
      ) : (
        <div key={i} className={`py-1.5 flex items-start gap-3 ${l.reachable_next_session ? '' : 'opacity-50'}`}>
          <span className="num w-20 shrink-0">{fmt.num(l.price)}</span>
          <span className={`num w-16 shrink-0 text-xs pt-0.5 ${l.distance_pct >= 0 ? 'text-up' : 'text-down'}`}>{fmt.pct(l.distance_pct, 1)}</span>
          <div className="min-w-0"><div>{l.label} <BiasTag bias={l.bias} /></div><div className="text-xs text-muted">{l.rule}</div></div>
        </div>
      ))}
    </div>
  )
}

function BaseRates({ rates }) {
  const [h, setH] = useState('20')
  const base = rates.baseline[h]
  return (
    <div>
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span className="text-xs text-muted">Forward return after signal, over</span>
        {rates.horizons_days.map((d) => <button key={d} className={`btn text-xs ${String(d) === h ? 'btn-on' : ''}`} onClick={() => setH(String(d))}>{d} trading days</button>)}
      </div>
      <div className="overflow-x-auto">
        <table className="data w-full text-sm">
          <thead><tr><th>Signal</th><th>n</th><th>Avg</th><th>Median</th><th>% positive</th><th>vs all days</th><th>Last fired</th><th>Sample</th></tr></thead>
          <tbody>
            <tr className="text-muted"><td>All days (baseline)</td><td className="num">{base.n}</td><td className="num">{fmt.pct(base.mean)}</td><td>—</td><td className="num">{base.pct_positive}%</td><td>—</td><td>—</td><td /></tr>
            {rates.signals.filter((s) => s.forward[h]).map((s) => {
              const f = s.forward[h]
              return (
                <tr key={s.id} className={s.recently_triggered ? 'bg-accent/5' : ''}>
                  <td>{s.label} {s.recently_triggered && <span className="label text-accent normal-case ml-1">fired recently</span>}</td>
                  <td className="num">{f.n}</td>
                  <td className={`num ${f.mean >= 0 ? 'text-up' : 'text-down'}`}>{fmt.pct(f.mean)}</td>
                  <td className="num">{fmt.pct(f.median)}</td>
                  <td className="num">{f.pct_positive}%</td>
                  <td className={`num ${f.vs_baseline >= 0 ? 'text-up' : 'text-down'}`}>{fmt.pct(f.vs_baseline)}</td>
                  <td className="font-mono text-xs">{s.last}</td>
                  <td className={`text-xs ${f.n < 5 ? 'text-down' : f.n < 15 ? 'text-warn' : 'text-muted'}`}>{s.reliability}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted mt-2">History {rates.history.start} → {rates.history.end} ({rates.history.bars} sessions). Close of the signal day to the close N sessions later.</p>
    </div>
  )
}

function Cases({ cases }) {
  const col = (title, items, cls) => (
    <div>
      <div className={`label mb-2 ${cls}`}>{title}</div>
      {items.length === 0 && <div className="text-sm text-muted">Nothing notable.</div>}
      <ul className="space-y-2">
        {items.map((o, i) => (
          <li key={i} className="text-sm">
            {o.url ? <a href={o.url} target="_blank" rel="noreferrer" className="hover:underline">{o.text}</a> : o.text}
            <div className="text-xs text-muted">{o.date ? `${fmt.date(o.date)} · ` : ''}{o.source}</div>
          </li>
        ))}
      </ul>
    </div>
  )
  return (
    <div className="grid md:grid-cols-2 gap-6">
      {col('What supports a bullish case', cases.bull, 'text-up')}
      {col('What supports a bearish case', cases.bear, 'text-down')}
    </div>
  )
}

export function OutlookPanel({ ticker }) {
  const { data, error, loading, reload } = useApi(`/ticker/${encodeURIComponent(ticker)}/outlook`)
  if (loading) return <Card title="Scenario outlook"><Loading text="Computing base rates…" /></Card>
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (data.error) return <Card title="Scenario outlook"><div className="text-sm text-muted">{data.error}</div></Card>
  const er = data.earnings_reaction
  return (
    <Card title="Scenario outlook: what could change the picture, and when"
      subtitle="Dated catalysts, the price levels that would trigger each rule, and what followed similar signals in the past. No buy/sell calls.">
      <Disclaimer>{data.disclaimer}</Disclaimer>
      <div className="grid lg:grid-cols-2 gap-6 mt-4">
        <div>
          <div className="label mb-3">Dates to watch</div>
          <Catalysts items={data.catalysts} />
          {er?.n > 0 && (
            <div className="mt-4 text-sm">
              <div className="label mb-1">Past earnings reactions</div>
              Over the last {er.n} earnings releases, {ticker} moved <b>{er.avg_abs_move_pct}%</b> on average (either direction), max {er.max_abs_move_pct}%.
              <div className="flex flex-wrap gap-1 mt-2">
                {er.moves.map((m) => <span key={m.date} title={m.date} className={`num text-xs px-1.5 py-0.5 rounded bg-panel2 ${m.move_pct >= 0 ? 'text-up' : 'text-down'}`}>{fmt.pct(m.move_pct, 1)}</span>)}
              </div>
            </div>
          )}
        </div>
        <div>
          <div className="label mb-3">Trigger levels (next-session close that would fire each rule)</div>
          <Levels levels={data.trigger_levels} price={data.price} />
          <p className="text-xs text-muted mt-2">Faded rows are more than 25% away.</p>
        </div>
      </div>
      <div className="mt-6">
        <div className="label mb-3">Historical base rates in {ticker}</div>
        <BaseRates rates={data.base_rates} />
      </div>
      <div className="mt-6">
        <Cases cases={data.cases} />
      </div>
    </Card>
  )
}

export default function Overview({ ticker, setMarkers }) {
  return (
    <div className="grid xl:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)] gap-4 items-start">
      <SignalPanel ticker={ticker} setMarkers={setMarkers} />
      <OutlookPanel ticker={ticker} />
    </div>
  )
}

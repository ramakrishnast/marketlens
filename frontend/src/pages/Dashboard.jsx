import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { TickerSearch } from '../App.jsx'
import { AsOf, Card, ErrorBox, Loading, Pct, fmt, useApi } from '../components/ui'

function Spark({ data, up }) {
  if (!data || data.length < 2) return null
  const min = Math.min(...data), max = Math.max(...data)
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * 100},${30 - ((v - min) / (max - min || 1)) * 28 - 1}`).join(' ')
  return (
    <svg viewBox="0 0 100 30" className="w-full h-7" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={up ? 'var(--up)' : 'var(--down)'} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

function MarketStrip() {
  const { data, error, loading, reload } = useApi('/market/overview')
  if (loading && !data) return <Loading text="Loading market overview…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {data.indices.map((x) => (
          <Link to={`/t/${encodeURIComponent(x.ticker)}`} key={x.ticker} className="card p-3 hover:border-accent">
            <div className="flex justify-between items-baseline">
              <span className="label">{x.label}</span>
              <span className="font-mono text-xs text-muted">{x.ticker}</span>
            </div>
            <div className="flex justify-between items-baseline mt-1">
              <span className="text-lg font-semibold num">{x.ticker === '^TNX' ? `${fmt.num(x.price)}%` : fmt.num(x.price)}</span>
              <Pct v={x.change_pct} className="text-sm" />
            </div>
            <Spark data={x.spark} up={x.change_pct >= 0} />
          </Link>
        ))}
      </div>
      <SectorHeatmap sectors={data.sectors} asOf={data.indices[0]?.as_of} />
    </div>
  )
}

function heat(v) {
  if (v === null || v === undefined) return 'var(--panel2)'
  const a = Math.min(Math.abs(v) / 3, 1) * 0.75 + 0.1
  return v >= 0 ? `color-mix(in srgb, var(--up) ${a * 100}%, var(--panel))` : `color-mix(in srgb, var(--down) ${a * 100}%, var(--panel))`
}

function SectorHeatmap({ sectors, asOf }) {
  const [period, setPeriod] = useState('change_pct')
  const scale = { change_pct: 1, ret_1w: 2, ret_1m: 4, ret_ytd: 10 }[period]
  const sorted = useMemo(() => [...sectors].sort((a, b) => (b[period] ?? -999) - (a[period] ?? -999)), [sectors, period])
  return (
    <Card title="Sector heatmap" subtitle="Select Sector SPDR ETFs, sorted best to worst"
      right={<div className="flex items-center gap-3 flex-wrap justify-end"><AsOf asOf={asOf} />
        <div className="flex gap-1">{[['change_pct', '1D'], ['ret_1w', '1W'], ['ret_1m', '1M'], ['ret_ytd', 'YTD']].map(([k, l]) => (
          <button key={k} className={`btn text-xs ${period === k ? 'btn-on' : ''}`} onClick={() => setPeriod(k)}>{l}</button>))}</div></div>}>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-11 gap-2">
        {sorted.map((s) => (
          <Link to={`/t/${s.ticker}`} key={s.ticker} className="rounded-lg p-2.5 text-center hover:ring-1 ring-accent"
            style={{ background: heat((s[period] ?? 0) / scale) }}>
            <div className="font-mono text-xs font-semibold">{s.ticker}</div>
            <div className="text-[11px] opacity-80 truncate">{s.name}</div>
            <div className="num text-sm font-semibold mt-0.5">{fmt.pct(s[period], 1)}</div>
          </Link>
        ))}
      </div>
    </Card>
  )
}

const COLS = [
  ['ticker', 'Ticker'], ['price', 'Price'], ['change_pct', 'Day %'], ['volume_vs_avg', 'Vol / avg'],
  ['range_52w_pos', '52w range'], ['rsi14', 'RSI 14'], ['dist_sma50_pct', 'vs 50d MA'], ['dist_sma200_pct', 'vs 200d MA'],
  ['next_earnings', 'Next earnings'], ['sentiment', 'Sentiment 7d'],
]

function RangeBar({ pos }) {
  if (pos === null || pos === undefined) return '—'
  return (
    <div className="flex items-center gap-2 w-28">
      <div className="relative h-1.5 flex-1 rounded bg-panel2">
        <div className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-accent" style={{ left: `calc(${Math.max(0, Math.min(100, pos))}% - 4px)` }} />
      </div>
      <span className="text-xs text-muted num w-8 text-right">{Math.round(pos)}%</span>
    </div>
  )
}

function WatchlistTable({ wl, onChanged }) {
  const nav = useNavigate()
  const { data, error, loading, reload } = useApi(`/watchlists/${wl.id}/table`, [wl.tickers.join(',')])
  const [sort, setSort] = useState(['ticker', 1])
  const rows = useMemo(() => {
    if (!data) return []
    const [k, dir] = sort
    return [...data.rows].sort((a, b) => {
      const av = k === 'sentiment' ? a.sentiment?.score : a[k]
      const bv = k === 'sentiment' ? b.sentiment?.score : b[k]
      if (av === bv) return 0
      if (av === null || av === undefined) return 1
      if (bv === null || bv === undefined) return -1
      return (av > bv ? 1 : -1) * dir
    })
  }, [data, sort])

  const add = async (t) => { await api.post(`/watchlists/${wl.id}/items`, { ticker: t }); onChanged() }
  const remove = async (t) => { await api.del(`/watchlists/${wl.id}/items/${encodeURIComponent(t)}`); onChanged() }
  const refresh = async () => { await api.post('/refresh', { tickers: wl.tickers }); reload() }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-center">
        <TickerSearch className="w-72" placeholder="Add ticker to this watchlist…" onPick={add} />
        <button className="btn" onClick={refresh}>↻ Refresh prices</button>
        {data?.rows?.[0]?.as_of && <AsOf asOf={data.rows[0].as_of} />}
      </div>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data && <Loading text="Loading prices (first load of a new ticker can take a few seconds)…" />}
      {data && wl.tickers.length === 0 && <div className="text-muted text-sm py-6">Empty watchlist. Add tickers above, or run <code className="font-mono">make demo</code>.</div>}
      {data && rows.length > 0 && (
        <div className="overflow-x-auto card">
          <table className="data w-full text-sm">
            <thead><tr>
              {COLS.map(([k, l]) => (
                <th key={k} className="cursor-pointer select-none" onClick={() => setSort([k, sort[0] === k ? -sort[1] : (k === 'ticker' ? 1 : -1)])}>
                  {l}{sort[0] === k ? (sort[1] > 0 ? ' ↑' : ' ↓') : ''}
                </th>))}
              <th />
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.ticker} className="cursor-pointer" onClick={() => nav(`/t/${encodeURIComponent(r.ticker)}`)}>
                  <td><div className="font-mono font-semibold">{r.ticker}</div><div className="text-xs text-muted truncate max-w-[180px]">{r.name}</div></td>
                  {r.error ? <td colSpan={9} className="text-down text-xs">{r.error}</td> : <>
                    <td className="num">{fmt.num(r.price)}</td>
                    <td><Pct v={r.change_pct} /></td>
                    <td className={`num ${r.volume_vs_avg > 1.5 ? 'text-warn font-semibold' : ''}`}>{r.volume_vs_avg ? `${r.volume_vs_avg.toFixed(2)}×` : '—'}</td>
                    <td><RangeBar pos={r.range_52w_pos} /></td>
                    <td className={`num ${r.rsi14 > 70 ? 'text-down' : r.rsi14 < 30 ? 'text-up' : ''}`}>{fmt.num(r.rsi14, 1)}</td>
                    <td><Pct v={r.dist_sma50_pct} d={1} /></td>
                    <td><Pct v={r.dist_sma200_pct} d={1} /></td>
                    <td className="text-xs">{r.next_earnings ? <span title={r.next_earnings.source}>{fmt.date(r.next_earnings.date)}{r.next_earnings.estimated && <span className="text-muted"> (est.)</span>}</span> : '—'}</td>
                    <td>{r.sentiment && r.sentiment.n > 0 ? <span className={`num ${r.sentiment.score > 0.1 ? 'text-up' : r.sentiment.score < -0.1 ? 'text-down' : 'text-muted'}`} title={`${r.sentiment.n} headlines`}>{r.sentiment.score > 0 ? '+' : ''}{r.sentiment.score.toFixed(2)}</span> : <span className="text-muted">—</span>}</td>
                  </>}
                  <td><button className="text-muted hover:text-down px-1" title="Remove" onClick={(e) => { e.stopPropagation(); remove(r.ticker) }}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function Dashboard() {
  const { data: lists, error, reload } = useApi('/watchlists')
  const [active, setActive] = useState(null)
  useEffect(() => { if (lists?.length && !lists.find((l) => l.id === active)) setActive(lists[0].id) }, [lists, active])
  const wl = lists?.find((l) => l.id === active)

  const create = async () => {
    const name = window.prompt('New watchlist name')
    if (!name) return
    const w = await api.post('/watchlists', { name })
    await reload(); setActive(w.id)
  }
  const rename = async () => {
    const name = window.prompt('Rename watchlist', wl.name)
    if (name) { await api.patch(`/watchlists/${wl.id}`, { name }); reload() }
  }
  const del = async () => {
    if (window.confirm(`Delete watchlist "${wl.name}"?`)) { await api.del(`/watchlists/${wl.id}`); setActive(null); reload() }
  }

  return (
    <div className="space-y-5">
      <MarketStrip />
      <Card title="Watchlists" right={<button className="btn" onClick={create}>+ New watchlist</button>}>
        {error && <ErrorBox error={error} onRetry={reload} />}
        <div className="flex flex-wrap gap-2 items-center mb-4">
          {lists?.map((l) => (
            <button key={l.id} onClick={() => setActive(l.id)} className={`btn ${l.id === active ? 'btn-on' : ''}`}>
              {l.name} <span className="text-muted text-xs ml-1">{l.tickers.length}</span>
            </button>
          ))}
          {wl && <span className="ml-auto flex gap-2"><button className="btn text-xs" onClick={rename}>Rename</button><button className="btn text-xs" onClick={del}>Delete</button></span>}
        </div>
        {wl && <WatchlistTable key={wl.id} wl={wl} onChanged={reload} />}
      </Card>
    </div>
  )
}

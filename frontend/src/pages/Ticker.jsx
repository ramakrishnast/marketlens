import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api'
import PriceChart, { OVERLAYS, PANES } from '../components/PriceChart.jsx'
import { AsOf, Card, ErrorBox, Loading, Pct, Tabs, fmt, useApi } from '../components/ui'
import { TICKER_TABS } from '../components/tabs/index.jsx'

const RANGES = ['3m', '6m', '1y', '2y', '5y', 'max']
const DEFAULT_ON = ['sma50', 'sma200', 'rsi', 'levels']

function loadEnabled() {
  try { const v = JSON.parse(localStorage.getItem('ml-indicators')); if (Array.isArray(v)) return new Set(v) } catch { /* ignore */ }
  return new Set(DEFAULT_ON)
}

function Stat({ label, children }) {
  return <div><div className="label">{label}</div><div className="num text-sm mt-0.5">{children}</div></div>
}

function AddToWatchlist({ ticker }) {
  const [lists, setLists] = useState([])
  const [msg, setMsg] = useState('')
  useEffect(() => { api.get('/watchlists').then(setLists).catch(() => {}) }, [])
  if (!lists.length) return null
  const inAny = lists.filter((l) => l.tickers.includes(ticker))
  return (
    <div className="flex items-center gap-2 text-sm">
      <select className="input py-1 text-xs" defaultValue="" onChange={async (e) => {
        const id = e.target.value; if (!id) return
        await api.post(`/watchlists/${id}/items`, { ticker })
        setLists(await api.get('/watchlists')); setMsg('Added'); e.target.value = ''
        setTimeout(() => setMsg(''), 1500)
      }}>
        <option value="">+ Add to watchlist…</option>
        {lists.map((l) => <option key={l.id} value={l.id} disabled={l.tickers.includes(ticker)}>{l.name}</option>)}
      </select>
      <span className="text-xs text-muted">{msg || (inAny.length ? `In: ${inAny.map((l) => l.name).join(', ')}` : '')}</span>
    </div>
  )
}

export function ChartPanel({ ticker, markers }) {
  const [range, setRange] = useState('1y')
  const [enabled, setEnabled] = useState(loadEnabled)
  const intraday = range === '5d'
  const { data, error, loading, reload } = useApi(intraday ? null : `/ticker/${encodeURIComponent(ticker)}/chart?range=${range}`)
  const intra = useApi(intraday ? `/ticker/${encodeURIComponent(ticker)}/intraday` : null)
  const toggle = (id) => {
    const n = new Set(enabled)
    n.has(id) ? n.delete(id) : n.add(id)
    setEnabled(n)
    try { localStorage.setItem('ml-indicators', JSON.stringify([...n])) } catch { /* ignore */ }
  }
  const vwapLine = useMemo(() => (intra.data?.vwap?.length ? [{ data: intra.data.vwap, color: '#f59e0b', title: 'VWAP' }] : []), [intra.data])
  const visibleMarkers = useMemo(() => {
    if (!markers || !data?.candles?.length) return []
    const first = data.candles[0].time
    return markers.filter((m) => m.time >= first)
  }, [markers, data])

  return (
    <Card>
      <div className="flex flex-wrap gap-2 items-center mb-3">
        <div className="flex gap-1">
          {[...RANGES, '5d'].map((r) => (
            <button key={r} className={`btn text-xs font-mono ${range === r ? 'btn-on' : ''}`} onClick={() => setRange(r)}>
              {r === '5d' ? '5D intraday' : r.toUpperCase()}
            </button>
          ))}
        </div>
        {!intraday && (
          <div className="flex flex-wrap gap-1 lg:ml-auto">
            {[...OVERLAYS, ...PANES].map((o) => (
              <button key={o.id} className={`btn text-xs ${enabled.has(o.id) ? 'btn-on' : ''}`} onClick={() => toggle(o.id)}>
                {o.color && <span className="inline-block w-2 h-2 rounded-full mr-1" style={{ background: o.color }} />}{o.label}
              </button>
            ))}
            <button className={`btn text-xs ${enabled.has('signals') ? 'btn-on' : ''}`} onClick={() => toggle('signals')}>Signal markers</button>
          </div>
        )}
      </div>
      {!intraday && <>
        {error && <ErrorBox error={error} onRetry={reload} />}
        {loading && !data && <Loading text="Loading chart…" />}
        {data && <PriceChart candles={data.candles} indicators={data.indicators} levels={data.levels}
          markers={enabled.has('signals') ? visibleMarkers : []} enabled={enabled} />}
      </>}
      {intraday && <>
        {intra.loading && <Loading text="Loading intraday bars…" />}
        {intra.data && !intra.data.bars.length && <div className="text-muted text-sm py-8 text-center">{intra.data.note}</div>}
        {intra.data?.bars?.length > 0 && <>
          <PriceChart candles={intra.data.bars} enabled={new Set()} extraLines={vwapLine} />
          <p className="text-xs text-muted mt-2">5-minute bars with session VWAP (orange). {intra.data.note}</p>
        </>}
      </>}
    </Card>
  )
}

export default function Ticker() {
  const { ticker: raw } = useParams()
  const ticker = decodeURIComponent(raw).toUpperCase()
  const { data, error, loading, reload } = useApi(`/ticker/${encodeURIComponent(ticker)}/chart?range=1y`)
  const [tab, setTab] = useState(TICKER_TABS[0]?.id)
  const [markers, setMarkers] = useState([])
  useEffect(() => { setMarkers([]) }, [ticker])
  const s = data?.snapshot || {}
  const ActiveTab = TICKER_TABS.find((t) => t.id === tab)?.Component

  return (
    <div className="space-y-4">
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data && <Loading text={`Loading ${ticker}…`} />}
      {data && <>
        <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
          <div>
            <div className="flex items-baseline gap-3">
              <h1 className="text-2xl font-bold font-mono">{ticker}</h1>
              <span className="text-muted">{data.name}</span>
            </div>
            <div className="flex items-baseline gap-3 mt-1">
              <span className="text-3xl font-semibold num">{fmt.num(s.price)}</span>
              <span className={`num ${s.change >= 0 ? 'text-up' : 'text-down'}`}>{s.change >= 0 ? '+' : ''}{fmt.num(s.change)}</span>
              <Pct v={s.change_pct} />
            </div>
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-x-6 gap-y-2">
            <Stat label="52w range">{fmt.num(s.low_52w)} – {fmt.num(s.high_52w)}</Stat>
            <Stat label="Vol / 50d avg">{s.volume_vs_avg ? `${s.volume_vs_avg.toFixed(2)}×` : '—'}</Stat>
            <Stat label="vs 50d MA"><Pct v={s.dist_sma50_pct} d={1} /></Stat>
            <Stat label="vs 200d MA"><Pct v={s.dist_sma200_pct} d={1} /></Stat>
            <Stat label="1M / 1Y"><Pct v={s.ret_1m} d={1} /> / <Pct v={s.ret_1y} d={1} /></Stat>
            <Stat label="YTD"><Pct v={s.ret_ytd} d={1} /></Stat>
          </div>
          <div className="ml-auto flex flex-col items-end gap-1">
            <AsOf asOf={data.as_of} />
            <AddToWatchlist ticker={ticker} />
          </div>
        </div>
        <ChartPanel key={ticker} ticker={ticker} markers={markers} />
        {TICKER_TABS.length > 0 && <>
          <Tabs tabs={TICKER_TABS} value={tab} onChange={setTab} />
          {ActiveTab && <ActiveTab key={`${ticker}-${tab}`} ticker={ticker} snapshot={s} setMarkers={setMarkers} />}
        </>}
      </>}
    </div>
  )
}

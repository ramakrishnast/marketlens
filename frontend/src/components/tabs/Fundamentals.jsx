import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AsOf, Card, ErrorBox, Loading, Segmented, fmt, useApi } from '../ui'

const C = { a: '#4ade80', b: '#60a5fa', c: '#f59e0b', d: '#e879f9', e: '#f87171' }

const axis = { stroke: 'var(--muted)', fontSize: 11, tickLine: false, axisLine: false }
const tip = { contentStyle: { background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, fontSize: 12 }, labelStyle: { color: 'var(--fg)' } }

function big(v) {
  if (v === null || v === undefined) return ''
  const a = Math.abs(v)
  if (a >= 1e12) return `${(v / 1e12).toFixed(1)}T`
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (a >= 1e6) return `${(v / 1e6).toFixed(0)}M`
  return v.toFixed(2)
}

function periodLabel(r, annual) {
  const d = new Date(r.period_end + 'T12:00:00')
  return annual ? `FY${String(d.getFullYear()).slice(2)}` : `${d.toLocaleString('en', { month: 'short' })} ${String(d.getFullYear()).slice(2)}`
}

function MiniChart({ title, data, series, kind = 'bar', format = big, note }) {
  const has = data.some((r) => series.some((s) => r[s.key] !== undefined && r[s.key] !== null))
  return (
    <div className="card p-3">
      <div className="text-sm font-medium mb-2">{title}</div>
      {!has ? <div className="text-xs text-muted h-44 grid place-items-center">Not reported in XBRL</div> : (
        <ResponsiveContainer width="100%" height={180}>
          {kind === 'bar' ? (
            <BarChart data={data} margin={{ left: -10, right: 4, top: 4 }}>
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" {...axis} interval="preserveStartEnd" />
              <YAxis {...axis} tickFormatter={format} width={52} />
              <Tooltip {...tip} formatter={(v) => format(v)} cursor={{ fill: 'var(--panel2)' }} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
              {series.map((s) => <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color} radius={[3, 3, 0, 0]} />)}
            </BarChart>
          ) : (
            <LineChart data={data} margin={{ left: -10, right: 4, top: 4 }}>
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" {...axis} interval="preserveStartEnd" />
              <YAxis {...axis} tickFormatter={format} width={52} domain={['auto', 'auto']} />
              <Tooltip {...tip} formatter={(v) => format(v)} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
              {series.map((s) => <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} dot={false} strokeWidth={2} connectNulls />)}
            </LineChart>
          )}
        </ResponsiveContainer>
      )}
      {note && <div className="text-[11px] text-muted mt-1">{note}</div>}
    </div>
  )
}

function Metric({ label, value, hint }) {
  return (
    <div className="card p-3">
      <div className="label">{label}</div>
      <div className="text-lg font-semibold num mt-0.5">{value}</div>
      {hint && <div className="text-[11px] text-muted mt-0.5">{hint}</div>}
    </div>
  )
}

const x = (v, d = 1) => (v === null || v === undefined ? '—' : `${v.toFixed(d)}×`)
const p = (v) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`)

export function PeerTable({ ticker }) {
  const { data, error, loading, reload } = useApi(`/ticker/${encodeURIComponent(ticker)}/peers`)
  if (loading) return <Loading text="Loading peers…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data.peers?.length) return <div className="text-sm text-muted">No peers found.</div>
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="data w-full text-sm">
          <thead><tr><th>Company</th><th>Mkt cap</th><th>P/E ttm</th><th>Fwd P/E</th><th>P/S</th><th>EV/EBITDA</th><th>PEG</th><th>Gross m.</th><th>Op. m.</th><th>Net m.</th><th>Rev growth</th></tr></thead>
          <tbody>
            {data.peers.map((r) => (
              <tr key={r.ticker} className={r.ticker === ticker ? 'bg-accent/5 font-medium' : ''}>
                <td><Link to={`/t/${r.ticker}`} className="font-mono font-semibold hover:underline">{r.ticker}</Link> <span className="text-xs text-muted">{r.name}</span></td>
                {r.error ? <td colSpan={10} className="text-xs text-muted">{r.error}</td> : <>
                  <td className="num">{fmt.big(r.market_cap)}</td><td className="num">{x(r.pe_ttm)}</td><td className="num">{x(r.forward_pe)}</td>
                  <td className="num">{x(r.ps_ttm)}</td><td className="num">{x(r.ev_ebitda)}</td><td className="num">{r.peg ? r.peg.toFixed(2) : '—'}</td>
                  <td className="num">{p(r.gross_margin)}</td><td className="num">{p(r.operating_margin)}</td><td className="num">{p(r.net_margin)}</td>
                  <td className="num">{r.revenue_growth !== null && r.revenue_growth !== undefined ? fmt.pct(r.revenue_growth, 1) : '—'}</td>
                </>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted mt-2">Peer basis: {data.basis}. TTM = trailing four quarters from SEC filings; prices from the local cache.</p>
    </div>
  )
}

export default function Fundamentals({ ticker }) {
  const { data, error, loading, reload } = useApi(`/ticker/${encodeURIComponent(ticker)}/fundamentals`)
  const [period, setPeriod] = useState('quarterly')
  const rows = useMemo(() => {
    if (!data?.available) return []
    const annual = period === 'annual'
    return data[period].map((r) => ({
      ...r, label: periodLabel(r, annual),
      gm: r.gross_margin, om: r.operating_margin, nm: r.net_margin, fm: r.fcf_margin,
    }))
  }, [data, period])
  if (loading) return <Loading text="Loading SEC fundamentals…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data.available) return <Card><div className="text-sm text-muted">{data.reason}</div></Card>
  const v = data.valuation
  const derived = [...new Set(rows.flatMap((r) => r.derived || []))]
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="font-semibold">{data.entity}</h3>
        <AsOf asOf={{ ...data.as_of, last_bar: null }} label="SEC data fetched" />
        <span className="text-xs text-muted">Latest period: {fmt.date(data.as_of.last_period)}</span>
        <div className="ml-auto"><Segmented options={['quarterly', 'annual']} value={period} onChange={setPeriod} /></div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-10 gap-2">
        <Metric label="Market cap" value={fmt.big(v.market_cap)} hint={`at ${fmt.num(v.price)}`} />
        <Metric label="P/E (ttm)" value={x(v.pe_ttm)} />
        <Metric label="Forward P/E" value={x(v.forward_pe)} hint={v.forward_pe ? 'Yahoo estimates' : 'n/a from free data'} />
        <Metric label="P/S (ttm)" value={x(v.ps_ttm)} />
        <Metric label="EV/EBITDA" value={x(v.ev_ebitda)} />
        <Metric label="PEG" value={v.peg ? v.peg.toFixed(2) : '—'} hint="P/E ÷ EPS growth" />
        <Metric label="FCF yield" value={p(v.fcf_yield)} />
        <Metric label="Rev growth" value={fmt.pct(v.growth_yoy_pct?.revenue, 1)} hint="TTM vs prior TTM" />
        <Metric label="EPS growth" value={fmt.pct(v.growth_yoy_pct?.eps_diluted, 1)} hint="TTM vs prior TTM" />
        <Metric label="Net debt" value={fmt.big((v.total_debt || 0) - (v.cash || 0))} hint="debt − cash" />
      </div>
      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
        <MiniChart title="Revenue & net income" data={rows} series={[{ key: 'revenue', name: 'Revenue', color: C.b }, { key: 'net_income', name: 'Net income', color: C.a }]} />
        <MiniChart title="EPS (diluted)" data={rows} series={[{ key: 'eps_diluted', name: 'EPS', color: C.a }]} format={(v) => (v === null || v === undefined ? '' : `$${v.toFixed(2)}`)} />
        <MiniChart title="Margins" kind="line" data={rows} format={(v) => (v === null || v === undefined ? '' : `${(v * 100).toFixed(0)}%`)}
          series={[{ key: 'gm', name: 'Gross', color: C.b }, { key: 'om', name: 'Operating', color: C.c }, { key: 'nm', name: 'Net', color: C.a }, { key: 'fm', name: 'FCF', color: C.d }]} />
        <MiniChart title="Free cash flow (CFO − capex)" data={rows} series={[{ key: 'fcf', name: 'FCF', color: C.d }]} />
        <MiniChart title="Debt vs cash" data={rows} series={[{ key: 'total_debt', name: 'Total debt', color: C.e }, { key: 'cash', name: 'Cash', color: C.a }]} />
        <MiniChart title="Shares outstanding" kind="line" data={rows} series={[{ key: 'shares_outstanding', name: 'Shares', color: C.c }]} />
      </div>
      {derived.length > 0 && <p className="text-xs text-muted">Some values ({derived.join(', ')}) are derived: Q4 = full year − nine months, and quarterly cash flow = year-to-date differences. Derived EPS is an approximation.</p>}
      <Card title="Peer comparison"><PeerTable ticker={ticker} /></Card>
    </div>
  )
}

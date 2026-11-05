import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import LineChart from '../components/LineChart.jsx'
import { Card, ErrorBox, Loading, Pct, Segmented, fmt, useApi } from '../components/ui'
import { Initials, PartyDot, money } from './Congress.jsx'

const PERIODS = [{ value: '30d', label: '30 days' }, { value: '90d', label: '90 days' }, { value: '6m', label: '6 months' }, { value: '1y', label: '1 yr' }, { value: 'all', label: 'all data' }]

function StatCard({ title, sub, children }) {
  return (
    <div className="card p-4">
      <div className="label">{title}</div>
      {sub && <div className="text-[10px] font-mono text-muted">{sub}</div>}
      <div className="mt-2">{children}</div>
    </div>
  )
}

function Timeline({ points }) {
  const [mode, setMode] = useState('daily')
  const series = useMemo(() => {
    let pts = points
    if (mode === 'monthly') {
      const m = {}
      for (const p of points) {
        const k = p.date.slice(0, 7) + '-01'
        m[k] = m[k] || { date: k, traded: 0, disclosed: 0 }
        m[k].traded += p.traded; m[k].disclosed += p.disclosed
      }
      pts = Object.values(m)
    }
    return [
      { name: 'Trade date', color: '#4ade80', data: pts.map((p) => ({ time: p.date, value: p.traded })) },
      { name: 'Disclosed date', color: '#60a5fa', dashed: true, data: pts.map((p) => ({ time: p.date, value: p.disclosed })) },
    ]
  }, [points, mode])
  return (
    <Card title="Trades and disclosures over time" subtitle="Counts per day (or month). Each line uses its own date; a trade can be disclosed weeks later."
      right={<Segmented options={[{ value: 'monthly', label: 'Monthly' }, { value: 'daily', label: 'Daily' }]} value={mode} onChange={setMode} />}>
      {points.length ? <LineChart series={series} height={260} /> : <div className="text-sm text-muted">No trades in this period.</div>}
    </Card>
  )
}

export default function CongressMember() {
  const { name: raw } = useParams()
  const name = decodeURIComponent(raw)
  const [period, setPeriod] = useState('6m')
  const { data, error, loading, reload } = useApi(`/congress/member?name=${encodeURIComponent(name)}&period=${period}`)
  const [sort, setSort] = useState(['disclosure_date', -1])
  const trades = useMemo(() => {
    if (!data?.trades) return []
    const [k, d] = sort
    return [...data.trades].sort((a, b) => ((a[k] ?? -1e18) > (b[k] ?? -1e18) ? 1 : -1) * d)
  }, [data, sort])
  if (loading && !data) return <Loading text="Loading member…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data.found) return <Card><div className="text-sm">No disclosed trades found for {name}. <Link className="text-accent" to="/congress">Back to leaderboard</Link></div></Card>
  const s = data.stats
  const at = data.all_time
  const follow = async () => { await api.post('/congress/follow', { member: name, on: !data.followed }); reload() }
  const th = (k, label, sub) => (
    <th className="cursor-pointer select-none" onClick={() => setSort([k, sort[0] === k ? -sort[1] : -1])}>{label}{sort[0] === k ? (sort[1] > 0 ? ' ↑' : ' ↓') : ''}{sub && <div className="normal-case text-[10px]">{sub}</div>}</th>
  )
  return (
    <div className="space-y-5">
      <Link to="/congress" className="text-xs font-mono text-accent">← Return to leaderboard</Link>
      <div className="flex flex-wrap gap-6 items-start">
        <div className="flex items-start gap-4">
          <Initials name={name} size={72} color={data.party?.startsWith('Rep') ? '#f87171' : data.party?.startsWith('Dem') ? '#60a5fa' : undefined} />
          <div>
            <h1 className="text-3xl font-bold">{name}</h1>
            <div className="text-sm text-muted flex items-center gap-2 mt-1"><PartyDot party={data.party} />{data.party || 'Party unknown'} · {data.chamber} · {data.state_dst}</div>
            <div className="label mt-2">All-time stats (since {fmt.date(at.first_trade)})</div>
            <div className="flex flex-wrap gap-1.5 mt-1 text-xs font-mono">
              {at.rank_6m && <span className="border border-line rounded px-2 py-0.5">Ranked #{at.rank_6m} of {at.ranked_members} (6 months)</span>}
              <span className="border border-line rounded px-2 py-0.5">{at.trades} trades</span>
              <span className="border border-line rounded px-2 py-0.5">Files {at.median_filing_days} days after trading (median)</span>
            </div>
          </div>
        </div>
        <div className="card p-3 min-w-[220px]">
          <div className="text-sm font-semibold">Estimated holdings <span className="text-muted font-normal text-xs">· from disclosures</span></div>
          <div className="flex items-center gap-3 mt-2">
            <span className="w-12 h-12 rounded-full border-4 border-[#60a5fa] grid place-items-center font-bold">{data.holdings.length}</span>
            <div className="text-xs space-x-1">{data.holdings.slice(0, 8).map((h) => <Link key={h.ticker} to={`/t/${h.ticker}`} className="font-mono hover:underline">{h.ticker}</Link>)}</div>
          </div>
          <div className="text-[10px] text-muted mt-1">Tickers bought more often than sold, with no full sale disclosed</div>
        </div>
        <button className={`btn ml-auto ${data.followed ? 'btn-on' : 'btn-primary'}`} onClick={follow}>{data.followed ? '✓ Following' : '+ Follow'}</button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Segmented label="Disclosed" options={PERIODS} value={period} onChange={setPeriod} />
        <span className="label">This period · {fmt.date(data.period_start)} – today</span>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <StatCard title="Member's estimated return" sub="on trade date">
          {s.estimated_return_pct !== undefined ? <>
            <Pct v={s.estimated_return_pct} d={0} className="text-2xl font-semibold" />
            {s.estimated_range && <div className="text-xs mt-1">Range: {fmt.pct(s.estimated_range[0], 0)} to {fmt.pct(s.estimated_range[1], 0)}</div>}
            <div className="text-xs text-muted mt-1">S&P: <b>{fmt.pct(s.estimated_spy_pct, 1)}</b> same windows / {fmt.pct(s.spy_period_pct, 1)} period</div>
          </> : <span className="text-sm text-muted">No priced purchases</span>}
        </StatCard>
        <StatCard title="Copy-trading strategy" sub="bought at next open after disclosure">
          {s.copy_return_pct !== undefined && s.copy_return_pct !== null ? <>
            <Pct v={s.copy_return_pct} d={0} className="text-2xl font-semibold" />
            <div className="text-xs text-muted mt-1">S&P: <b>{fmt.pct(s.copy_spy_pct, 1)}</b> same windows / {fmt.pct(s.spy_period_pct, 1)} period</div>
          </> : <span className="text-sm text-muted">—</span>}
        </StatCard>
        <StatCard title="Money traded"><div className="text-2xl font-semibold num">{money(s.money_low, s.money_high)}</div><div className="text-xs text-muted mt-1">reported range</div></StatCard>
        <StatCard title="Filing time"><div className="text-2xl font-semibold text-accent">{s.median_filing_days ?? '—'} days</div><div className="text-xs text-muted mt-1">median, trade to filing</div></StatCard>
        <StatCard title="Trades with price data"><div className="text-2xl font-semibold num">{s.priced}/{s.priceable_buys} priced</div><div className="text-xs text-muted mt-1">{s.buys} buys · {s.sells} sells in period</div></StatCard>
      </div>
      <p className="text-xs text-muted">Both returns model disclosed purchases only, amount-weighted by range midpoints. This is not the member's complete portfolio or net worth. {data.label}</p>

      <Timeline points={data.timeline} />

      <Card title="Disclosed trades" subtitle="The selected period uses the disclosure date; the trade date may be earlier. Click a column to sort.">
        <div className="overflow-x-auto -mx-4">
          <table className="data w-full text-sm">
            <thead><tr>
              {th('ticker', 'Ticker')}<th>Company</th>{th('tx_type', 'Action')}{th('tx_date', 'Traded')}{th('disclosure_date', 'Disclosed')}{th('delay_days', 'Filing time')}{th('amount_low', 'Amount')}
              <th className="text-right">Price<div className="normal-case text-[10px]">at trade / disclosure</div></th>
              {th('return_pct', 'Performance', 'since trade / disclosure')}<th>Source</th>
            </tr></thead>
            <tbody>{trades.map((t, i) => (
              <tr key={i}>
                <td>{t.ticker ? <Link to={`/t/${t.ticker}`} className="font-mono font-semibold hover:underline">{t.ticker}</Link> : '—'}</td>
                <td className="max-w-[260px] truncate" title={t.asset}>{t.asset.replace(/\s*\([A-Z.]+\)\s*$/, '')}</td>
                <td><span className={t.tx_type === 'buy' ? 'text-up' : t.tx_type === 'sell' ? 'text-down' : 'text-warn'}>{t.tx_type === 'buy' ? 'Bought' : t.tx_type === 'sell' ? 'Sold' : 'Exchange'}</span>
                  {t.owner !== 'self' && <div><span className="label border border-line rounded px-1 normal-case">{t.owner}</span></div>}</td>
                <td className="font-mono text-xs">{t.tx_date}</td><td className="font-mono text-xs">{t.disclosure_date}</td>
                <td className="num font-semibold">{t.delay_days}d</td>
                <td className="num text-xs">{money(t.amount_low, t.amount_high)}</td>
                <td className="num text-right text-xs">{t.price_trade ? `$${fmt.num(t.price_trade)}` : '—'} / {t.price_disclosure ? `$${fmt.num(t.price_disclosure)}` : '—'}</td>
                <td className="text-xs">{t.return_pct !== null && t.return_pct !== undefined ? <>
                  <Pct v={t.return_pct} d={1} /> / <Pct v={t.copy_return_pct} d={1} />
                  <div className="text-[10px] text-muted">S&P: {fmt.pct(t.spy_pct, 1)} / {fmt.pct(t.copy_spy_pct, 1)}</div></> : <span className="text-muted">{t.tx_type === 'buy' ? 'no price data' : '—'}</span>}</td>
                <td><a className="text-xs text-muted hover:underline whitespace-nowrap" href={t.url} target="_blank" rel="noreferrer">House filing ↗</a></td>
              </tr>))}</tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}

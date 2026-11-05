import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import LineChart from '../components/LineChart.jsx'
import { Card, ErrorBox, Loading, Pct, Segmented, Tabs, fmt, useApi } from '../components/ui'

const PERIODS = [{ value: '30d', label: '30 days' }, { value: '90d', label: '90 days' }, { value: '6m', label: '6 months' }, { value: '1y', label: '1 yr' }, { value: 'all', label: 'all data' }]
const COLORS = ['#60a5fa', '#4ade80', '#f87171', '#a78bfa', '#2dd4bf', '#f472b6', '#fb923c', '#93c5fd', '#86efac', '#fde047']

export function PartyDot({ party }) {
  const c = party?.startsWith('Dem') ? '#60a5fa' : party?.startsWith('Rep') ? '#f87171' : '#a78bfa'
  return <span className="inline-block w-2 h-2 rounded-full" style={{ background: c }} title={party || 'unknown'} />
}

export function Initials({ name, size = 36, color }) {
  const ini = name.split(' ').filter((w) => /^[A-Z]/.test(w)).map((w) => w[0]).slice(0, 1).concat(name.split(' ').slice(-1).map((w) => w[0])).join('')
  return <span className="inline-grid place-items-center rounded-full font-semibold text-xs shrink-0" style={{ width: size, height: size, background: 'var(--panel2)', border: `2px solid ${color || 'var(--line)'}` }}>{ini}</span>
}

export const money = (lo, hi) => `${fmt.big(lo)}–${fmt.big(hi)}`
export const memberPath = (m) => `/congress/member/${encodeURIComponent(m)}`

function MemberSearch() {
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [res, setRes] = useState([])
  useEffect(() => {
    if (q.length < 2) { setRes([]); return }
    const t = setTimeout(() => api.get(`/congress/search?q=${encodeURIComponent(q)}`).then(setRes).catch(() => {}), 150)
    return () => clearTimeout(t)
  }, [q])
  return (
    <div className="relative w-full sm:w-96">
      <input className="input w-full font-mono" placeholder="Search members…" value={q} onChange={(e) => setQ(e.target.value)} />
      {res.length > 0 && <div className="absolute z-20 mt-1 w-full card max-h-72 overflow-auto">{res.map((m) => (
        <button key={m} className="w-full text-left px-3 py-2 text-sm hover:bg-panel2" onClick={() => nav(memberPath(m))}>{m}</button>))}</div>}
    </div>
  )
}

function Leaderboard({ period, setPeriod }) {
  const nav = useNavigate()
  const [rankBy, setRankBy] = useState('estimated')
  const [party, setParty] = useState('')
  const [chart, setChart] = useState(null)
  const { data, error, loading, reload } = useApi(`/congress/leaderboard?period=${period}&rank_by=${rankBy}${party ? `&party=${party}` : ''}`)
  const top = useMemo(() => (data ? data.rows.filter((r) => r.ranked).slice(0, 10).map((r) => r.member) : []), [data])
  const [hidden, setHidden] = useState(new Set())
  useEffect(() => {
    if (!top.length) return
    api.get(`/congress/performance?period=${period}&members=${encodeURIComponent(top.join('|'))}`).then(setChart).catch(() => setChart(null))
  }, [top, period])
  const series = useMemo(() => chart ? chart.series.filter((s) => !hidden.has(s.key)).map((s, i) => ({
    name: s.name, data: s.data, color: s.key === 'SPY' ? '#cbd5e1' : s.key === 'ALL' ? '#facc15' : COLORS[(chart.series.findIndex((x) => x.key === s.key) - 2) % COLORS.length],
    dashed: s.key === 'SPY',
  })) : null, [chart, hidden])
  const toggle = (k) => { const n = new Set(hidden); n.has(k) ? n.delete(k) : n.add(k); setHidden(n) }

  if (loading && !data) return <Loading text="Computing leaderboard (first load seeds ~11k disclosed trades)…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  const h = data.headline
  return (
    <div className="space-y-4">
      {h && (
        <div className="text-lg sm:text-xl font-semibold leading-snug max-w-3xl">
          <Link to={memberPath(h.member)} className="text-accent hover:underline">{h.member}</Link> made <span className="text-accent">{h.trades} trades</span> in this period.
          Copying their purchases when they were disclosed would have {h.copy_excess_pct >= 0 ? <span className="text-accent">outperformed</span> : <span className="text-down">underperformed</span>} the S&P 500 by <span className={h.copy_excess_pct >= 0 ? 'text-accent' : 'text-down'}>{Math.abs(h.copy_excess_pct).toFixed(0)}%</span> (hypothetical).
        </div>
      )}
      <p className="text-sm text-muted max-w-3xl">Members of Congress must disclose stock trades over $1,000 within 45 days (STOCK Act). MarketLens parses each House filing and measures disclosed purchases against the S&P 500.</p>
      <div className="flex flex-wrap gap-2 items-center">
        <Segmented label="Rank by" options={[{ value: 'estimated', label: "Member's estimated return" }, { value: 'copy', label: 'Copy on disclosure' }]} value={rankBy} onChange={setRankBy} />
        <select className="input py-1.5 text-xs font-mono" value={party} onChange={(e) => setParty(e.target.value)}>
          <option value="">All parties</option><option value="Dem">Democrats</option><option value="Rep">Republicans</option><option value="Ind">Independents</option>
        </select>
        <Segmented label="Period" options={PERIODS} value={period} onChange={setPeriod} />
      </div>
      <div className="card px-3 py-2 text-xs font-mono text-muted">
        <span className="text-accent">●</span> Filings through {fmt.date(data.as_of?.latest_filing)}, prices through {fmt.date(data.as_of?.prices_through)}{data.as_of?.source === 'sample-data' ? ' (sample data)' : ''}. {data.stats.members_traded} members traded in this period; {data.stats.members_ranked} have at least {data.stats.min_trades} priced purchases and are ranked. House only (Senate: TODO).
      </div>
      <Card title="Performance over time" subtitle="Amount-weighted return of each member's disclosed purchases from their trade dates. Dashed: S&P 500. Click a chip to hide or show a line.">
        <div className="flex flex-wrap gap-2 mb-3">
          {chart?.series.map((s, i) => (
            <button key={s.key} onClick={() => toggle(s.key)} className={`flex flex-col items-center gap-1 w-20 ${hidden.has(s.key) ? 'opacity-35' : ''}`}>
              <Initials name={s.key === 'SPY' ? 'S P' : s.key === 'ALL' ? 'All Congress' : s.name} size={40}
                color={s.key === 'SPY' ? '#cbd5e1' : s.key === 'ALL' ? '#facc15' : COLORS[(i - 2) % COLORS.length]} />
              <span className="text-[11px] truncate w-full text-center">{s.key === 'SPY' ? 'S&P 500' : s.name.split(' ').slice(-1)[0]}</span>
            </button>
          ))}
        </div>
        {series ? <LineChart series={series} height={380} percent /> : <Loading />}
      </Card>
      <div className="flex justify-center"><div className="card px-4 py-2 flex items-center gap-3"><span className="font-semibold">S&P 500</span><Pct v={data.sp500_pct} d={1} className="font-semibold" /><span className="text-xs text-muted">{PERIODS.find((p) => p.value === period)?.label}</span></div></div>
      <div className="card overflow-x-auto">
        <table className="data w-full text-sm">
          <thead><tr><th>#</th><th>Member</th><th>Trades</th><th>Filing time</th><th>Member's est. return<div className="normal-case text-[10px]">on trade date</div></th><th>Copy strategy<div className="normal-case text-[10px]">on disclosure date</div></th><th>Win rate vs S&P</th><th>Money traded</th><th /></tr></thead>
          <tbody>
            {data.all && (
              <tr className="bg-warn/5">
                <td>★</td><td><div className="font-semibold text-warn">All Congress</div><div className="text-xs text-muted">{data.all.members} members</div></td>
                <td className="num">{data.all.trades}</td><td className="num">{data.all.avg_filing_delay_days?.toFixed(0)}d</td>
                <td><Pct v={data.all.estimated_return_pct} d={1} /><div className="text-[11px] text-muted">S&P {fmt.pct(data.all.estimated_spy_pct, 1)}</div></td>
                <td><Pct v={data.all.copy_return_pct} d={1} /><div className="text-[11px] text-muted">S&P {fmt.pct(data.all.copy_spy_pct, 1)}</div></td>
                <td className="num">{data.all.win_rate_vs_spy_pct?.toFixed(0)}%<div className="text-[11px] text-muted">{data.all.priced_buys} priced buys</div></td>
                <td className="num text-xs">{money(data.all.money_low, data.all.money_high)}</td><td />
              </tr>
            )}
            {data.rows.map((r, i) => (
              <tr key={r.member} className={`cursor-pointer ${r.ranked ? '' : 'opacity-60'}`} onClick={() => nav(memberPath(r.member))}>
                <td className="text-muted">{r.ranked ? i + 1 : '—'}</td>
                <td><div className="flex items-center gap-2"><Initials name={r.member} size={30} /><div><div className="font-medium">{r.member}</div><div className="text-xs text-muted flex items-center gap-1.5"><PartyDot party={r.party} />{r.party || '—'} · {r.chamber} · {r.state_dst}</div></div></div></td>
                <td className="num">{r.trades}<div className="text-[11px] text-muted">{r.buys} buys · {r.sells} sells</div></td>
                <td className="num">{r.avg_filing_delay_days?.toFixed(0)}d</td>
                <td>{r.estimated_return_pct !== undefined ? <><Pct v={r.estimated_return_pct} d={1} /><div className="text-[11px] text-muted">S&P {fmt.pct(r.estimated_spy_pct, 1)}</div></> : <span className="text-xs text-muted">no priced buys</span>}</td>
                <td>{r.copy_return_pct !== undefined && r.copy_return_pct !== null ? <><Pct v={r.copy_return_pct} d={1} /><div className="text-[11px] text-muted">S&P {fmt.pct(r.copy_spy_pct, 1)}</div></> : '—'}</td>
                <td className="num">{r.win_rate_vs_spy_pct !== undefined ? `${r.win_rate_vs_spy_pct.toFixed(0)}%` : '—'}<div className="text-[11px] text-muted">{r.priced_buys} priced</div></td>
                <td className="num text-xs">{money(r.money_low, r.money_high)}</td>
                <td className="text-muted">›</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">{data.label}</p>
    </div>
  )
}

export function TradeRow({ t, showMember }) {
  const nav = useNavigate()
  return (
    <tr>
      {showMember && <td><button className="hover:underline text-left" onClick={() => nav(memberPath(t.member))}>{t.member}</button></td>}
      <td>{t.ticker ? <Link className="font-mono font-semibold hover:underline" to={`/t/${t.ticker}`}>{t.ticker}</Link> : <span className="text-muted">—</span>}<div className="text-[11px] text-muted max-w-[220px] truncate">{t.asset}</div></td>
      <td><span className={t.tx_type === 'buy' ? 'text-up' : t.tx_type === 'sell' ? 'text-down' : 'text-warn'}>{t.tx_type === 'buy' ? 'Bought' : t.tx_type === 'sell' ? 'Sold' : 'Exchange'}</span>{t.owner !== 'self' && <div className="label normal-case">{t.owner}</div>}</td>
      <td className="font-mono text-xs">{t.tx_date}</td><td className="font-mono text-xs">{t.disclosure_date}</td>
      <td className="num text-xs">{money(t.amount_low, t.amount_high)}</td>
      <td><a className="text-xs text-muted hover:underline" href={t.url} target="_blank" rel="noreferrer">House filing ↗</a></td>
    </tr>
  )
}

function Filings() {
  const { data, error, loading } = useApi('/congress/filings?days=30')
  const [open, setOpen] = useState(null)
  const nav = useNavigate()
  if (loading) return <Loading />
  if (error) return <ErrorBox error={error} />
  return (
    <Card title="Latest periodic transaction reports" subtitle="Each row is one filing (PDF on the House Clerk site). Scanned paper filings are linked but can't be parsed.">
      <div className="divide-y divide-line">
        {data.filings.map((f) => (
          <div key={f.doc_id} className="py-2">
            <div className="flex items-center gap-3 text-sm cursor-pointer" onClick={() => setOpen(open === f.doc_id ? null : f.doc_id)}>
              <span className="font-mono text-xs text-muted w-24">{f.filing_date}</span>
              <PartyDot party={f.party} />
              <button className="font-medium hover:underline" onClick={(e) => { e.stopPropagation(); nav(memberPath(f.member)) }}>{f.member}</button>
              <span className="text-xs text-muted">{f.state_dst}</span>
              <span className="ml-auto text-xs">{f.n_trades ? `${f.n_trades} trades` : <span className="text-muted">not parsed</span>}</span>
              <a className="text-xs text-muted hover:underline" href={f.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>PDF ↗</a>
            </div>
            {open === f.doc_id && f.trades.length > 0 && (
              <table className="data w-full text-sm mt-2"><tbody>{f.trades.map((t) => <TradeRow key={t.id} t={t} />)}</tbody></table>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}

function Stocks({ period }) {
  const { data, error, loading } = useApi(`/congress/stocks?period=${period === 'all' ? '1y' : period}`)
  if (loading) return <Loading />
  if (error) return <ErrorBox error={error} />
  return (
    <Card title="Most-traded stocks" subtitle="By number of members trading (trade date in period). Midpoints of disclosed ranges.">
      <div className="overflow-x-auto"><table className="data w-full text-sm">
        <thead><tr><th>Ticker</th><th>Members</th><th>Buys</th><th>Sells</th><th>Buy $ (mid)</th><th>Sell $ (mid)</th><th>Last trade</th><th>Who</th></tr></thead>
        <tbody>{data.rows.map((r) => (
          <tr key={r.ticker}><td><Link className="font-mono font-semibold hover:underline" to={`/t/${r.ticker}`}>{r.ticker}</Link><div className="text-[11px] text-muted max-w-[220px] truncate">{r.asset}</div></td>
            <td className="num">{r.members}</td><td className="num text-up">{r.buys}</td><td className="num text-down">{r.sells}</td>
            <td className="num">{fmt.big(r.buy_volume_mid)}</td><td className="num">{fmt.big(r.sell_volume_mid)}</td>
            <td className="font-mono text-xs">{r.last_trade}</td><td className="text-xs text-muted max-w-[260px] truncate">{r.member_names.join(', ')}</td></tr>))}</tbody>
      </table></div>
    </Card>
  )
}

function Following() {
  const { data, loading } = useApi('/congress/following')
  if (loading) return <Loading />
  if (!data.members.length) return <Card><div className="text-sm text-muted">You're not following anyone yet. Open a member and click <b>Follow</b>. Followed members' new filings appear here and in your daily briefing.</div></Card>
  return (
    <Card title={`Following ${data.members.length}`} subtitle={data.members.join(' · ')}>
      <table className="data w-full text-sm"><thead><tr><th>Member</th><th>Ticker</th><th>Action</th><th>Traded</th><th>Disclosed</th><th>Amount</th><th /></tr></thead>
        <tbody>{data.trades.map((t) => <TradeRow key={t.id} t={t} showMember />)}</tbody></table>
    </Card>
  )
}

export default function Congress() {
  const [tab, setTab] = useState('board')
  const [period, setPeriod] = useState('6m')
  const filings = useApi('/congress/filings?days=7')
  const [refreshing, setRefreshing] = useState('')
  const refresh = async () => { const r = await api.post('/congress/refresh'); setRefreshing(r.status === 'started' ? 'Fetching new House filings in the background… reload in a minute.' : r.status) }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">Congress Trades</h1>
        <div className="ml-auto flex items-center gap-2"><MemberSearch /><button className="btn" onClick={refresh}>↻ New filings</button></div>
      </div>
      {refreshing && <div className="text-xs text-muted">{refreshing}</div>}
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'board', label: 'Leaderboard' }, { id: 'filings', label: 'Filings', badge: filings.data ? `${filings.data.count_7d} in last 7d` : undefined },
        { id: 'stocks', label: 'Stocks' }, { id: 'following', label: 'Following' }]} />
      {tab === 'board' && <Leaderboard period={period} setPeriod={setPeriod} />}
      {tab === 'filings' && <Filings />}
      {tab === 'stocks' && <Stocks period={period} />}
      {tab === 'following' && <Following />}
    </div>
  )
}

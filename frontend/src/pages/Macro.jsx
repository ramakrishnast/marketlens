import { useMemo, useState } from 'react'
import LineChart from '../components/LineChart.jsx'
import { AsOf, Card, ErrorBox, Loading, Segmented, fmt, useApi } from '../components/ui'

const COLORS = { DFF: '#f59e0b', DGS10: '#60a5fa', DGS2: '#a78bfa', T10Y2Y: '#4ade80', CPIAUCSL: '#f87171', UNRATE: '#2dd4bf' }

function SeriesChart({ id, years }) {
  const { data, loading } = useApi(`/macro/${id}?years=${years}`)
  const series = useMemo(() => (data?.points?.length ? [{ name: data.label, data: data.points, color: COLORS[id] }] : null), [data, id])
  if (loading) return <Loading />
  return series ? <LineChart series={series} height={200} /> : <div className="text-sm text-muted">No data</div>
}

function RatesChart({ years }) {
  const a = useApi(`/macro/DGS10?years=${years}`)
  const b = useApi(`/macro/DGS2?years=${years}`)
  const c = useApi(`/macro/DFF?years=${years}`)
  const series = useMemo(() => [
    a.data && { name: '10Y', data: a.data.points, color: COLORS.DGS10 },
    b.data && { name: '2Y', data: b.data.points, color: COLORS.DGS2 },
    c.data && { name: 'Fed funds', data: c.data.points, color: COLORS.DFF },
  ].filter(Boolean), [a.data, b.data, c.data])
  return series.length ? <LineChart series={series} height={300} /> : <Loading />
}

export default function Macro() {
  const { data, error, loading, reload } = useApi('/macro')
  const [years, setYears] = useState('5')
  if (loading) return <Loading text="Loading FRED data…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-2xl font-bold">Macro</h1>
        <span className="text-xs text-muted">{data.source}. Without FRED_API_KEY, series load from FRED's public CSV; the release calendar needs the key.</span>
        <div className="ml-auto"><Segmented label="Range" options={[{ value: '1', label: '1Y' }, { value: '5', label: '5Y' }, { value: '10', label: '10Y' }]} value={years} onChange={setYears} /></div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        {data.series.map((s) => (
          <a key={s.id} href={s.url} target="_blank" rel="noreferrer" className="card p-3 hover:border-accent">
            <div className="label">{s.label}</div>
            <div className="text-2xl font-semibold num mt-1">{s.id === 'CPIAUCSL' ? `${fmt.num(s.yoy_pct)}%` : fmt.num(s.value)}</div>
            {s.id !== 'CPIAUCSL' && <div className="text-xs text-muted">1M {s.change_1m >= 0 ? '+' : ''}{fmt.num(s.change_1m)} · 1Y {s.change_1y >= 0 ? '+' : ''}{fmt.num(s.change_1y)}</div>}
            <div className="mt-1"><AsOf asOf={{ last_bar: s.date, source: s.source, fetched_at: s.fetched_at }} label="as of" /></div>
          </a>
        ))}
      </div>
      <div className="grid xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-4 items-start">
        <Card title="Rates: fed funds, 2-year and 10-year Treasury yields (%)"><RatesChart years={years} /></Card>
        <Card title="Upcoming macro events (45 days)">
          {data.events.length === 0 && <div className="text-sm text-muted">No events in the calendar. Add FRED_API_KEY for CPI/jobs/GDP release dates.</div>}
          <div className="divide-y divide-line">
            {data.events.map((e, i) => (
              <div key={i} className="py-2 text-sm"><span className="font-mono text-xs text-muted mr-2">{fmt.date(e.date)}</span>{e.url ? <a href={e.url} target="_blank" rel="noreferrer" className="hover:underline">{e.event}</a> : e.event}
                <div className="text-[11px] text-muted">{e.source}</div></div>))}
          </div>
        </Card>
      </div>
      <div className="grid md:grid-cols-3 gap-4">
        <Card title="Yield curve: 10Y minus 2Y (pp)" subtitle="Below zero = inverted"><SeriesChart id="T10Y2Y" years={years} /></Card>
        <Card title="CPI inflation, year over year (%)"><SeriesChart id="CPIAUCSL" years={years} /></Card>
        <Card title="Unemployment rate (%)"><SeriesChart id="UNRATE" years={years} /></Card>
      </div>
    </div>
  )
}

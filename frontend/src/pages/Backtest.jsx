import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { TickerSearch } from '../App.jsx'
import LineChart from '../components/LineChart.jsx'
import { Card, ErrorBox, Loading, fmt, useApi } from '../components/ui'

const METRIC_LABELS = {
  total_return_pct: ['Total return', (v) => fmt.pct(v)], cagr_pct: ['CAGR', (v) => fmt.pct(v)],
  sharpe: ['Sharpe', (v) => fmt.num(v)], sortino: ['Sortino', (v) => fmt.num(v)],
  max_drawdown_pct: ['Max drawdown', (v) => fmt.pct(v)], volatility_pct: ['Volatility (ann.)', (v) => fmt.pct(v, 1, false)],
  win_rate_pct: ['Win rate', (v) => (v === null || v === undefined ? '—' : `${v.toFixed(1)}%`)],
  profit_factor: ['Profit factor', (v) => fmt.num(v)], num_trades: ['Trades', (v) => v ?? '—'],
  exposure_pct: ['Exposure time', (v) => (v === null || v === undefined ? '—' : `${v.toFixed(1)}%`)],
  avg_trade_pct: ['Avg trade', (v) => fmt.pct(v)], fees_paid: ['Fees + commissions', (v) => fmt.big(v)],
  final_equity: ['Final equity', (v) => fmt.big(v)],
}

function Hypothetical() {
  return <div className="card p-2.5 text-sm border-warn/50 text-warn font-medium text-center">Hypothetical results. Past performance does not predict future results.</div>
}

function MetricsTable({ m, b }) {
  return (
    <table className="data w-full text-sm">
      <thead><tr><th>Metric</th><th>Strategy</th>{b && <th>SPY buy & hold</th>}</tr></thead>
      <tbody>
        {Object.entries(METRIC_LABELS).map(([k, [label, f]]) => (
          <tr key={k}><td className="text-muted">{label}</td><td className="num font-medium">{f(m[k])}</td>{b && <td className="num text-muted">{f(b[k])}</td>}</tr>
        ))}
      </tbody>
    </table>
  )
}

function heatColor(v, min, max) {
  if (v === null || v === undefined) return 'var(--panel2)'
  const t = max === min ? 0.5 : (v - min) / (max - min)
  const c = t >= 0.5 ? 'var(--up)' : 'var(--down)'
  return `color-mix(in srgb, ${c} ${Math.abs(t - 0.5) * 150 + 8}%, var(--panel))`
}

function Heatmap({ data }) {
  const vals = data.grid.flat().filter((v) => v !== null)
  const min = Math.min(...vals), max = Math.max(...vals)
  return (
    <div className="overflow-x-auto">
      <table className="text-xs font-mono">
        <thead><tr><th className="p-1 text-muted">{data.y_param} ↓ / {data.x_param} →</th>{data.x_values.map((x) => <th key={x} className="p-1 text-muted">{x}</th>)}</tr></thead>
        <tbody>
          {data.grid.map((row, j) => (
            <tr key={j}><td className="p-1 text-muted text-right">{data.y_values[j]}</td>
              {row.map((v, i) => {
                const best = data.best && data.best[data.x_param] === data.x_values[i] && data.best[data.y_param] === data.y_values[j]
                return <td key={i} className={`p-0.5`}><div className={`w-16 h-9 grid place-items-center rounded num ${best ? 'ring-2 ring-accent' : ''}`} style={{ background: heatColor(v, min, max) }}>{v === null ? '—' : v.toFixed(2)}</div></td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Backtest() {
  const { data: meta } = useApi('/backtest/presets')
  const [ticker, setTicker] = useState('AAPL')
  const [strategy, setStrategy] = useState('sma_cross')
  const [params, setParams] = useState({})
  const [code, setCode] = useState('')
  const [start, setStart] = useState('2018-01-01')
  const [end, setEnd] = useState('')
  const [initial, setInitial] = useState(10000)
  const [costs, setCosts] = useState({ commission_pct: 0.05, commission_fixed: 0, slippage_pct: 0.05 })
  const [result, setResult] = useState(null)
  const [sweep, setSweep] = useState(null)
  const [wf, setWf] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [metric, setMetric] = useState('sharpe')
  const preset = meta?.presets.find((p) => p.id === strategy)
  const sweepKeys = Object.keys(preset?.sweep || {})
  const [axes, setAxes] = useState({})

  useEffect(() => {
    if (!preset) return
    setParams({ ...preset.params })
    if (preset.id === 'custom' && !code) setCode(preset.template)
    const ks = Object.keys(preset.sweep || {})
    setAxes(ks.length >= 2 ? { x: ks[0], xv: preset.sweep[ks[0]].join(', '), y: ks[1], yv: preset.sweep[ks[1]].join(', ') } : {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy, meta])

  const body = () => ({
    ticker, strategy, params: Object.fromEntries(Object.entries(params).map(([k, v]) => [k, Number(v)])),
    start: start || null, end: end || null, initial: Number(initial),
    costs: { commission_pct: costs.commission_pct / 100, commission_fixed: Number(costs.commission_fixed), slippage_pct: costs.slippage_pct / 100 },
    code: strategy === 'custom' ? code : null,
  })
  const call = async (kind, path, extra = {}) => {
    setBusy(kind); setErr('')
    try {
      const r = await api.post(path, { ...body(), ...extra })
      if (kind === 'run') setResult(r)
      if (kind === 'sweep') setSweep(r)
      if (kind === 'wf') setWf(r)
    } catch (e) { setErr(e.message) }
    setBusy('')
  }
  const sweepArgs = () => ({
    x_param: axes.x, y_param: axes.y, metric,
    x_values: axes.xv.split(',').map((s) => Number(s.trim())).filter((n) => !Number.isNaN(n)),
    y_values: axes.yv.split(',').map((s) => Number(s.trim())).filter((n) => !Number.isNaN(n)),
  })

  const equitySeries = useMemo(() => result ? [
    { name: `${result.ticker} strategy`, data: result.equity, color: '#4ade80' },
    ...(result.benchmark ? [{ name: 'SPY buy & hold', data: result.benchmark.equity, color: '#94a3b8', dashed: true }] : []),
  ] : null, [result])
  const ddSeries = useMemo(() => result ? [{ name: 'Drawdown', data: result.drawdown, color: '#f87171', area: true }] : null, [result])
  const wfSeries = useMemo(() => wf ? [{ name: 'Walk-forward OOS equity', data: wf.stitched_equity, color: '#60a5fa' }] : null, [wf])

  if (!meta) return <Loading />
  return (
    <div className="grid lg:grid-cols-[340px_minmax(0,1fr)] gap-4 items-start">
      <Card title="Strategy builder">
        <div className="space-y-3 text-sm">
          <label className="block"><span className="label">Ticker</span>
            <div className="flex gap-2 items-center mt-1"><span className="font-mono font-semibold w-16">{ticker}</span><TickerSearch className="flex-1" placeholder="Change…" onPick={setTicker} /></div>
          </label>
          <label className="block"><span className="label">Strategy</span>
            <select className="input w-full mt-1" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {meta.presets.map((p) => <option key={p.id} value={p.id} disabled={p.enabled === false}>{p.label}</option>)}
            </select>
            <span className="text-xs text-muted">{preset?.description}</span>
          </label>
          {Object.keys(params).length > 0 && (
            <div className="grid grid-cols-3 gap-2">
              {Object.entries(params).map(([k, v]) => (
                <label key={k}><span className="label">{k}</span><input className="input w-full mt-1 num" value={v} onChange={(e) => setParams({ ...params, [k]: e.target.value })} /></label>
              ))}
            </div>
          )}
          {strategy === 'custom' && (
            <label className="block"><span className="label">Python strategy class</span>
              <textarea className="input w-full mt-1 font-mono text-xs h-64" spellCheck={false} value={code} onChange={(e) => setCode(e.target.value)} />
              <span className="text-xs text-muted">Available: <code>pd</code>, <code>np</code>, <code>ind</code> (sma, ema, rsi, macd, bollinger, atr). Runs locally in your container.</span>
            </label>
          )}
          <div className="grid grid-cols-2 gap-2">
            <label><span className="label">Start</span><input type="date" className="input w-full mt-1" value={start} onChange={(e) => setStart(e.target.value)} /></label>
            <label><span className="label">End</span><input type="date" className="input w-full mt-1" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label><span className="label">Initial $</span><input className="input w-full mt-1 num" value={initial} onChange={(e) => setInitial(e.target.value)} /></label>
            <label><span className="label">Commission %</span><input className="input w-full mt-1 num" value={costs.commission_pct} onChange={(e) => setCosts({ ...costs, commission_pct: e.target.value })} /></label>
            <label><span className="label">Fixed $ / fill</span><input className="input w-full mt-1 num" value={costs.commission_fixed} onChange={(e) => setCosts({ ...costs, commission_fixed: e.target.value })} /></label>
            <label><span className="label">Slippage %</span><input className="input w-full mt-1 num" value={costs.slippage_pct} onChange={(e) => setCosts({ ...costs, slippage_pct: e.target.value })} /></label>
          </div>
          <p className="text-xs text-muted">Signals use each day's close and fill at the <b>next day's open</b> (no look-ahead). Long/flat only. Benchmark: SPY buy-and-hold with the same costs.</p>
          <button className="btn btn-primary w-full py-2" disabled={!!busy} onClick={() => call('run', '/backtest/run')}>{busy === 'run' ? 'Running…' : 'Run backtest'}</button>
        </div>
      </Card>

      <div className="space-y-4 min-w-0">
        <Hypothetical />
        {err && <ErrorBox error={err} />}
        {!result && !busy && <Card><div className="text-sm text-muted">Pick a ticker and strategy, then <b>Run backtest</b>. Try SMA crossover on AAPL first.</div></Card>}
        {busy === 'run' && <Loading text="Running backtest…" />}
        {result && <>
          <Card title={`${result.ticker} · ${preset?.label} · ${result.start} → ${result.end}`} subtitle={`Params ${JSON.stringify(result.params)} · costs ${(result.costs.commission_pct * 100).toFixed(3)}% + $${result.costs.commission_fixed}/fill, slippage ${(result.costs.slippage_pct * 100).toFixed(3)}%`}>
            <LineChart series={equitySeries} height={320} />
            <div className="mt-3"><LineChart series={ddSeries} height={130} percent /></div>
          </Card>
          <div className="grid xl:grid-cols-[minmax(0,360px)_minmax(0,1fr)] gap-4 items-start">
            <Card title="Results vs benchmark"><MetricsTable m={result.metrics} b={result.benchmark?.metrics} /></Card>
            <Card title={`Trades (${result.trades.length})`}>
              <div className="overflow-auto max-h-[480px]">
                <table className="data w-full text-sm">
                  <thead><tr><th>Entry</th><th>Price</th><th>Exit</th><th>Price</th><th>Days</th><th>Return</th><th>P&L</th></tr></thead>
                  <tbody>{[...result.trades].reverse().map((t, i) => (
                    <tr key={i}><td className="font-mono text-xs">{t.entry_date}</td><td className="num">{fmt.num(t.entry_price)}</td>
                      <td className="font-mono text-xs">{t.exit_date || <span className="text-warn">open</span>}</td><td className="num">{t.exit_price ? fmt.num(t.exit_price) : '—'}</td>
                      <td className="num">{t.bars_held}</td><td className={`num ${t.return_pct >= 0 ? 'text-up' : 'text-down'}`}>{fmt.pct(t.return_pct)}</td>
                      <td className="num">{fmt.big(t.pnl)}</td></tr>))}</tbody>
                </table>
              </div>
            </Card>
          </div>
        </>}

        {axes.x && (
          <Card title="Parameter sweep & walk-forward" subtitle="Find out whether a result is robust or a lucky parameter choice.">
            <div className="flex flex-wrap gap-2 items-end text-sm mb-3">
              <label><span className="label">X: {axes.x}</span><input className="input w-44 mt-1 font-mono text-xs" value={axes.xv} onChange={(e) => setAxes({ ...axes, xv: e.target.value })} /></label>
              <label><span className="label">Y: {axes.y}</span><input className="input w-44 mt-1 font-mono text-xs" value={axes.yv} onChange={(e) => setAxes({ ...axes, yv: e.target.value })} /></label>
              <label><span className="label">Metric</span><select className="input mt-1" value={metric} onChange={(e) => setMetric(e.target.value)}>{meta.metrics.map((m) => <option key={m}>{m}</option>)}</select></label>
              {sweepKeys.length > 2 && <span className="text-xs text-muted">Other params fixed at the builder values.</span>}
              <button className="btn" disabled={!!busy} onClick={() => call('sweep', '/backtest/sweep', sweepArgs())}>{busy === 'sweep' ? 'Sweeping…' : 'Run sweep'}</button>
              <button className="btn" disabled={!!busy} onClick={() => call('wf', '/backtest/walkforward', sweepArgs())}>{busy === 'wf' ? 'Walking forward…' : 'Walk-forward test'}</button>
            </div>
            <div className="card p-2.5 text-xs border-warn/50 mb-3"><b className="text-warn">Overfitting warning:</b> {sweep?.warning || wf?.warning || 'Optimizing on past data tends to fit noise. Judge parameters on the out-of-sample (walk-forward) results, not the best cell of the heatmap.'}</div>
            {sweep && <div className="mb-4"><div className="label mb-2">{sweep.metric} by parameter (full period, in-sample) · best cell outlined</div><Heatmap data={sweep} /></div>}
            {wf && (
              <div className="space-y-3">
                <div className="label">Train/test split: optimized on {wf.split.train[0]} → {wf.split.train[1]}, tested on {wf.split.test[0]} → {wf.split.test[1]} with {JSON.stringify(wf.split.best_params)}</div>
                <table className="data text-sm">
                  <thead><tr><th>Metric</th><th>In-sample (train)</th><th>Out-of-sample (test)</th><th>SPY (test)</th></tr></thead>
                  <tbody>{['cagr_pct', 'sharpe', 'max_drawdown_pct', 'win_rate_pct', 'num_trades'].map((k) => (
                    <tr key={k}><td className="text-muted">{METRIC_LABELS[k][0]}</td><td className="num">{METRIC_LABELS[k][1](wf.split.in_sample[k])}</td>
                      <td className="num font-medium">{METRIC_LABELS[k][1](wf.split.out_of_sample[k])}</td><td className="num text-muted">{METRIC_LABELS[k][1](wf.oos_run.benchmark?.metrics[k])}</td></tr>))}</tbody>
                </table>
                {wf.split.degradation_pct !== null && wf.split.degradation_pct !== undefined && <p className={`text-sm ${wf.split.degradation_pct > 30 ? 'text-warn' : ''}`}>{wf.metric} {wf.split.degradation_pct >= 0 ? 'fell' : 'rose'} {Math.abs(wf.split.degradation_pct).toFixed(0)}% from in-sample to out-of-sample{wf.split.degradation_pct < 0 ? ' (one test window is a small sample; check the folds below)' : ''}.</p>}
                <div className="label">Anchored walk-forward ({wf.folds.length} folds, re-optimized before each)</div>
                <table className="data text-sm">
                  <thead><tr><th>Test window</th><th>Chosen params</th><th>In-sample {wf.metric}</th><th>OOS {wf.metric}</th><th>OOS return</th></tr></thead>
                  <tbody>{wf.folds.map((f, i) => (
                    <tr key={i}><td className="font-mono text-xs">{f.test[0]} → {f.test[1]}</td><td className="font-mono text-xs">{JSON.stringify(f.params)}</td>
                      <td className="num">{fmt.num(f.in_sample)}</td><td className="num">{fmt.num(f.out_of_sample)}</td><td><span className={f.oos_return_pct >= 0 ? 'text-up' : 'text-down'}>{fmt.pct(f.oos_return_pct)}</span></td></tr>))}</tbody>
                </table>
                <LineChart series={wfSeries} height={220} />
              </div>
            )}
          </Card>
        )}
      </div>
    </div>
  )
}

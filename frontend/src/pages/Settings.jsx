import { useState } from 'react'
import { api } from '../api'
import { Card, Loading, fmt, useApi } from '../components/ui'

const KEY_HELP = {
  OPENAI_API_KEY: ['AI summaries, Ask AI, Research Desk, AI briefing (gpt-6-luna)', 'https://platform.openai.com/api-keys'],
  SEC_USER_AGENT: ['Required by SEC EDGAR for fundamentals & filings: "Your Name you@example.com"', 'https://www.sec.gov/os/accessing-edgar-data'],
  FRED_API_KEY: ['Macro release calendar (series work without it)', 'https://fred.stlouisfed.org/docs/api/api_key.html'],
  FINNHUB_API_KEY: ['More company news (free tier)', 'https://finnhub.io/register'],
  ALPHA_VANTAGE_API_KEY: ['Alternative news source (free tier)', 'https://www.alphavantage.co/support/#api-key'],
  REDDIT_CLIENT_ID: ['Reddit posts in the Crowd tab (free "script" app)', 'https://www.reddit.com/prefs/apps'],
  SMTP_HOST: ['Email delivery of the daily briefing', null],
}

export default function Settings() {
  const status = useApi('/status')
  const data = useApi('/status/data')
  const [msg, setMsg] = useState('')
  const run = async (job) => { await api.post(`/status/run/${job}`); setMsg(`Started ${job} in the background.`) }
  if (status.loading) return <Loading />
  const s = status.data
  return (
    <div className="space-y-4 max-w-5xl">
      <h1 className="text-2xl font-bold">Status</h1>
      <div className="grid md:grid-cols-3 gap-3">
        <Card title="Data mode"><div className="font-mono text-lg">{s.data_mode}</div><p className="text-xs text-muted mt-1">auto = live with fallback to bundled sample data · live = live only · mock = sample only. Set DATA_MODE in .env.</p></Card>
        <Card title="AI"><div className="font-mono text-lg">{s.ai_enabled ? s.ai_model : 'off'}</div><p className="text-xs text-muted mt-1">{s.ai_enabled ? 'OpenAI key detected.' : 'Add OPENAI_API_KEY to .env and restart.'}</p></Card>
        <Card title="Sentiment model"><div className="font-mono text-lg">{s.sentiment_model}</div><p className="text-xs text-muted mt-1">{s.sentiment_model === 'finbert' ? 'FinBERT running locally.' : 'Lexicon scorer. Run make finbert for FinBERT (~2 GB).'}</p></Card>
      </div>
      <Card title="API keys (.env)">
        <table className="data w-full text-sm"><tbody>
          {Object.entries(s.keys).map(([k, ok]) => (
            <tr key={k}><td className="font-mono text-xs">{k}</td><td>{ok ? <span className="text-up">✓ set</span> : <span className="text-muted">not set</span>}</td>
              <td className="text-xs text-muted">{KEY_HELP[k]?.[0]}</td><td>{KEY_HELP[k]?.[1] && <a className="text-xs text-accent hover:underline" href={KEY_HELP[k][1]} target="_blank" rel="noreferrer">get key ↗</a>}</td></tr>))}
        </tbody></table>
      </Card>
      <Card title="Scheduled jobs" right={msg && <span className="text-xs text-accent">{msg}</span>}>
        {data.data?.jobs.length === 0 && <div className="text-sm text-muted">Scheduler is off (SCHEDULER_ENABLED=false).</div>}
        <table className="data w-full text-sm"><tbody>
          {data.data?.jobs.map((j) => (
            <tr key={j.id}><td>{j.name}</td><td className="font-mono text-xs">{j.next_run ? `next: ${fmt.datetime(j.next_run)}` : '—'}</td><td><button className="btn text-xs" onClick={() => run(j.id)}>Run now</button></td></tr>))}
        </tbody></table>
      </Card>
      <Card title="Incremental collection runs" subtitle={`Stored in ${data.data?.database?.backend} (${data.data?.database?.target}). Each scheduled job records what it added.`}
        right={<button className="btn text-xs" onClick={data.reload}>↻</button>}>
        {data.data?.runs.length === 0 && <div className="text-sm text-muted">No runs yet. They appear after a scheduled job runs, or click <b>Run now</b> above.</div>}
        {data.data?.runs.length > 0 && (
          <div className="overflow-x-auto"><table className="data w-full text-sm">
            <thead><tr><th>Job</th><th>Started</th><th>Status</th><th>Rows added</th><th>Detail</th></tr></thead>
            <tbody>{data.data.runs.map((r) => (
              <tr key={r.id}><td className="font-mono text-xs">{r.job}</td><td className="text-xs">{fmt.datetime(r.started_at)}</td>
                <td className={r.status === 'ok' ? 'text-up' : r.status === 'running' ? 'text-warn' : 'text-down'} title={r.error || ''}>{r.status}</td>
                <td className="num">{r.rows_added ?? 0}</td>
                <td className="text-xs text-muted">{r.error || Object.entries(r.detail || {}).map(([k, v]) => `${k} ${v > 0 ? '+' : ''}${v}`).join(' · ') || '—'}</td></tr>))}</tbody>
          </table></div>
        )}
        <div className="flex flex-wrap gap-x-5 gap-y-1 mt-3 text-xs text-muted font-mono">
          {Object.entries(data.data?.table_counts || {}).map(([k, v]) => <span key={k}>{k}: {v.toLocaleString()}</span>)}
        </div>
      </Card>
      <Card title="Cached data freshness">
        <table className="data w-full text-sm">
          <thead><tr><th>Kind</th><th>Items</th><th>Most recent fetch</th><th>Oldest fetch</th><th>Sources</th></tr></thead>
          <tbody>{data.data?.groups.map((g) => (
            <tr key={g.kind}><td className="font-mono text-xs">{g.kind}</td><td className="num">{g.count}</td><td className="text-xs">{fmt.datetime(g.latest)} ({fmt.ago(g.latest)})</td>
              <td className="text-xs">{fmt.datetime(g.oldest)}</td><td className="text-xs">{Object.entries(g.sources).map(([k, v]) => `${k}: ${v}`).join(' · ')}</td></tr>))}</tbody>
        </table>
      </Card>
      <p className="text-xs text-muted">{s.disclaimer}</p>
    </div>
  )
}

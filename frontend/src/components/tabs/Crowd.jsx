import { AsOf, Card, ErrorBox, Loading, Pct, fmt, useApi } from '../ui'
import { SentimentChip } from './News.jsx'

export default function Crowd({ ticker }) {
  const { data, error, loading, reload } = useApi(`/ticker/${encodeURIComponent(ticker)}/crowd`)
  if (loading) return <Loading text="Loading crowd chatter…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  const st = data.stocktwits_tags
  return (
    <div className="space-y-4">
      <div className="card p-3 text-sm border-warn/40 flex gap-2"><span className="text-warn">⚠</span><span>{data.label}</span></div>
      <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
        {data.mentions.length === 0 && <div className="card p-3 text-sm text-muted">Not in the top Reddit mentions right now.</div>}
        {data.mentions.map((m) => (
          <a key={m.platform} href={m.url} target="_blank" rel="noreferrer" className="card p-3 hover:border-accent">
            <div className="label">{m.label}</div>
            <div className="flex items-baseline gap-2 mt-1"><span className="text-2xl font-semibold">#{m.rank}</span>
              {m.rank_24h_ago && <span className="text-xs text-muted">was #{m.rank_24h_ago} 24h ago</span>}</div>
            <div className="text-sm mt-1">{m.mentions_24h} mentions / 24h {m.change_pct !== null && <Pct v={m.change_pct} d={0} />}</div>
            <div className="text-[11px] text-muted mt-1">ApeWisdom · {fmt.date(m.date)}</div>
          </a>
        ))}
        <div className="card p-3">
          <div className="label">StockTwits user tags</div>
          {st.tagged > 0 ? <>
            <div className="flex h-2.5 rounded overflow-hidden mt-2"><div className="bg-up" style={{ width: `${st.bullish_pct}%` }} /><div className="bg-down flex-1" /></div>
            <div className="flex justify-between text-sm mt-1"><span className="text-up">{st.bullish} bullish</span><span className="text-down">{st.bearish} bearish</span></div>
            <div className="text-[11px] text-muted mt-1">{st.tagged} of {st.total} recent posts tagged by their authors</div>
          </> : <div className="text-sm text-muted mt-1">No tagged posts.</div>}
        </div>
        <div className="card p-3">
          <div className="label">Text sentiment of posts</div>
          <div className="mt-2"><SentimentChip s={data.model_sentiment.score} /></div>
          <div className="text-[11px] text-muted mt-1">{data.model_sentiment.n} posts scored ({data.model_sentiment.scorer}). Sarcasm and slang fool every model.</div>
        </div>
      </div>
      <Card title="Recent posts" subtitle={`StockTwits${data.reddit_enabled ? ' + Reddit (r/wallstreetbets, r/stocks, r/investing, …)' : '. Add REDDIT_CLIENT_ID/SECRET to .env for Reddit posts.'}`}
        right={<AsOf asOf={data.as_of.posts} label="Fetched" />}>
        <div className="divide-y divide-line -mx-2">
          {data.posts.length === 0 && <div className="text-sm text-muted px-2">No recent posts.</div>}
          {data.posts.map((p) => (
            <a key={p.id} href={p.url} target="_blank" rel="noreferrer" className="flex gap-3 px-2 py-2 hover:bg-panel2">
              <div className="w-24 shrink-0 text-[11px] font-mono text-muted pt-0.5">
                <div>{p.community}</div>
                {p.user_label && <div className={p.user_label === 'Bullish' ? 'text-up' : 'text-down'}>{p.user_label}</div>}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm leading-snug break-words">{p.snippet}</div>
                <div className="text-[11px] text-muted mt-0.5">@{p.author} · {fmt.datetime(p.created_at)}{p.score ? ` · ${p.score} likes/points` : ''}</div>
              </div>
              <div className="pt-0.5"><SentimentChip s={p.sentiment} /></div>
            </a>
          ))}
        </div>
      </Card>
    </div>
  )
}

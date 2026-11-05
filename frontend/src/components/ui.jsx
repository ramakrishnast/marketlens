import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'

export function useApi(path, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: !!path })
  const seq = useRef(0)
  const load = useCallback(() => {
    if (!path) return
    const n = ++seq.current
    setState((s) => ({ ...s, loading: true, error: null }))
    api.get(path)
      .then((data) => n === seq.current && setState({ data, error: null, loading: false }))
      .catch((error) => n === seq.current && setState({ data: null, error: error.message, loading: false }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps])
  useEffect(() => { load() }, [load])
  return { ...state, reload: load }
}

export const fmt = {
  num(v, d = 2) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—'
    return Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })
  },
  pct(v, d = 2, sign = true) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—'
    return `${sign && v > 0 ? '+' : ''}${Number(v).toFixed(d)}%`
  },
  big(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—'
    const a = Math.abs(v)
    const s = v < 0 ? '-' : ''
    if (a >= 1e12) return `${s}$${(a / 1e12).toFixed(2)}T`
    if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`
    if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(1)}K`
    return `${s}$${a.toFixed(0)}`
  },
  vol(v) {
    if (!v) return '—'
    if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
    if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`
    return String(Math.round(v))
  },
  date(s) {
    if (!s) return '—'
    const d = new Date(s.length <= 10 ? s + 'T12:00:00' : s)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  },
  datetime(s) {
    if (!s) return '—'
    return new Date(s).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
  },
  ago(s) {
    if (!s) return ''
    const m = (Date.now() - new Date(s).getTime()) / 60000
    if (m < 1) return 'just now'
    if (m < 60) return `${Math.round(m)}m ago`
    if (m < 60 * 24) return `${Math.round(m / 60)}h ago`
    return `${Math.round(m / 1440)}d ago`
  },
}

export function Pct({ v, d = 2, className = '' }) {
  const cls = v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-muted'
  return <span className={`num ${cls} ${className}`}>{fmt.pct(v, d)}</span>
}

export function AsOf({ asOf, label = 'Data as of' }) {
  if (!asOf) return null
  const sample = asOf.source === 'sample-data'
  return (
    <span className="label inline-flex items-center gap-1.5 normal-case" title={`Fetched ${asOf.fetched_at || ''} · source: ${asOf.source || ''}`}>
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${sample ? 'bg-warn' : 'bg-accent'}`} />
      {label} {asOf.last_bar ? fmt.date(asOf.last_bar) : fmt.datetime(asOf.fetched_at)}
      {sample && <span className="text-warn">· sample data</span>}
    </span>
  )
}

export function Loading({ text = 'Loading…' }) {
  return <div className="text-muted text-sm py-6 animate-pulse">{text}</div>
}

export function ErrorBox({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="card p-3 border-down/50 text-sm flex items-center gap-3">
      <span className="text-down">⚠</span>
      <span className="flex-1">{error}</span>
      {onRetry && <button className="btn" onClick={onRetry}>Retry</button>}
    </div>
  )
}

export function Card({ title, right, children, className = '', subtitle }) {
  return (
    <section className={`card p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-start justify-between gap-3 mb-3">
          <div>
            {title && <h2 className="font-semibold">{title}</h2>}
            {subtitle && <p className="text-xs text-muted mt-0.5">{subtitle}</p>}
          </div>
          {right}
        </div>
      )}
      {children}
    </section>
  )
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="flex gap-1 border-b border-line overflow-x-auto">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={`px-3 py-2 text-sm font-mono whitespace-nowrap border-b-2 -mb-px ${value === t.id ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg'}`}>
          {t.label}{t.badge !== undefined && <span className="ml-1.5 text-xs text-muted">{t.badge}</span>}
        </button>
      ))}
    </div>
  )
}

export function Segmented({ options, value, onChange, label }) {
  return (
    <div className="inline-flex items-center border border-line rounded-lg overflow-hidden text-sm">
      {label && <span className="label px-2.5 py-1.5 bg-panel2 border-r border-line">{label}</span>}
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value
        const l = typeof o === 'string' ? o : o.label
        return (
          <button key={v} onClick={() => onChange(v)}
            className={`px-2.5 py-1.5 font-mono text-xs border-r border-line last:border-r-0 ${value === v ? 'bg-panel2 text-accent' : 'text-muted hover:text-fg'}`}>
            {l}
          </button>
        )
      })}
    </div>
  )
}

export function AIBadge() {
  return <span className="label border border-line rounded px-1.5 py-0.5 normal-case">AI-generated</span>
}

export function NeedsKey({ what = 'This feature' }) {
  return (
    <div className="card p-4 text-sm">
      <div className="font-semibold mb-1">Add an API key to enable AI</div>
      <p className="text-muted">{what} uses OpenAI (<code>gpt-6-luna</code>). Add <code className="font-mono">OPENAI_API_KEY=…</code> to your <code>.env</code> file and restart (<code className="font-mono">make run</code>). Everything else in MarketLens works without a key.</p>
    </div>
  )
}

export function Disclaimer({ children }) {
  return (
    <p className="text-xs text-muted border-l-2 border-warn pl-2">
      {children || 'Research only, not investment advice. Free data may be delayed or incomplete.'}
    </p>
  )
}

export function Markdown({ text }) {
  return <div className="prose-ml text-sm" dangerouslySetInnerHTML={{ __html: renderMarkdown(text || '') }} />
}

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*(?!\s)(.+?)\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/(^|[\s(])_(?!\s)([^_]+?)_(?=$|[\s).,;:])/g, '$1<em>$2</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
}

// Small, safe markdown renderer (escapes HTML first): headings, lists, tables, links, bold.
export function renderMarkdown(md) {
  const lines = md.replace(/\r/g, '').split('\n')
  const out = []
  let list = null
  let table = null
  const flush = () => {
    if (list) { out.push(`<${list.t}>${list.items.map((i) => `<li>${inline(i)}</li>`).join('')}</${list.t}>`); list = null }
    if (table) {
      const [head, ...rows] = table.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r))
      const cells = (r) => r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim())
      out.push(`<table><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`)
      table = null
    }
  }
  for (const line of lines) {
    let m
    if (/^\s*\|.*\|\s*$/.test(line)) { if (list) flush(); table = table || []; table.push(line); continue }
    if (table) flush()
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) { flush(); out.push(`<h${m[1].length + 1}>${inline(m[2])}</h${m[1].length + 1}>`); continue }
    if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { if (!list || list.t !== 'ul') { flush(); list = { t: 'ul', items: [] } } list.items.push(m[1]); continue }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (!list || list.t !== 'ol') { flush(); list = { t: 'ol', items: [] } } list.items.push(m[1]); continue }
    if (!line.trim()) { flush(); continue }
    flush()
    out.push(`<p>${inline(line)}</p>`)
  }
  flush()
  return out.join('\n')
}

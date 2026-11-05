import { useEffect, useRef, useState } from 'react'
import { NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api } from './api'
import Dashboard from './pages/Dashboard.jsx'
import Ticker from './pages/Ticker.jsx'
import { lazyPage } from './pages/lazy.jsx'

const Backtest = lazyPage(() => import('./pages/Backtest.jsx'))
const Chat = lazyPage(() => import('./pages/Chat.jsx'))
const Briefing = lazyPage(() => import('./pages/Briefing.jsx'))
const Macro = lazyPage(() => import('./pages/Macro.jsx'))
const Congress = lazyPage(() => import('./pages/Congress.jsx'))
const CongressMember = lazyPage(() => import('./pages/CongressMember.jsx'))
const Settings = lazyPage(() => import('./pages/Settings.jsx'))

const NAV = [
  ['/', 'Dashboard'],
  ['/congress', 'Congress Trades'],
  ['/backtest', 'Backtest'],
  ['/chat', 'Ask AI'],
  ['/briefing', 'Briefing'],
  ['/macro', 'Macro'],
  ['/settings', 'Status'],
]

function ThemeToggle() {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme || 'dark')
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('ml-theme', theme) } catch { /* ignore */ }
  }, [theme])
  return (
    <button className="btn font-mono text-xs" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title="Toggle light/dark">
      {theme === 'dark' ? '☾ dark' : '☀ light'}
    </button>
  )
}

export function TickerSearch({ onPick, placeholder = 'Search ticker or company…', autoFocus = false, className = '' }) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState([])
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const timer = useRef()
  useEffect(() => {
    clearTimeout(timer.current)
    if (!q.trim()) { setRes([]); return }
    timer.current = setTimeout(() => {
      api.get(`/search?q=${encodeURIComponent(q)}`).then((r) => { setRes(r); setHi(0) }).catch(() => setRes([]))
    }, 150)
  }, [q])
  const pick = (t) => { onPick(t); setQ(''); setRes([]); setOpen(false) }
  return (
    <div className={`relative ${className}`}>
      <input className="input w-full font-mono" value={q} autoFocus={autoFocus} placeholder={placeholder}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') setHi((h) => Math.min(h + 1, res.length - 1))
          if (e.key === 'ArrowUp') setHi((h) => Math.max(h - 1, 0))
          if (e.key === 'Enter') { const t = res[hi]?.ticker || q.trim().toUpperCase(); if (t) pick(t) }
          if (e.key === 'Escape') setOpen(false)
        }} />
      {open && res.length > 0 && (
        <div className="absolute z-30 mt-1 w-full card shadow-lg max-h-80 overflow-auto">
          {res.map((r, i) => (
            <button key={r.ticker} onMouseDown={() => pick(r.ticker)}
              className={`w-full text-left px-3 py-2 text-sm flex gap-3 ${i === hi ? 'bg-panel2' : ''}`}>
              <span className="font-mono font-semibold w-16">{r.ticker}</span>
              <span className="text-muted truncate">{r.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function App() {
  const nav = useNavigate()
  const [status, setStatus] = useState(null)
  const [menu, setMenu] = useState(false)
  useEffect(() => { api.get('/status').then(setStatus).catch(() => {}) }, [])
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-line bg-panel/80 backdrop-blur sticky top-0 z-20">
        <div className="max-w-[1400px] mx-auto px-4 h-14 flex items-center gap-4">
          <NavLink to="/" className="flex items-center gap-2 font-bold tracking-tight shrink-0">
            <svg width="22" height="22" viewBox="0 0 32 32"><path d="M5 22 L12 13 L17 18 L27 8" stroke="var(--accent)" strokeWidth="3.2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
            MarketLens
          </NavLink>
          <nav className="hidden lg:flex gap-1 text-sm">
            {NAV.map(([to, label]) => (
              <NavLink key={to} to={to} end={to === '/'}
                className={({ isActive }) => `px-2.5 py-1.5 rounded-md ${isActive ? 'bg-panel2 text-fg' : 'text-muted hover:text-fg'}`}>
                {label}
              </NavLink>
            ))}
          </nav>
          <TickerSearch className="flex-1 max-w-xs ml-auto" onPick={(t) => nav(`/t/${t}`)} />
          <ThemeToggle />
          <button className="btn lg:hidden" onClick={() => setMenu(!menu)} aria-label="Menu">☰</button>
        </div>
        {menu && (
          <nav className="lg:hidden border-t border-line px-4 py-2 flex flex-wrap gap-1 text-sm">
            {NAV.map(([to, label]) => (
              <NavLink key={to} to={to} end={to === '/'} onClick={() => setMenu(false)}
                className={({ isActive }) => `px-2.5 py-1.5 rounded-md ${isActive ? 'bg-panel2' : 'text-muted'}`}>{label}</NavLink>
            ))}
          </nav>
        )}
      </header>
      {status && status.data_mode === 'mock' && (
        <div className="bg-warn/15 text-warn text-xs text-center py-1 font-mono">
          DATA_MODE=mock · showing bundled sample data captured from the real sources
        </div>
      )}
      <main className="flex-1 max-w-[1400px] w-full mx-auto px-4 py-5">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/t/:ticker" element={<Ticker status={status} />} />
          <Route path="/congress" element={<Congress />} />
          <Route path="/congress/member/:name" element={<CongressMember />} />
          <Route path="/backtest" element={<Backtest />} />
          <Route path="/chat" element={<Chat status={status} />} />
          <Route path="/briefing" element={<Briefing status={status} />} />
          <Route path="/macro" element={<Macro />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
      <footer className="border-t border-line text-xs text-muted py-3 text-center px-4">
        MarketLens is a personal research tool. It never places trades and nothing here is investment advice.
        Free data is delayed, unofficial and rate-limited.
      </footer>
    </div>
  )
}

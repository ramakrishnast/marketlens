import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, createSeriesMarkers,
} from 'lightweight-charts'

export const OVERLAYS = [
  { id: 'sma20', label: 'SMA 20', color: '#60a5fa' },
  { id: 'sma50', label: 'SMA 50', color: '#f59e0b' },
  { id: 'sma200', label: 'SMA 200', color: '#e879f9' },
  { id: 'ema20', label: 'EMA 20', color: '#22d3ee', dashed: true },
  { id: 'ema50', label: 'EMA 50', color: '#fb923c', dashed: true },
  { id: 'ema200', label: 'EMA 200', color: '#c084fc', dashed: true },
  { id: 'bb', label: 'Bollinger', color: '#94a3b8' },
  { id: 'levels', label: 'Support/Resistance', color: '#94a3b8' },
]
export const PANES = [
  { id: 'rsi', label: 'RSI' },
  { id: 'macd', label: 'MACD' },
  { id: 'atr', label: 'ATR' },
]

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function useTheme() {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme)
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(document.documentElement.dataset.theme))
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => obs.disconnect()
  }, [])
  return theme
}

/**
 * Daily candles (+ indicators) or intraday bars (+ VWAP).
 * props: candles [{time, open, high, low, close, volume}], indicators {name: [{time, value}]}, levels, markers,
 *        enabled: Set of overlay/pane ids, intraday: {bars, vwap}
 */
export default function PriceChart({ candles, indicators = {}, levels = [], markers = [], enabled, height = 520, extraLines = [] }) {
  const ref = useRef(null)
  const theme = useTheme()
  useEffect(() => {
    if (!ref.current || !candles?.length) return
    const up = cssVar('--up'), down = cssVar('--down'), fg = cssVar('--muted'), line = cssVar('--line'), bg = cssVar('--panel')
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: bg }, textColor: fg, fontSize: 11, panes: { separatorColor: line } },
      grid: { vertLines: { color: line + '55' }, horzLines: { color: line + '55' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line, timeVisible: typeof candles[0].time === 'number', secondsVisible: false },
    })
    const price = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
    })
    price.setData(candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })))
    price.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } })

    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false })
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    vol.setData(candles.map((c) => ({ time: c.time, value: c.volume, color: (c.close >= c.open ? up : down) + '66' })))

    const addLine = (data, color, opts = {}, pane = 0) => {
      if (!data?.length) return null
      const s = chart.addSeries(LineSeries, { color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...opts }, pane)
      s.setData(data)
      return s
    }
    const on = (id) => enabled?.has(id)
    for (const o of OVERLAYS) {
      if (o.id.startsWith('sma') || o.id.startsWith('ema')) {
        if (on(o.id)) addLine(indicators[o.id], o.color, { lineStyle: o.dashed ? LineStyle.Dashed : LineStyle.Solid, title: o.label })
      }
    }
    for (const l of extraLines) addLine(l.data, l.color, { lineWidth: 2, title: l.title })
    if (on('bb')) {
      addLine(indicators.bb_upper, '#94a3b8', { lineStyle: LineStyle.Dotted })
      addLine(indicators.bb_middle, '#94a3b8', { lineStyle: LineStyle.Dashed, lineWidth: 1 })
      addLine(indicators.bb_lower, '#94a3b8', { lineStyle: LineStyle.Dotted })
    }
    if (on('levels')) {
      for (const l of levels) {
        price.createPriceLine({ price: l.price, color: l.kind === 'support' ? up : down, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: `${l.kind === 'support' ? 'S' : 'R'} ×${l.touches}` })
      }
    }
    if (markers?.length) createSeriesMarkers(price, markers)

    let pane = 1
    if (on('rsi') && indicators.rsi14) {
      const s = addLine(indicators.rsi14, '#a78bfa', { title: 'RSI 14', lastValueVisible: true }, pane)
      s.createPriceLine({ price: 70, color: down, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false })
      s.createPriceLine({ price: 30, color: up, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false })
      pane++
    }
    if (on('macd') && indicators.macd) {
      const h = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane)
      h.setData(indicators.macd_hist.map((p) => ({ ...p, color: (p.value >= 0 ? up : down) + 'aa' })))
      addLine(indicators.macd, '#60a5fa', { title: 'MACD' }, pane)
      addLine(indicators.macd_signal, '#f59e0b', { title: 'Signal' }, pane)
      pane++
    }
    if (on('atr') && indicators.atr14) {
      addLine(indicators.atr14, '#2dd4bf', { title: 'ATR 14', lastValueVisible: true }, pane)
      pane++
    }
    const panes = chart.panes()
    if (panes.length > 1) {
      panes[0].setStretchFactor(3)
      for (let i = 1; i < panes.length; i++) panes[i].setStretchFactor(1)
    }
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [candles, indicators, levels, markers, enabled, theme, extraLines])

  const extraPanes = ['rsi', 'macd', 'atr'].filter((p) => enabled?.has(p)).length
  return <div ref={ref} style={{ height: height + extraPanes * 120 }} className="w-full" />
}

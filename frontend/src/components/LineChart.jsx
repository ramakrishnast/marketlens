import { useEffect, useRef } from 'react'
import { AreaSeries, ColorType, LineSeries, LineStyle, createChart } from 'lightweight-charts'

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/** Multi-line time-series chart. series: [{name, data:[{time,value}], color, dashed, area}] */
export default function LineChart({ series, height = 320, percent = false }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current || !series?.length) return
    const line = cssVar('--line'), muted = cssVar('--muted'), bg = cssVar('--panel')
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: bg }, textColor: muted, fontSize: 11 },
      grid: { vertLines: { color: line + '55' }, horzLines: { color: line + '55' } },
      rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line },
      localization: percent ? { priceFormatter: (v) => `${v.toFixed(1)}%` } : undefined,
    })
    for (const s of series) {
      if (!s.data?.length) continue
      const opts = { color: s.color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: s.name,
        lineStyle: s.dashed ? LineStyle.Dashed : LineStyle.Solid }
      const ser = s.area
        ? chart.addSeries(AreaSeries, { ...opts, lineColor: s.color, topColor: s.color + '00', bottomColor: s.color + '55', invertFilledArea: true })
        : chart.addSeries(LineSeries, opts)
      ser.setData(s.data)
    }
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [series, percent])
  return <div ref={ref} style={{ height }} className="w-full" />
}

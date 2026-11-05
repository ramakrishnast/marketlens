import { Card, ErrorBox, Loading, useApi } from '../ui'
import { PartyDot, TradeRow } from '../../pages/Congress.jsx'

export default function CongressTab({ ticker }) {
  const { data, error, loading } = useApi(`/congress/ticker/${encodeURIComponent(ticker)}`)
  if (loading) return <Loading />
  if (error) return <ErrorBox error={error} />
  return (
    <Card title={`Congress trades in ${ticker}`} subtitle="House periodic transaction reports. Disclosures can lag trades by up to 45 days and don't reveal intent.">
      {data.trades.length === 0 ? <div className="text-sm text-muted">No disclosed House trades in {ticker} in the loaded filings.</div> : (
        <div className="overflow-x-auto"><table className="data w-full text-sm">
          <thead><tr><th>Member</th><th>Ticker</th><th>Action</th><th>Traded</th><th>Disclosed</th><th>Amount</th><th /></tr></thead>
          <tbody>{data.trades.map((t, i) => <TradeRow key={i} t={{ ...t, ticker, asset: `${t.chamber} · ${t.party || ''}` }} showMember />)}</tbody>
        </table></div>
      )}
      <p className="text-xs text-muted mt-2"><PartyDot party="Democrat" /> Dem <PartyDot party="Republican" /> Rep</p>
    </Card>
  )
}

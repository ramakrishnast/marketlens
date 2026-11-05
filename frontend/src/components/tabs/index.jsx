import Fundamentals from './Fundamentals.jsx'
import Filings from './Filings.jsx'
import Overview from './Overview.jsx'

// Ticker page tabs. Each feature adds its tab here.
export const TICKER_TABS = [
  { id: 'overview', label: 'Signals & Outlook', Component: Overview },
  { id: 'fundamentals', label: 'Fundamentals', Component: Fundamentals },
  { id: 'filings', label: 'Filings', Component: Filings },
]

import { Suspense, lazy } from 'react'
import { Loading } from '../components/ui'

export function lazyPage(loader) {
  const C = lazy(loader)
  return function LazyPage(props) {
    return <Suspense fallback={<Loading />}><C {...props} /></Suspense>
  }
}

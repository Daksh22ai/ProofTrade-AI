import axios from 'axios'

const BASE = '/api'

export const api = {
  signals:    ()             => axios.get(`${BASE}/signals`).then(r => r.data),
  analysis:   (sym)          => axios.get(`${BASE}/analysis/${sym}`).then(r => r.data),
  chart:      (sym, tf='1h') => axios.get(`${BASE}/chart/${sym}?tf=${tf}`).then(r => r.data),
  cvd:        (sym, mt)      => axios.get(`${BASE}/cvd/${sym}?market_type=${mt}`).then(r => r.data),
  market:     (sym)          => axios.get(`${BASE}/market/${sym}`).then(r => r.data),
  verify:     (sym)          => axios.get(`${BASE}/verify/${sym}`).then(r => r.data),
  deployment: ()             => axios.get(`${BASE}/deployment`).then(r => r.data),
}

export function subscribeSSE(onEvent) {
  const es = new EventSource('/api/stream')
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)) }
    catch(err) {}
  }
  return () => es.close()
}

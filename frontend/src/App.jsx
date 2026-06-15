import { useState } from 'react'
import Landing from './pages/Landing.jsx'
import Terminal from './pages/Terminal.jsx'
import Playbook from './pages/Playbook.jsx'

export default function App() {
  const [view, setView] = useState('landing')

  if (view === 'terminal') return <Terminal onBack={() => setView('landing')} />
  if (view === 'playbook') return <Playbook onBack={() => setView('landing')} />
  return <Landing onEnter={() => setView('terminal')} onPlaybook={() => setView('playbook')} />
}

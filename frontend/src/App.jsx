import { useState } from 'react';
import LoggingPanel from './components/LoggingPanel';
import LiveMapPanel from './components/LiveMapPanel';
import ReplayPanel from './components/ReplayPanel';
import AlgorithmPanel from './components/AlgorithmPanel';
import BenchmarkPanel from './components/BenchmarkPanel';
import ChecklistPanel from './components/ChecklistPanel';
import LiveFeedPanel from './components/LiveFeedPanel';

const PANELS = [
  { id: 'logging',   label: 'Logging' },
  { id: 'live-map',  label: 'Live Map' },
  { id: 'replay',    label: 'Replay' },
  { id: 'algorithm', label: 'Algorithm' },
  { id: 'benchmark', label: 'Benchmark' },
  { id: 'checklist', label: 'Pre-flight' },
  { id: 'live-feed', label: 'Live Feed' },
];

const PANEL_COMPONENTS = {
  'logging':   <LoggingPanel />,
  'live-map':  <LiveMapPanel />,
  'replay':    <ReplayPanel />,
  'algorithm': <AlgorithmPanel />,
  'benchmark': <BenchmarkPanel />,
  'checklist': <ChecklistPanel />,
  'live-feed': <LiveFeedPanel />,
};

export default function App() {
  const [active, setActive] = useState('logging');

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100 flex flex-col">
      <header className="bg-gray-950 border-b border-gray-800 px-6 py-3 flex items-center gap-6 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center font-bold text-white text-sm select-none">
            D
          </div>
          <span className="font-bold text-white text-lg tracking-tight">DRISHTI-NAV GCS</span>
        </div>
        <nav className="flex gap-1">
          {PANELS.map((p) => (
            <button
              key={p.id}
              onClick={() => setActive(p.id)}
              className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
                active === p.id
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
              }`}
            >
              {p.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="flex-1 overflow-auto">
        {PANEL_COMPONENTS[active]}
      </main>
    </div>
  );
}

import { useState } from 'react';

const API = 'http://localhost:8000';

const CHECK_META = {
  gps_fix:       { label: 'GPS Fix',         icon: '🛰' },
  heading:       { label: 'Heading',          icon: '🧭' },
  frame_counter: { label: 'Camera Stream',   icon: '📷' },
  tile_db:       { label: 'Tile Database',   icon: '🗺' },
  disk_space:    { label: 'Disk Space',      icon: '💾' },
  gsd_norm:      { label: 'GSD Normalisation', icon: '📐' },
};

const CHECK_ORDER = ['gps_fix', 'heading', 'frame_counter', 'tile_db', 'disk_space', 'gsd_norm'];

function CheckRow({ id, result }) {
  const meta = CHECK_META[id] ?? { label: id, icon: '•' };
  const pass = result?.pass;

  return (
    <div className={`flex items-start gap-4 px-5 py-3.5 rounded-lg border transition-colors
                     ${pass === true  ? 'border-green-800 bg-green-950/40'
                     : pass === false ? 'border-red-800 bg-red-950/40'
                     :                  'border-gray-800 bg-gray-800/40'}`}>
      {/* Pass/fail indicator */}
      <div className={`mt-0.5 w-6 h-6 rounded-full flex items-center justify-center shrink-0 text-sm font-bold
                       ${pass === true  ? 'bg-green-600 text-white'
                       : pass === false ? 'bg-red-600 text-white'
                       :                  'bg-gray-700 text-gray-400'}`}>
        {pass === true ? '✓' : pass === false ? '✗' : '?'}
      </div>

      {/* Label */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-200">{meta.label}</span>
        </div>
        {result && (
          <div className={`text-xs mt-0.5 font-mono
                           ${pass ? 'text-gray-400' : 'text-red-300'}`}>
            {result.message}
          </div>
        )}
      </div>

      {/* Numeric detail */}
      {result && (
        <div className="text-xs font-mono text-gray-500 text-right shrink-0 self-center">
          {id === 'gps_fix'       && result.satellites != null && `${result.satellites} sats`}
          {id === 'heading'       && result.heading_deg != null && `${result.heading_deg.toFixed(1)}°`}
          {id === 'frame_counter' && result.fps != null && `${result.fps} fps`}
          {id === 'tile_db'       && result.tile_count != null && `${result.tile_count} tiles`}
          {id === 'disk_space'    && result.free_gb != null && `${result.free_gb} GB`}
        </div>
      )}
    </div>
  );
}

export default function ChecklistPanel() {
  const [checks, setChecks]       = useState(null);
  const [loading, setLoading]     = useState(false);
  const [failDemo, setFailDemo]   = useState(false);
  const [error, setError]         = useState('');

  async function runChecks(demo = failDemo) {
    setLoading(true);
    setError('');
    try {
      const url = `${API}/logger/preflight${demo ? '?fail_demo=true' : ''}`;
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setChecks(await r.json());
    } catch (e) {
      setError(`Cannot reach backend: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  function handleToggleDemo(val) {
    setFailDemo(val);
    if (checks) runChecks(val);
  }

  const allPass  = checks && CHECK_ORDER.every((k) => checks[k]?.pass !== false);
  const failing  = checks ? CHECK_ORDER.filter((k) => checks[k]?.pass === false) : [];

  return (
    <div className="h-[calc(100vh-56px)] overflow-y-auto bg-gray-900 p-6">
      <div className="max-w-2xl mx-auto space-y-5">

        {/* ── Header ── */}
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-white tracking-wide">Pre-flight Checklist</h2>
            <p className="text-xs text-gray-500 mt-0.5">Run before arming. All checks must pass for GO status.</p>
          </div>

          {/* Simulate failures toggle */}
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <span className="text-xs text-gray-400">Simulate failures</span>
            <button
              role="switch"
              aria-checked={failDemo}
              onClick={() => handleToggleDemo(!failDemo)}
              className={`relative w-9 h-5 rounded-full transition-colors
                          ${failDemo ? 'bg-amber-600' : 'bg-gray-700'}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white
                                transition-transform ${failDemo ? 'translate-x-4' : 'translate-x-0'}`} />
            </button>
          </label>
        </div>

        {/* ── Run button ── */}
        <button
          onClick={() => runChecks()}
          disabled={loading}
          className="w-full py-3 bg-blue-600 hover:bg-blue-500 disabled:opacity-40
                     rounded-lg text-sm font-bold text-white transition-colors tracking-wide"
        >
          {loading ? 'Running checks…' : checks ? '↻  Re-run Pre-flight Check' : '▶  Run Pre-flight Check'}
        </button>

        {error && (
          <div className="px-4 py-3 bg-red-950/60 border border-red-800 rounded-lg text-sm text-red-300">
            {error}
          </div>
        )}

        {/* ── Check rows ── */}
        {checks && (
          <div className="space-y-2">
            {CHECK_ORDER.map((id) => (
              <CheckRow key={id} id={id} result={checks[id]} />
            ))}
          </div>
        )}

        {/* ── GO / NO-GO verdict ── */}
        {checks && (
          <div className={`rounded-xl border-2 p-6 text-center transition-colors
                           ${allPass
                             ? 'border-green-600 bg-green-950/50'
                             : 'border-red-600 bg-red-950/50'}`}>

            {/* Badge */}
            <div className={`inline-block px-8 py-3 rounded-full text-3xl font-black tracking-widest mb-3
                             ${allPass ? 'bg-green-600 text-white' : 'bg-red-600 text-white'}`}>
              {allPass ? 'GO' : 'NO-GO'}
            </div>

            {allPass ? (
              <p className="text-green-300 font-semibold text-sm mt-1">
                All checks passed — safe to arm
              </p>
            ) : (
              <div className="mt-1 space-y-1">
                <p className="text-red-300 font-semibold text-sm">
                  {failing.length} check{failing.length > 1 ? 's' : ''} failed — do not arm
                </p>
                <ul className="text-xs text-red-400 space-y-0.5">
                  {failing.map((k) => (
                    <li key={k}>
                      ✗ {CHECK_META[k]?.label ?? k}: {checks[k]?.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Placeholder before first run */}
        {!checks && !error && (
          <div className="text-center text-gray-600 text-sm py-8">
            Press the button above to run checks
          </div>
        )}
      </div>
    </div>
  );
}

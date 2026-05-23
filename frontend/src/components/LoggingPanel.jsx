import { useState, useEffect, useRef } from 'react';

const API = 'http://localhost:8000';

function hdopColor(hdop) {
  if (hdop <= 1.0) return 'text-green-400';
  if (hdop <= 2.0) return 'text-yellow-400';
  return 'text-red-400';
}

function hdopBarColor(hdop) {
  if (hdop <= 1.0) return 'bg-green-400';
  if (hdop <= 2.0) return 'bg-yellow-400';
  return 'bg-red-500';
}

function hdopLabel(hdop) {
  if (hdop <= 1.0) return 'Excellent';
  if (hdop <= 2.0) return 'Good';
  return 'Poor';
}

function CompassRose({ heading }) {
  const rad = (heading * Math.PI) / 180;
  const nx = 20 + 13 * Math.sin(rad);
  const ny = 20 - 13 * Math.cos(rad);
  const sx = 20 - 7 * Math.sin(rad);
  const sy = 20 + 7 * Math.cos(rad);

  return (
    <svg width="44" height="44" viewBox="0 0 44 44">
      <circle cx="22" cy="22" r="20" fill="none" stroke="#374151" strokeWidth="1.5" />
      <text x="22" y="8"  textAnchor="middle" fontSize="7" fill="#6B7280" fontFamily="monospace">N</text>
      <text x="22" y="40" textAnchor="middle" fontSize="7" fill="#6B7280" fontFamily="monospace">S</text>
      <text x="8"  y="25" textAnchor="middle" fontSize="7" fill="#6B7280" fontFamily="monospace">W</text>
      <text x="37" y="25" textAnchor="middle" fontSize="7" fill="#6B7280" fontFamily="monospace">E</text>
      {/* red north needle */}
      <line x1="22" y1="22" x2={nx} y2={ny} stroke="#EF4444" strokeWidth="2.5" strokeLinecap="round" />
      {/* white south needle */}
      <line x1="22" y1="22" x2={sx} y2={sy} stroke="#9CA3AF" strokeWidth="2" strokeLinecap="round" />
      <circle cx="22" cy="22" r="2.5" fill="#4B5563" />
    </svg>
  );
}

function defaultSessionName() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return (
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  );
}

const DISK_TOTAL_MB = 48_000;

export default function LoggingPanel() {
  const [host, setHost]         = useState('192.168.1.100');
  const [user, setUser]         = useState('pi');
  const [keyPath, setKeyPath]   = useState('~/.ssh/id_rsa');
  const [sessionName, setSession] = useState(defaultSessionName);
  const [altitude, setAltitude] = useState(80);

  const [connected, setConnected]   = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [running, setRunning]       = useState(false);
  const [connError, setConnError]   = useState('');

  const [status, setStatus] = useState(null);
  const sseRef = useRef(null);

  useEffect(() => {
    const es = new EventSource(`${API}/logger/status`);
    sseRef.current = es;
    es.onmessage = (e) => {
      try { setStatus(JSON.parse(e.data)); } catch { /* ignore */ }
    };
    es.onerror = () => { /* backend not up yet — silently retry */ };
    return () => es.close();
  }, []);

  async function handleConnect() {
    setConnecting(true);
    setConnError('');
    try {
      const res = await fetch(`${API}/logger/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host, user, key_path: keyPath }),
      });
      if (res.ok) {
        setConnected(true);
      } else {
        setConnError('Connection failed.');
      }
    } catch {
      setConnError('Cannot reach backend.');
    } finally {
      setConnecting(false);
    }
  }

  async function handleStart() {
    const res = await fetch(`${API}/logger/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ altitude_m: Number(altitude), session_name: sessionName }),
    });
    if (res.ok) setRunning(true);
  }

  async function handleStop() {
    const res = await fetch(`${API}/logger/stop`, { method: 'POST' });
    if (res.ok) setRunning(false);
  }

  const diskPct = status
    ? Math.max(0, Math.min(100, Math.round((status.disk_mb_remaining / DISK_TOTAL_MB) * 100)))
    : 100;

  const diskBarColor =
    diskPct > 30 ? 'bg-blue-500' : diskPct > 10 ? 'bg-yellow-500' : 'bg-red-500';

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <h2 className="text-xl font-bold text-white tracking-wide">Logging Control</h2>

      {/* ── RPi Connection ── */}
      <section className="bg-gray-800 rounded-xl p-5 space-y-4">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest">
          RPi Connection
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {[
            { label: 'Host IP',      value: host,    set: setHost,    placeholder: '192.168.x.x' },
            { label: 'Username',     value: user,    set: setUser,    placeholder: 'pi' },
            { label: 'SSH Key Path', value: keyPath, set: setKeyPath, placeholder: '~/.ssh/id_rsa' },
          ].map(({ label, value, set, placeholder }) => (
            <div key={label}>
              <label className="text-xs text-gray-400 block mb-1">{label}</label>
              <input
                className="w-full bg-gray-700 rounded px-3 py-2 text-sm text-white font-mono
                           focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
                value={value}
                placeholder={placeholder}
                onChange={(e) => set(e.target.value)}
                disabled={connected}
              />
            </div>
          ))}
        </div>
        <div className="flex items-center gap-4">
          <button
            onClick={handleConnect}
            disabled={connected || connecting}
            className={`px-5 py-2 rounded text-sm font-semibold transition-colors ${
              connected
                ? 'bg-green-800 text-green-300 cursor-default'
                : 'bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50'
            }`}
          >
            {connected ? '✓ Connected' : connecting ? 'Connecting…' : 'Connect'}
          </button>
          {connError && <span className="text-sm text-red-400">{connError}</span>}
        </div>
      </section>

      {/* ── Session Config ── */}
      <section className="bg-gray-800 rounded-xl p-5 space-y-4">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest">
          Session Config
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-gray-400 block mb-1">Session Name</label>
            <input
              className="w-full bg-gray-700 rounded px-3 py-2 text-sm text-white font-mono
                         focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
              value={sessionName}
              onChange={(e) => setSession(e.target.value)}
              disabled={running}
            />
          </div>
          <div>
            <label className="text-xs text-gray-400 block mb-1">Altitude (m)</label>
            <input
              type="number"
              min={10}
              max={500}
              className="w-full bg-gray-700 rounded px-3 py-2 text-sm text-white
                         focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
              value={altitude}
              onChange={(e) => setAltitude(e.target.value)}
              disabled={running}
            />
          </div>
        </div>
        <div className="flex gap-3">
          <button
            onClick={handleStart}
            disabled={!connected || running}
            className="px-6 py-2 bg-emerald-600 hover:bg-emerald-500
                       disabled:opacity-40 disabled:cursor-not-allowed
                       rounded text-sm font-semibold text-white transition-colors"
          >
            ▶ Start
          </button>
          <button
            onClick={handleStop}
            disabled={!running}
            className="px-6 py-2 bg-red-700 hover:bg-red-600
                       disabled:opacity-40 disabled:cursor-not-allowed
                       rounded text-sm font-semibold text-white transition-colors"
          >
            ■ Stop
          </button>
        </div>
      </section>

      {/* ── Live Status ── */}
      <section className="bg-gray-800 rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-3">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest">
            Live Status
          </h3>
          {running && (
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
              <span className="text-xs text-red-400 font-semibold">REC</span>
            </span>
          )}
          {!status && (
            <span className="text-xs text-gray-600">waiting for backend…</span>
          )}
        </div>

        {status && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {/* Frames captured */}
            <div className="bg-gray-700 rounded-lg p-4">
              <div className="text-xs text-gray-400 mb-1">Frames</div>
              <div className="text-2xl font-mono font-bold text-white tabular-nums">
                {status.frames_captured.toLocaleString()}
              </div>
            </div>

            {/* GPS HDOP */}
            <div className="bg-gray-700 rounded-lg p-4">
              <div className="text-xs text-gray-400 mb-1">GPS HDOP</div>
              <div className={`text-2xl font-mono font-bold tabular-nums ${hdopColor(status.gps_quality)}`}>
                {status.gps_quality.toFixed(2)}
              </div>
              <div className={`mt-2 w-full h-1.5 rounded-full ${hdopBarColor(status.gps_quality)}`} />
              <div className="flex justify-between mt-1">
                <span className="text-xs text-gray-500">{hdopLabel(status.gps_quality)}</span>
                <span className="text-xs text-gray-500">{status.fix_count} sats</span>
              </div>
            </div>

            {/* Heading */}
            <div className="bg-gray-700 rounded-lg p-4 flex flex-col items-center gap-1">
              <div className="text-xs text-gray-400 self-start mb-1">Heading</div>
              <CompassRose heading={status.heading_deg} />
              <div className="text-sm font-mono text-white tabular-nums">
                {status.heading_deg.toFixed(1)}°
              </div>
            </div>

            {/* Disk */}
            <div className="bg-gray-700 rounded-lg p-4">
              <div className="text-xs text-gray-400 mb-1">Disk Free</div>
              <div className="text-2xl font-mono font-bold text-white tabular-nums">
                {(status.disk_mb_remaining / 1024).toFixed(1)}
                <span className="text-base font-normal text-gray-400"> GB</span>
              </div>
              <div className="mt-2 w-full bg-gray-600 rounded-full h-2">
                <div
                  className={`h-2 rounded-full transition-all duration-500 ${diskBarColor}`}
                  style={{ width: `${diskPct}%` }}
                />
              </div>
              <div className="text-xs text-gray-500 mt-1">{diskPct}% free</div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

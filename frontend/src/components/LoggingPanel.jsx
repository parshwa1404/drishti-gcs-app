import { useState, useEffect, useRef } from 'react';

const API = 'http://localhost:8000';

function hdopColor(hdop) {
  if (hdop == null) return 'text-gray-500';
  if (hdop <= 1.0) return 'text-green-400';
  if (hdop <= 2.0) return 'text-yellow-400';
  return 'text-red-400';
}

function hdopBarColor(hdop) {
  if (hdop == null) return 'bg-gray-600';
  if (hdop <= 1.0) return 'bg-green-400';
  if (hdop <= 2.0) return 'bg-yellow-400';
  return 'bg-red-500';
}

function hdopLabel(hdop) {
  if (hdop == null) return 'n/a';
  if (hdop <= 1.0) return 'Excellent';
  if (hdop <= 2.0) return 'Good';
  return 'Poor';
}

function fmtClock(ms) {
  if (!ms) return '—';
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

const CONN_STYLES = {
  'connected':          ['border-green-700 text-green-400', 'bg-green-400', false],
  'reconnecting':       ['border-amber-700 text-amber-400', 'bg-amber-400', true],
  'waiting for logger': ['border-gray-600 text-gray-400',   'bg-gray-500',  true],
  'error':              ['border-red-700 text-red-400',     'bg-red-500',   false],
};

function ConnBadge({ status }) {
  if (!status) return null;
  const [cls, dot, pulse] = CONN_STYLES[status] || ['border-gray-600 text-gray-400', 'bg-gray-500', false];
  return (
    <span className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold
                      bg-gray-900/70 border ${cls}`}>
      <span className={`w-2 h-2 rounded-full ${dot} ${pulse ? 'animate-pulse' : ''}`} />
      {status}
    </span>
  );
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
  const [host, setHost]         = useState('100.64.53.20');
  const [user, setUser]         = useState('pi');
  const [authMode, setAuthMode] = useState('password'); // 'password' | 'key'
  const [keyPath, setKeyPath]   = useState('~/.ssh/id_rsa');
  const [password, setPassword] = useState('');
  const [sessionName, setSession] = useState(defaultSessionName);
  const [altitude, setAltitude] = useState(80);

  const [connected, setConnected]   = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [running, setRunning]       = useState(false);
  const [connError, setConnError]   = useState('');

  const [status, setStatus] = useState(null);
  const sseRef = useRef(null);

  // Restore connection state when panel remounts after a tab switch
  useEffect(() => {
    fetch(`${API}/logger/state`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.connected) {
          setConnected(true);
          if (data.host) setHost(data.host);
        }
      })
      .catch(() => {});
  }, []);

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
        body: JSON.stringify({
          host,
          user,
          key_path: authMode === 'key' ? keyPath : '',
          password: authMode === 'password' ? password : '',
        }),
      });
      if (res.ok) {
        setConnected(true);
      } else {
        const err = await res.json().catch(() => ({}));
        setConnError(err.detail || 'Connection failed.');
      }
    } catch {
      setConnError('Cannot reach backend.');
    } finally {
      setConnecting(false);
    }
  }

  async function handleStart() {
    setConnError('');
    const res = await fetch(`${API}/logger/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ altitude_m: Number(altitude), session_name: sessionName }),
    });
    if (res.ok) {
      setRunning(true);
    } else {
      const err = await res.json().catch(() => ({}));
      setConnError(err.detail || 'Failed to start recording.');
    }
  }

  async function handleStop() {
    const res = await fetch(`${API}/logger/stop`, { method: 'POST' });
    if (res.ok) setRunning(false);
  }

  const diskPct = status && status.disk_mb_remaining != null
    ? Math.max(0, Math.min(100, Math.round((status.disk_mb_remaining / DISK_TOTAL_MB) * 100)))
    : null;

  const diskBarColor =
    diskPct == null ? 'bg-gray-600'
      : diskPct > 30 ? 'bg-blue-500'
      : diskPct > 10 ? 'bg-yellow-500'
      : 'bg-red-500';

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <h2 className="text-xl font-bold text-white tracking-wide">Logging Control</h2>

      {/* ── RPi Connection ── */}
      <section className="bg-gray-800 rounded-xl p-5 space-y-4">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest">
          RPi Connection
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="text-xs text-gray-400 block mb-1">Host IP</label>
            <input
              className="w-full bg-gray-700 rounded px-3 py-2 text-sm text-white font-mono
                         focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
              value={host}
              placeholder="192.168.x.x"
              onChange={(e) => setHost(e.target.value)}
              disabled={connected}
            />
          </div>
          <div>
            <label className="text-xs text-gray-400 block mb-1">Username</label>
            <input
              className="w-full bg-gray-700 rounded px-3 py-2 text-sm text-white font-mono
                         focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
              value={user}
              placeholder="pi"
              onChange={(e) => setUser(e.target.value)}
              disabled={connected}
            />
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs text-gray-400">
                {authMode === 'key' ? 'SSH Key Path' : 'Password'}
              </label>
              {!connected && (
                <button
                  type="button"
                  onClick={() => setAuthMode(m => m === 'key' ? 'password' : 'key')}
                  className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                >
                  use {authMode === 'key' ? 'password' : 'key'} instead
                </button>
              )}
            </div>
            {authMode === 'key' ? (
              <input
                className="w-full bg-gray-700 rounded px-3 py-2 text-sm text-white font-mono
                           focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
                value={keyPath}
                placeholder="~/.ssh/id_rsa"
                onChange={(e) => setKeyPath(e.target.value)}
                disabled={connected}
              />
            ) : (
              <input
                type="password"
                className="w-full bg-gray-700 rounded px-3 py-2 text-sm text-white font-mono
                           focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
                value={password}
                placeholder="password"
                onChange={(e) => setPassword(e.target.value)}
                disabled={connected}
              />
            )}
          </div>
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
          <ConnBadge status={status?.connection_status} />
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
                {(status.frames_captured ?? 0).toLocaleString()}
              </div>
            </div>

            {/* GPS HDOP */}
            <div className="bg-gray-700 rounded-lg p-4">
              <div className="text-xs text-gray-400 mb-1">GPS HDOP</div>
              <div className={`text-2xl font-mono font-bold tabular-nums ${hdopColor(status.gps_quality)}`}>
                {status.gps_quality != null ? status.gps_quality.toFixed(2) : '—'}
              </div>
              <div className={`mt-2 w-full h-1.5 rounded-full ${hdopBarColor(status.gps_quality)}`} />
              <div className="flex justify-between mt-1">
                <span className="text-xs text-gray-500">{hdopLabel(status.gps_quality)}</span>
                <span className="text-xs text-gray-500">
                  {status.fix_count != null ? `${status.fix_count} sats` : '—'}
                </span>
              </div>
            </div>

            {/* Heading */}
            <div className="bg-gray-700 rounded-lg p-4 flex flex-col items-center gap-1">
              <div className="text-xs text-gray-400 self-start mb-1">Heading</div>
              <CompassRose heading={status.heading_deg ?? 0} />
              <div className="text-sm font-mono text-white tabular-nums">
                {status.heading_deg != null ? `${status.heading_deg.toFixed(1)}°` : '—'}
              </div>
            </div>

            {/* Disk */}
            <div className="bg-gray-700 rounded-lg p-4">
              <div className="text-xs text-gray-400 mb-1">Disk Free</div>
              <div className="text-2xl font-mono font-bold text-white tabular-nums">
                {status.disk_mb_remaining != null
                  ? <>{(status.disk_mb_remaining / 1024).toFixed(1)}<span className="text-base font-normal text-gray-400"> GB</span></>
                  : '—'}
              </div>
              <div className="mt-2 w-full bg-gray-600 rounded-full h-2">
                <div
                  className={`h-2 rounded-full transition-all duration-500 ${diskBarColor}`}
                  style={{ width: `${diskPct ?? 0}%` }}
                />
              </div>
              <div className="text-xs text-gray-500 mt-1">
                {diskPct != null ? `${diskPct}% free` : 'n/a'}
              </div>
            </div>
          </div>
        )}

        {/* Per-frame fields from the RPi logger (timestamps.csv) */}
        {status && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              ['Altitude', status.altitude_m != null ? `${status.altitude_m.toFixed(1)} m` : '—'],
              ['Latitude', status.lat != null ? status.lat.toFixed(6) : '—'],
              ['Longitude', status.lon != null ? status.lon.toFixed(6) : '—'],
              ['Last frame', fmtClock(status.unix_ms ?? status.timestamp_ms)],
            ].map(([label, value]) => (
              <div key={label} className="bg-gray-700/60 rounded-lg px-3 py-2">
                <div className="text-xs text-gray-400 mb-0.5">{label}</div>
                <div className="text-sm font-mono font-semibold text-gray-100 tabular-nums">{value}</div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

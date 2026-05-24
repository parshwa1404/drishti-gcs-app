import { useState, useEffect, useRef, useCallback } from 'react';
import { MapContainer, TileLayer, Polyline, CircleMarker, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const API = 'http://localhost:8000';

const ESRI_IMAGERY = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  attribution: 'Tiles &copy; Esri',
  maxZoom: 19,
};

const MAX_TRACK = 500;
const SPARK_LEN  = 60;

// ─── Compass rose (same as LoggingPanel) ─────────────────────────────────────

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
      <line x1="22" y1="22" x2={nx} y2={ny} stroke="#EF4444" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="22" y1="22" x2={sx} y2={sy} stroke="#9CA3AF" strokeWidth="2"   strokeLinecap="round" />
      <circle cx="22" cy="22" r="2.5" fill="#4B5563" />
    </svg>
  );
}

// ─── Sparkline ────────────────────────────────────────────────────────────────

function Sparkline({ data, color = '#3b82f6', height = 36 }) {
  if (data.length < 2) return <div style={{ height }} className="w-full" />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 160;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  });
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none"
         className="w-full">
      <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

// ─── Map: auto-pan on new GPS fix, go grey when lost ─────────────────────────

function TrackController({ trackPoints, lastPos }) {
  const map  = useMap();
  const init = useRef(false);

  useEffect(() => {
    if (!init.current && trackPoints.length > 0) {
      map.setView([trackPoints[0].lat, trackPoints[0].lon], 16);
      init.current = true;
    }
  }, [trackPoints, map]);

  useEffect(() => {
    if (lastPos && init.current) {
      map.panTo([lastPos.lat, lastPos.lon], { animate: true, duration: 0.4 });
    }
  }, [lastPos, map]);

  return null;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function hdopColor(v) {
  if (v == null) return 'text-gray-500';
  if (v <= 1.0)  return 'text-green-400';
  if (v <= 2.0)  return 'text-amber-400';
  return 'text-red-400';
}

function fmtDuration(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

function StatRow({ label, value }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-gray-700 last:border-0">
      <span className="text-xs text-gray-500">{label}</span>
      <span className="text-xs font-mono text-gray-100 tabular-nums">{value ?? '—'}</span>
    </div>
  );
}

// ─── Main panel ──────────────────────────────────────────────────────────────

export default function LiveFeedPanel() {
  const [telemetry, setTelemetry]       = useState(null);
  const [connected, setConnected]       = useState(false);
  const [wifiStrength, setWifiStrength] = useState('lost');
  const [altHistory, setAltHistory]     = useState([]);
  const [gsHistory, setGsHistory]       = useState([]);
  const [trackPoints, setTrackPoints]   = useState([]);
  const [countdown, setCountdown]       = useState(0);
  const sseRef        = useRef(null);
  const countdownRef  = useRef(null);
  const lastPosRef    = useRef(null);

  const startCountdown = useCallback((secs) => {
    setCountdown(secs);
    if (countdownRef.current) clearInterval(countdownRef.current);
    countdownRef.current = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) { clearInterval(countdownRef.current); return 0; }
        return c - 1;
      });
    }, 1000);
  }, []);

  useEffect(() => {
    const es = new EventSource(`${API}/telemetry/status`);
    sseRef.current = es;

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (!data.connected) {
          setConnected(false);
          setWifiStrength('lost');
          startCountdown(5);
          return;
        }
        setConnected(true);
        setWifiStrength(data.wifi_strength ?? 'strong');
        setTelemetry(data);

        if (data.altitude_m != null)
          setAltHistory((h) => [...h.slice(-(SPARK_LEN - 1)), data.altitude_m]);
        if (data.groundspeed_ms != null)
          setGsHistory((h) => [...h.slice(-(SPARK_LEN - 1)), data.groundspeed_ms]);

        if (data.lat != null && data.lon != null) {
          const pt = { lat: data.lat, lon: data.lon };
          lastPosRef.current = pt;
          setTrackPoints((pts) => {
            const next = [...pts, pt];
            return next.length > MAX_TRACK ? next.slice(next.length - MAX_TRACK) : next;
          });
        }
      } catch { /* ignore parse errors */ }
    };

    es.onerror = () => {};
    return () => { es.close(); if (countdownRef.current) clearInterval(countdownRef.current); };
  }, [startCountdown]);

  const statusPill = connected
    ? wifiStrength === 'weak'
      ? { label: 'WEAK SIGNAL', cls: 'bg-amber-700 text-amber-100' }
      : { label: 'CONNECTED',   cls: 'bg-green-700 text-green-100' }
    : { label: 'LOST',          cls: 'bg-red-800 text-red-100' };

  const battery   = telemetry?.battery_pct ?? null;
  const batColor  = battery == null ? 'bg-gray-600'
    : battery > 50 ? 'bg-green-500'
    : battery > 25 ? 'bg-amber-500'
    : 'bg-red-500';

  const trackLine = trackPoints.map((p) => [p.lat, p.lon]);
  const lastPos   = trackPoints.length > 0 ? trackPoints[trackPoints.length - 1] : null;

  return (
    <div className="h-[calc(100vh-56px)] flex flex-col bg-gray-900 overflow-hidden">

      {/* ── Status bar ── */}
      <div className="shrink-0 bg-gray-950 border-b border-gray-800 px-4 py-2.5
                      flex items-center gap-4 flex-wrap">
        <span className={`px-4 py-1 rounded-full font-bold text-sm tracking-widest ${statusPill.cls}`}>
          {statusPill.label}
        </span>

        {telemetry?.session_duration_s != null && (
          <span className="text-xs text-gray-400 font-mono tabular-nums">
            Session: <span className="text-white">{fmtDuration(telemetry.session_duration_s)}</span>
          </span>
        )}

        {!connected && countdown > 0 && (
          <span className="text-xs text-red-400 font-mono">
            Reconnecting in {countdown} s…
          </span>
        )}

        {wifiStrength === 'strong' && (
          <span className="ml-auto text-xs text-green-400 font-mono">▂▄▆█ Strong</span>
        )}
        {wifiStrength === 'weak' && (
          <span className="ml-auto text-xs text-amber-400 font-mono">▂▄__ Weak</span>
        )}
      </div>

      {/* ── Two-column body + map ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex flex-1 min-h-0 overflow-hidden">

          {/* Left column — flight state */}
          <div className="w-[30%] border-r border-gray-800 overflow-y-auto p-4 space-y-5">

            {/* Altitude */}
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">Altitude</div>
              <div className="text-3xl font-bold font-mono text-white tabular-nums">
                {telemetry?.altitude_m != null ? telemetry.altitude_m.toFixed(1) : '—'}
                <span className="text-base font-normal text-gray-500 ml-1">m</span>
              </div>
              <Sparkline data={altHistory} color="#3b82f6" height={40} />
            </div>

            {/* Groundspeed */}
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">Groundspeed</div>
              <div className="text-2xl font-bold font-mono text-white tabular-nums">
                {telemetry?.groundspeed_ms != null ? telemetry.groundspeed_ms.toFixed(1) : '—'}
                <span className="text-sm font-normal text-gray-500 ml-1">m/s</span>
              </div>
              <Sparkline data={gsHistory} color="#10b981" height={32} />
            </div>

            {/* Heading */}
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">Heading</div>
              <div className="flex items-center gap-3">
                <CompassRose heading={telemetry?.heading_deg ?? 0} />
                <span className="text-xl font-mono text-white tabular-nums">
                  {telemetry?.heading_deg != null ? `${telemetry.heading_deg.toFixed(1)}°` : '—'}
                </span>
              </div>
            </div>

            {/* Battery */}
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">
                Battery
                {battery != null && (
                  <span className={`ml-2 font-mono font-bold ${battery > 50 ? 'text-green-400' : battery > 25 ? 'text-amber-400' : 'text-red-400'}`}>
                    {battery.toFixed(1)}%
                  </span>
                )}
              </div>
              <div className="w-full h-3 bg-gray-700 rounded overflow-hidden">
                <div
                  className={`h-full transition-all duration-500 ${batColor}`}
                  style={{ width: `${battery ?? 0}%` }}
                />
              </div>
            </div>
          </div>

          {/* Right column — logger state */}
          <div className="w-[25%] border-r border-gray-800 overflow-y-auto p-4">
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-3">Logger State</div>

            <StatRow label="Frames captured" value={telemetry?.frames_captured?.toLocaleString()} />
            <StatRow
              label="Disk free"
              value={telemetry?.disk_free_gb != null ? `${telemetry.disk_free_gb.toFixed(2)} GB` : null}
            />
            <div className="flex items-center justify-between py-1.5 border-b border-gray-700">
              <span className="text-xs text-gray-500">GPS HDOP</span>
              <span className={`text-xs font-mono tabular-nums font-semibold ${hdopColor(telemetry?.gps_hdop)}`}>
                {telemetry?.gps_hdop != null ? telemetry.gps_hdop.toFixed(2) : '—'}
              </span>
            </div>
            <StatRow label="Satellites"    value={telemetry?.gps_satellites} />
            <StatRow
              label="Session duration"
              value={telemetry?.session_duration_s != null ? fmtDuration(telemetry.session_duration_s) : null}
            />

            {/* Disk bar */}
            {telemetry?.disk_free_gb != null && (
              <div className="mt-4">
                <div className="text-xs text-gray-500 mb-1">Disk free</div>
                <div className="w-full h-2 bg-gray-700 rounded overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all duration-500"
                    style={{ width: `${Math.min(100, (telemetry.disk_free_gb / 12) * 100)}%` }}
                  />
                </div>
              </div>
            )}
          </div>

          {/* Map — 45% */}
          <div className="flex-1 relative">
            {!connected && (
              <div className="absolute inset-0 z-[2000] bg-gray-900/60 flex items-center justify-center">
                <span className="bg-gray-800 border border-gray-600 text-gray-400
                                 text-sm font-medium px-4 py-2 rounded-lg">
                  Last known position
                </span>
              </div>
            )}
            <MapContainer
              center={[19.9175, 73.8278]}
              zoom={16}
              style={{ height: '100%', width: '100%', filter: connected ? 'none' : 'grayscale(80%)' }}
            >
              <TileLayer url={ESRI_IMAGERY.url} attribution={ESRI_IMAGERY.attribution} maxZoom={ESRI_IMAGERY.maxZoom} />

              {trackLine.length > 1 && (
                <Polyline positions={trackLine} pathOptions={{ color: '#3b82f6', weight: 2, opacity: 0.8 }} />
              )}
              {lastPos && (
                <CircleMarker
                  center={[lastPos.lat, lastPos.lon]}
                  radius={8}
                  pathOptions={{ color: connected ? '#3b82f6' : '#6b7280', fillColor: connected ? '#3b82f6' : '#6b7280', fillOpacity: 0.9, weight: 2 }}
                />
              )}

              <TrackController trackPoints={trackPoints} lastPos={lastPos} />
            </MapContainer>
          </div>
        </div>
      </div>
    </div>
  );
}

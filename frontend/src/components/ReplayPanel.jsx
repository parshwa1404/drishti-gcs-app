import { useState, useEffect, useRef, useCallback } from 'react';
import { MapContainer, TileLayer, Polyline, CircleMarker, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const API = 'http://localhost:8000';

const ESRI_IMAGERY = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  attribution: 'Tiles &copy; Esri',
  maxZoom: 19,
};

const RATES = [0.25, 0.5, 1, 2, 4];

function fmtTs(ms) {
  if (!ms) return '—';
  return new Date(ms).toISOString().replace('T', ' ').slice(0, 23);
}

function fmtCoord(v, pos, neg) {
  if (v == null) return '—';
  return `${Math.abs(v).toFixed(6)}° ${v >= 0 ? pos : neg}`;
}

function blank(v, fmt) {
  return v == null ? '—' : fmt(v);
}

// ── Map helpers ───────────────────────────────────────────────────────────────

function MapController({ gpsTrack, currentPos }) {
  const map = useMap();
  const fitted = useRef(false);
  useEffect(() => {
    if (gpsTrack.length > 1 && !fitted.current) {
      map.fitBounds(gpsTrack.map((p) => [p.lat, p.lon]), { padding: [30, 30] });
      fitted.current = true;
    }
  }, [gpsTrack, map]);
  useEffect(() => {
    if (currentPos && fitted.current) {
      map.panTo([currentPos.lat, currentPos.lon], { animate: true, duration: 0.3 });
    }
  }, [currentPos, map]);
  return null;
}

// ── Toast ──────────────────────────────────────────────────────────────────
function Toast({ message, onDismiss }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4000);
    return () => clearTimeout(t);
  }, [onDismiss]);
  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[9999]
                    bg-red-900 border border-red-600 text-red-200
                    px-5 py-3 rounded-lg shadow-xl text-sm font-medium">
      {message}
    </div>
  );
}

// ── Session dropdown ──────────────────────────────────────────────────────────

function SessionDropdown({ onOpen, disabled }) {
  const [open, setOpen]       = useState(false);
  const [sessions, setSessions] = useState(null);
  const [loading, setLoading] = useState(false);
  const [manualPath, setManualPath] = useState('');

  async function fetchSessions() {
    setLoading(true);
    try {
      const r = await fetch(`${API}/replay/sessions`);
      setSessions(r.ok ? await r.json() : []);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }

  async function choose(path, sessionId) {
    setOpen(false);
    const r = await fetch(`${API}/replay/open/${encodeURIComponent(sessionId)}?path=${encodeURIComponent(path)}`, { method: 'POST' });
    if (!r.ok) return;
    onOpen(await r.json(), path);
  }

  async function chooseManual() {
    if (!manualPath.trim()) return;
    const sid = manualPath.trim().replace(/\\/g, '/').split('/').filter(Boolean).at(-1) || 'session';
    const r = await fetch(`${API}/replay/open/${encodeURIComponent(sid)}?path=${encodeURIComponent(manualPath.trim())}`, { method: 'POST' });
    if (r.ok) { setOpen(false); onOpen(await r.json(), manualPath.trim()); }
  }

  return (
    <div className="relative">
      <button
        onClick={() => { setOpen(!open); if (!open && sessions === null) fetchSessions(); }}
        disabled={disabled}
        className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40
                   rounded text-sm font-semibold text-white transition-colors"
      >
        {open ? 'Sessions ✕' : 'Select Session ▾'}
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-1 z-50 bg-gray-900 border border-gray-700
                        rounded-xl shadow-2xl w-[420px]">
          {/* Manual path */}
          <div className="px-3 py-2.5 border-b border-gray-700 flex gap-2">
            <input
              className="flex-1 bg-gray-800 text-xs font-mono text-gray-200 px-2 py-1.5
                         rounded focus:outline-none focus:ring-1 focus:ring-indigo-500"
              placeholder="/path/to/session"
              value={manualPath}
              onChange={(e) => setManualPath(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && chooseManual()}
            />
            <button
              onClick={chooseManual}
              className="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-xs text-white rounded"
            >Load</button>
          </div>
          <div className="max-h-72 overflow-y-auto">
            {loading ? (
              <div className="px-4 py-4 text-sm text-gray-500 animate-pulse">Scanning sessions…</div>
            ) : !sessions || sessions.length === 0 ? (
              <div className="px-4 py-4 text-sm text-gray-500">
                No sessions found. Set <span className="font-mono text-gray-400">SESSIONS_ROOT</span> in backend/.env
              </div>
            ) : sessions.map((s) => (
              <div
                key={s.session_id}
                onClick={() => choose(s.path, s.session_id)}
                className="px-4 py-2.5 border-b border-gray-800 last:border-0 hover:bg-gray-800
                           cursor-pointer transition-colors"
              >
                <div className="text-sm font-mono text-gray-200">{s.session_id}</div>
                <div className="text-xs text-gray-500 mt-0.5 truncate">{s.path}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function ReplayPanel() {
  const [session, setSession]     = useState(null);  // { session_id, total_rows, has_overlay, unix_ms_start, unix_ms_end }
  const [sessionPath, setSessionPath] = useState('');
  const [row, setRow]             = useState(0);
  const [frame, setFrame]         = useState(null);  // ReplayRecord from API
  const [frameLoading, setFrameLoading] = useState(false);
  const [playing, setPlaying]     = useState(false);
  const [rate, setRate]           = useState(1);
  const [toast, setToast]         = useState('');
  const [gpsTrack, setGpsTrack]   = useState([]);

  // JPEG URL cache: row → object URL (±20 window)
  const jpegCache = useRef({});
  const playRef   = useRef(null);
  // For timestamp-driven playback
  const playStateRef = useRef({ startRow: 0, startWall: 0, startMs: 0, rate: 1, session: null });

  // ── Frame loading ─────────────────────────────────────────────────────────

  const loadFrame = useCallback(async (targetRow, sid) => {
    const sessionId = sid || session?.session_id;
    if (!sessionId) return;
    setFrameLoading(true);
    try {
      const r = await fetch(`${API}/replay/${encodeURIComponent(sessionId)}/frame/${targetRow}`);
      if (!r.ok) { setFrame(null); return; }
      const data = await r.json();
      setFrame(data);
      if (data.lat != null && data.lon != null) {
        setGpsTrack((prev) => {
          if (prev.some((p) => p.row === targetRow)) return prev;
          return [...prev, { row: targetRow, lat: data.lat, lon: data.lon }].sort((a, b) => a.row - b.row);
        });
      }
    } catch {
      setFrame(null);
    } finally {
      setFrameLoading(false);
    }
  }, [session]);

  useEffect(() => {
    if (session) loadFrame(row);
  }, [row, session]); // eslint-disable-line react-hooks/exhaustive-deps

  // JPEG lazy cache: pre-load ±20 frames
  const scheduleJpegs = useCallback((centerRow) => {
    if (!session) return;
    const sid = session.session_id;
    const lo = Math.max(0, centerRow - 20);
    const hi = Math.min(session.total_rows - 1, centerRow + 20);
    // Evict far entries
    for (const k of Object.keys(jpegCache.current)) {
      const n = Number(k);
      if (n < lo || n > hi) { URL.revokeObjectURL(jpegCache.current[k]); delete jpegCache.current[k]; }
    }
    for (let r = lo; r <= hi; r++) {
      if (jpegCache.current[r]) continue;
      fetch(`${API}/replay/${encodeURIComponent(sid)}/jpeg/${r}`)
        .then((res) => res.ok ? res.blob() : null)
        .then((blob) => { if (blob) jpegCache.current[r] = URL.createObjectURL(blob); })
        .catch(() => {});
    }
  }, [session]);

  useEffect(() => { scheduleJpegs(row); }, [row, session]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Playback ──────────────────────────────────────────────────────────────

  const stopPlay = useCallback(() => {
    if (playRef.current) { clearInterval(playRef.current); playRef.current = null; }
    setPlaying(false);
  }, []);

  const startPlay = useCallback(() => {
    if (playRef.current || !session || !frame) return;
    playStateRef.current = {
      startRow: row,
      startWall: Date.now(),
      startMs: frame.unix_ms,
      rate,
      session,
    };
    playRef.current = setInterval(() => {
      const st = playStateRef.current;
      if (!st.session) return;
      const wallElapsed = (Date.now() - st.startWall) * st.rate;
      const targetMs = st.startMs + wallElapsed;
      // Find closest row by ts
      fetch(`${API}/replay/${encodeURIComponent(st.session.session_id)}/seek?ts_ms=${Math.round(targetMs)}`)
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          if (!data) return;
          const newRow = data.row;
          if (newRow >= st.session.total_rows - 1) { stopPlay(); setRow(st.session.total_rows - 1); return; }
          setRow(newRow);
        })
        .catch(() => {});
    }, 100);
    setPlaying(true);
  }, [session, frame, row, rate, stopPlay]);

  useEffect(() => () => { if (playRef.current) clearInterval(playRef.current); }, []);

  // Update rate in playStateRef without restarting
  useEffect(() => { playStateRef.current.rate = rate; }, [rate]);

  // ── Keyboard nav ──────────────────────────────────────────────────────────

  useEffect(() => {
    if (!session) return;
    const handler = (e) => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); stopPlay(); setRow((r) => Math.max(0, r - 1)); }
      if (e.key === 'ArrowRight') { e.preventDefault(); stopPlay(); setRow((r) => Math.min(session.total_rows - 1, r + 1)); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [session, stopPlay]);

  // ── Jump to low-inlier ────────────────────────────────────────────────────

  async function jumpLowInlier(dir) {
    if (!session) return;
    stopPlay();
    const r = await fetch(`${API}/replay/${encodeURIComponent(session.session_id)}/next-low-inlier?from=${row}&dir=${dir}`);
    if (!r.ok) return;
    const data = await r.json();
    if (data.row != null) setRow(data.row);
  }

  // ── Session open ──────────────────────────────────────────────────────────

  function handleSessionOpen(info, path) {
    stopPlay();
    setSession(info);
    setSessionPath(path);
    setRow(0);
    setFrame(null);
    setGpsTrack([]);
    jpegCache.current = {};
    // Immediately load frame 0
    loadFrame(0, info.session_id);
  }

  // ── Render ────────────────────────────────────────────────────────────────

  const trackLine = gpsTrack.map((p) => [p.lat, p.lon]);
  const currentPos = frame?.lat != null ? { lat: frame.lat, lon: frame.lon } : null;

  const metaRows = frame ? [
    ['Timestamp',    fmtTs(frame.unix_ms)],
    ['Lat',          fmtCoord(frame.lat, 'N', 'S')],
    ['Lon',          fmtCoord(frame.lon, 'E', 'W')],
    ['Altitude',     blank(frame.altitude_m, (v) => `${v.toFixed(1)} m`)],
    ['Heading',      blank(frame.heading_deg, (v) => `${v.toFixed(1)}°`)],
    ['HDOP',         blank(frame.hdop, (v) => v.toFixed(2))],
    ['Satellites',   blank(frame.satellite_count, (v) => String(v))],
    ['Disk free',    blank(frame.disk_free_gb, (v) => `${v.toFixed(2)} GB`)],
  ] : [];

  const overlayRows = frame?.overlay ? [
    ['Inliers',      blank(frame.overlay.inlier_count, String)],
    ['Position err', blank(frame.overlay.position_error_m, (v) => `${v.toFixed(1)} m`)],
    ['Retrieval',    blank(frame.overlay.retrieval_rank, (v) => `rank ${v}`)],
    ['Reject',       frame.overlay.reject_reason || '—'],
  ] : [];

  const jpegSrc = session && jpegCache.current[row];

  return (
    <div className="flex flex-col h-[calc(100vh-56px)] bg-gray-900">

      {/* ── Top bar ── */}
      <div className="shrink-0 bg-gray-950 border-b border-gray-800 px-4 py-3 flex items-center gap-3 flex-wrap">
        <SessionDropdown onOpen={handleSessionOpen} disabled={false} />
        {session && (
          <span className="text-sm text-gray-400 font-mono ml-2">
            <span className="text-gray-300">{session.session_id}</span>
            {' · '}{session.total_rows} frames
            {session.has_overlay && <span className="ml-2 text-xs text-emerald-400 font-semibold">overlay</span>}
          </span>
        )}
        {session && (
          <span className="ml-auto text-xs font-mono text-gray-500 tabular-nums">
            {row + 1} / {session.total_rows}
          </span>
        )}
      </div>

      {/* ── Timeline + playback controls ── */}
      {session && (
        <div className="shrink-0 bg-gray-900 border-b border-gray-800 px-4 py-2 flex items-center gap-3">
          <button
            onClick={playing ? stopPlay : startPlay}
            className="w-16 py-1.5 bg-emerald-700 hover:bg-emerald-600 rounded text-xs font-bold text-white transition-colors"
          >
            {playing ? '⏸ Pause' : '▶ Play'}
          </button>

          {/* Rate selector */}
          <div className="flex items-center gap-1">
            {RATES.map((r) => (
              <button
                key={r}
                onClick={() => { setRate(r); playStateRef.current.rate = r; }}
                className={`px-2 py-1 rounded text-xs font-mono font-semibold transition-colors
                            ${rate === r ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}
              >
                {r}×
              </button>
            ))}
          </div>

          {/* Jump to low-inlier */}
          <button
            onClick={() => jumpLowInlier('back')}
            disabled={!session.has_overlay}
            className="px-3 py-1.5 rounded text-xs text-white font-semibold transition-colors
                       bg-amber-700 hover:bg-amber-600 disabled:opacity-30 disabled:cursor-not-allowed"
            title="Prev low-inlier frame"
          >
            ◀ Low
          </button>
          <button
            onClick={() => jumpLowInlier('fwd')}
            disabled={!session.has_overlay}
            className="px-3 py-1.5 rounded text-xs text-white font-semibold transition-colors
                       bg-amber-700 hover:bg-amber-600 disabled:opacity-30 disabled:cursor-not-allowed"
            title="Next low-inlier frame"
          >
            Low ▶
          </button>

          {/* Scrubber */}
          <input
            type="range"
            min={0}
            max={session.total_rows - 1}
            value={row}
            onChange={(e) => { stopPlay(); setRow(Number(e.target.value)); }}
            className="flex-1 accent-blue-500"
          />
        </div>
      )}

      {/* ── Main content ── */}
      {!session ? (
        <div className="flex-1 flex items-center justify-center text-gray-600 text-base">
          Click{' '}
          <span className="mx-1.5 px-2 py-0.5 bg-gray-800 rounded text-indigo-400 font-semibold text-sm">Select Session ▾</span>
          to load a flight recording
        </div>
      ) : (
        <div className="flex-1 flex overflow-hidden">

          {/* Left — JPEG frame (60%) */}
          <div className="flex flex-col w-[60%] border-r border-gray-800 overflow-hidden">
            <div className="flex-1 bg-black flex items-center justify-center min-h-0">
              {jpegSrc ? (
                <img
                  key={jpegSrc}
                  src={jpegSrc}
                  alt={`Frame ${row}`}
                  className="max-w-full max-h-full object-contain"
                />
              ) : frame && !frame.jpeg_available ? (
                <div className="text-gray-600 text-sm flex flex-col items-center gap-2">
                  <div className="w-16 h-16 bg-gray-800 rounded flex items-center justify-center text-3xl">?</div>
                  Frame unavailable
                </div>
              ) : (
                <div className="text-gray-700 text-xs animate-pulse">
                  {frameLoading ? 'Loading…' : 'Buffering…'}
                </div>
              )}
            </div>

            {/* Map below frame */}
            <div className="h-48 shrink-0 border-t border-gray-800">
              <MapContainer
                center={[19.9175, 73.8278]}
                zoom={14}
                style={{ height: '100%', width: '100%' }}
                zoomControl
              >
                <TileLayer url={ESRI_IMAGERY.url} attribution={ESRI_IMAGERY.attribution} maxZoom={ESRI_IMAGERY.maxZoom} />
                {trackLine.length > 1 && (
                  <Polyline positions={trackLine} pathOptions={{ color: '#9ca3af', weight: 2, opacity: 0.7 }} />
                )}
                {currentPos && (
                  <CircleMarker
                    center={[currentPos.lat, currentPos.lon]}
                    radius={8}
                    pathOptions={{ color: '#3b82f6', fillColor: '#3b82f6', fillOpacity: 0.9, weight: 2 }}
                  />
                )}
                <MapController gpsTrack={gpsTrack} currentPos={currentPos} />
              </MapContainer>
            </div>
          </div>

          {/* Right — metadata + overlay (40%) */}
          <div className="w-[40%] overflow-y-auto">
            {frame ? (
              <div className="px-5 py-4">
                <table className="text-sm w-full">
                  <tbody className="divide-y divide-gray-700">
                    {metaRows.map(([label, value]) => (
                      <tr key={label}>
                        <td className="py-1.5 pr-6 text-gray-400 w-28 shrink-0">{label}</td>
                        <td className="py-1.5 font-mono text-gray-100">{value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {overlayRows.length > 0 && (
                  <>
                    <div className="mt-4 mb-2 text-xs font-semibold text-emerald-400 uppercase tracking-wider">Algorithm Overlay</div>
                    <table className="text-sm w-full">
                      <tbody className="divide-y divide-gray-800">
                        {overlayRows.map(([label, value]) => (
                          <tr key={label}>
                            <td className="py-1.5 pr-6 text-gray-500 w-28 shrink-0">{label}</td>
                            <td className="py-1.5 font-mono text-gray-200">{value}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
              </div>
            ) : (
              <div className="p-5 text-gray-600 text-sm animate-pulse">Loading metadata…</div>
            )}
          </div>
        </div>
      )}

      {toast && <Toast message={toast} onDismiss={() => setToast('')} />}
    </div>
  );
}

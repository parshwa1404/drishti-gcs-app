import { useState, useEffect, useRef, useCallback } from 'react';
import { MapContainer, TileLayer, Polyline, CircleMarker, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const API = 'http://localhost:8000';

const ESRI_IMAGERY = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  attribution: 'Tiles &copy; Esri',
  maxZoom: 19,
};

function fmtTs(ms) {
  if (!ms) return '—';
  const d = new Date(ms);
  return d.toISOString().replace('T', ' ').slice(0, 23);
}

function fmtCoord(v, pos, neg) {
  if (v == null) return '—';
  return `${Math.abs(v).toFixed(6)}° ${v >= 0 ? pos : neg}`;
}

// ─── Map controller: fitBounds on first load, panTo on subsequent moves ─────
function MapController({ gpsTrack, currentPos }) {
  const map = useMap();
  const fitted = useRef(false);

  useEffect(() => {
    if (gpsTrack.length > 1 && !fitted.current) {
      map.fitBounds(
        gpsTrack.map((p) => [p.lat, p.lon]),
        { padding: [40, 40] }
      );
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

// ─── Toast ───────────────────────────────────────────────────────────────────
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

// ─── Quick Verify card ───────────────────────────────────────────────────────

function QuickVerifyCard({ verify, onDismiss }) {
  const isGood = verify.verdict === 'GOOD';
  return (
    <div className={`shrink-0 border-b ${isGood ? 'border-green-800 bg-green-950/40' : 'border-red-800 bg-red-950/40'} px-4 py-3`}>
      <div className="flex items-start gap-4">
        {/* Verdict badge */}
        <div className={`shrink-0 px-4 py-2 rounded-lg font-bold text-lg tracking-widest
                         ${isGood ? 'bg-green-700 text-green-100' : 'bg-red-700 text-red-100'}`}>
          {isGood ? 'GOOD' : 'RE-FLY'}
        </div>

        {/* Stats */}
        <div className="flex-1 grid grid-cols-2 gap-x-8 gap-y-0.5 text-xs">
          <div className="flex gap-2 text-gray-400">
            <span>Frames:</span>
            <span className="text-gray-200 font-mono">{verify.frame_count}</span>
          </div>
          <div className="flex gap-2 text-gray-400">
            <span>Duration:</span>
            <span className="text-gray-200 font-mono">{verify.duration_s} s</span>
          </div>
          <div className="flex gap-2 text-gray-400">
            <span>GPS pts:</span>
            <span className="text-gray-200 font-mono">{verify.gps_track_points}</span>
          </div>
          <div className="flex gap-2 text-gray-400">
            <span>HDOP (median):</span>
            <span className={`font-mono font-semibold ${verify.gps_fix_quality.hdop_median <= 1.5 ? 'text-green-400' : 'text-amber-400'}`}>
              {verify.gps_fix_quality.hdop_median ?? '—'}
            </span>
          </div>
          <div className="flex gap-2 text-gray-400">
            <span>Gaps &gt; 1 s:</span>
            <span className={`font-mono font-semibold ${verify.recording_gaps.length === 0 ? 'text-green-400' : 'text-red-400'}`}>
              {verify.recording_gaps.length}
            </span>
          </div>
          <div className="flex gap-2 text-gray-400">
            <span>Danger zone frames:</span>
            <span className={`font-mono font-semibold ${verify.heading_coverage.danger_zone_frames === 0 ? 'text-green-400' : 'text-amber-400'}`}>
              {verify.heading_coverage.danger_zone_frames}
            </span>
          </div>
        </div>

        {/* Dismiss */}
        <button
          onClick={onDismiss}
          className="shrink-0 text-xs text-gray-500 hover:text-gray-300 transition-colors px-2 py-1 rounded"
        >
          Dismiss
        </button>
      </div>

      {/* RE-FLY reasons */}
      {!isGood && verify.refly_reasons.length > 0 && (
        <ul className="mt-2 ml-[120px] space-y-0.5 text-xs text-red-300 list-disc list-inside">
          {verify.refly_reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}

// ─── Main panel ──────────────────────────────────────────────────────────────
export default function ReplayPanel() {
  const [sessionDir, setSessionDir]     = useState('');
  const [loading, setLoading]           = useState(false);
  const [sessionData, setSessionData]   = useState(null);
  const [currentIdx, setCurrentIdx]     = useState(0);
  const [playing, setPlaying]           = useState(false);
  const [toast, setToast]               = useState('');
  const [imgError, setImgError]         = useState(false);
  const [verify, setVerify]             = useState(null);
  const [verifyDismissed, setVerifyDismissed] = useState(false);
  const playRef = useRef(null);
  const imgRef  = useRef(null);

  function fetchVerify(sessionName) {
    setVerify(null);
    setVerifyDismissed(false);
    fetch(`${API}/session/verify/${encodeURIComponent(sessionName)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((v) => { if (v) setVerify(v); })
      .catch(() => {});
  }

  // Auto-load mock on mount
  useEffect(() => {
    fetch(`${API}/session/mock`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.frame_count) {
          setSessionData(data);
          setSessionDir('[mock session]');
          setCurrentIdx(0);
          fetchVerify(data.session_name);
        }
      })
      .catch(() => {});
  }, []);

  // Keyboard nav
  useEffect(() => {
    if (!sessionData) return;
    const handler = (e) => {
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        setCurrentIdx((i) => Math.max(0, i - 1));
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        setCurrentIdx((i) => Math.min(sessionData.frame_count - 1, i + 1));
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [sessionData]);

  // Playback
  const stopPlay = useCallback(() => {
    if (playRef.current) {
      clearInterval(playRef.current);
      playRef.current = null;
    }
    setPlaying(false);
  }, []);

  const startPlay = useCallback(() => {
    if (playRef.current || !sessionData) return;
    playRef.current = setInterval(() => {
      setCurrentIdx((i) => {
        if (i >= sessionData.frame_count - 1) {
          stopPlay();
          return i;
        }
        return i + 1;
      });
    }, 100);
    setPlaying(true);
  }, [sessionData, stopPlay]);

  useEffect(() => () => { if (playRef.current) clearInterval(playRef.current); }, []);

  async function handleLoad() {
    if (!sessionDir || sessionDir === '[mock session]') return;
    setLoading(true);
    stopPlay();
    try {
      const res = await fetch(`${API}/session/load`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_dir: sessionDir }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setToast(err.detail || 'Failed to load session');
        return;
      }
      const data = await res.json();
      if (!data.frame_count) {
        setToast('No frames found in session');
        return;
      }
      setSessionData(data);
      setCurrentIdx(0);
      fetchVerify(data.session_name);
    } catch {
      setToast('Cannot reach backend');
    } finally {
      setLoading(false);
    }
  }

  async function handleLoadMock() {
    setLoading(true);
    stopPlay();
    try {
      const res = await fetch(`${API}/session/mock`);
      if (!res.ok) { setToast('Mock session not available'); return; }
      const data = await res.json();
      setSessionData(data);
      setSessionDir('[mock session]');
      setCurrentIdx(0);
      fetchVerify(data.session_name);
    } catch {
      setToast('Cannot reach backend');
    } finally {
      setLoading(false);
    }
  }

  const frame     = sessionData?.frames?.[currentIdx];
  const gpsTrack  = sessionData?.gps_track ?? [];
  const trackLine = gpsTrack.map((p) => [p.lat, p.lon]);
  const currentPos = frame?.lat != null ? { lat: frame.lat, lon: frame.lon } : null;

  return (
    <div className="flex flex-col h-[calc(100vh-56px)] bg-gray-900">

      {/* ── Top bar ── */}
      <div className="shrink-0 bg-gray-950 border-b border-gray-800 px-4 py-3 flex items-center gap-3 flex-wrap">
        <input
          className="flex-1 min-w-[260px] bg-gray-700 rounded px-3 py-2 text-sm
                     font-mono text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
          placeholder="/path/to/session_dir"
          value={sessionDir === '[mock session]' ? '' : sessionDir}
          onChange={(e) => setSessionDir(e.target.value)}
        />
        <button
          onClick={handleLoad}
          disabled={loading || !sessionDir || sessionDir === '[mock session]'}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40
                     rounded text-sm font-semibold text-white transition-colors"
        >
          {loading ? 'Loading…' : 'Load'}
        </button>
        <button
          onClick={handleLoadMock}
          disabled={loading}
          className="px-4 py-2 bg-gray-600 hover:bg-gray-500 disabled:opacity-40
                     rounded text-sm font-semibold text-white transition-colors"
        >
          Load Mock
        </button>
        {sessionData && (
          <span className="text-sm text-gray-400 font-mono whitespace-nowrap">
            {sessionData.frame_count} frames · {sessionData.duration_s}s ·{' '}
            <span className="text-gray-300">{sessionData.session_name}</span>
          </span>
        )}
      </div>

      {/* ── Quick Verify ── */}
      {verify && !verifyDismissed && (
        <QuickVerifyCard verify={verify} onDismiss={() => setVerifyDismissed(true)} />
      )}

      {/* ── Timeline ── */}
      {sessionData && (
        <div className="shrink-0 bg-gray-900 border-b border-gray-800 px-4 py-2
                        flex items-center gap-3">
          <button
            onClick={playing ? stopPlay : startPlay}
            className="w-16 py-1.5 bg-emerald-700 hover:bg-emerald-600 rounded
                       text-xs font-bold text-white transition-colors"
          >
            {playing ? '⏸ Pause' : '▶ Play'}
          </button>
          <input
            type="range"
            min={0}
            max={sessionData.frame_count - 1}
            value={currentIdx}
            onChange={(e) => {
              stopPlay();
              setCurrentIdx(Number(e.target.value));
              setImgError(false);
            }}
            className="flex-1 accent-blue-500"
          />
          <span className="text-xs font-mono text-gray-400 tabular-nums w-20 text-right">
            {currentIdx + 1} / {sessionData.frame_count}
          </span>
        </div>
      )}

      {/* ── Main content ── */}
      {!sessionData ? (
        <div className="flex-1 flex items-center justify-center text-gray-600 text-base">
          Load a session or click <span className="mx-1.5 px-2 py-0.5 bg-gray-800 rounded text-gray-400 font-mono text-sm">Load Mock</span> to begin
        </div>
      ) : (
        <div className="flex-1 flex overflow-hidden">

          {/* Left — frame + metadata (55%) */}
          <div className="flex flex-col w-[55%] overflow-y-auto border-r border-gray-800">
            <div className="flex-1 bg-black flex items-center justify-center min-h-[300px]">
              {imgError ? (
                <div className="text-gray-600 text-sm flex flex-col items-center gap-2">
                  <div className="w-16 h-16 bg-gray-800 rounded flex items-center justify-center text-3xl">?</div>
                  Frame unavailable
                </div>
              ) : frame ? (
                <img
                  ref={imgRef}
                  key={frame.timestamp_ms}
                  src={`${API}/session/frame/${frame.timestamp_ms}`}
                  alt={`Frame ${currentIdx}`}
                  className="max-w-full max-h-full object-contain"
                  onError={() => setImgError(true)}
                  onLoad={() => setImgError(false)}
                />
              ) : null}
            </div>

            {/* Metadata card */}
            {frame && (
              <div className="shrink-0 bg-gray-800 border-t border-gray-700 px-5 py-4">
                <table className="text-sm w-full">
                  <tbody className="divide-y divide-gray-700">
                    {[
                      ['Timestamp', fmtTs(frame.timestamp_ms)],
                      ['Position', frame.lat != null
                        ? `${fmtCoord(frame.lat, 'N', 'S')},  ${fmtCoord(frame.lon, 'E', 'W')}`
                        : '—'],
                      ['Heading', frame.heading_deg != null ? `${frame.heading_deg.toFixed(1)}°` : '—'],
                      ['HDOP', frame.hdop != null ? frame.hdop.toFixed(2) : '—'],
                      ...(frame.altitude_m != null ? [['Altitude', `${frame.altitude_m.toFixed(1)} m`]] : []),
                    ].map(([label, value]) => (
                      <tr key={label}>
                        <td className="py-1.5 pr-6 text-gray-400 w-28">{label}</td>
                        <td className="py-1.5 font-mono text-gray-100">{value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Right — Leaflet map (45%) */}
          <div className="w-[45%] relative">
            <MapContainer
              center={[19.9175, 73.8278]}
              zoom={14}
              style={{ height: '100%', width: '100%' }}
              zoomControl={true}
            >
              <TileLayer
                url={ESRI_IMAGERY.url}
                attribution={ESRI_IMAGERY.attribution}
                maxZoom={ESRI_IMAGERY.maxZoom}
              />

              {/* Full GPS track in grey */}
              {trackLine.length > 1 && (
                <Polyline
                  positions={trackLine}
                  pathOptions={{ color: '#9ca3af', weight: 2, opacity: 0.7 }}
                />
              )}

              {/* Current position marker */}
              {currentPos && (
                <CircleMarker
                  center={[currentPos.lat, currentPos.lon]}
                  radius={8}
                  pathOptions={{
                    color: '#3b82f6',
                    fillColor: '#3b82f6',
                    fillOpacity: 0.9,
                    weight: 2,
                  }}
                />
              )}

              <MapController gpsTrack={gpsTrack} currentPos={currentPos} />
            </MapContainer>
          </div>

        </div>
      )}

      {toast && <Toast message={toast} onDismiss={() => setToast('')} />}
    </div>
  );
}

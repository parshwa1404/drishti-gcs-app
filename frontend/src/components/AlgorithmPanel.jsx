import { useState, useEffect, useRef, useCallback } from 'react';
import { MapContainer, TileLayer, Polyline, CircleMarker, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const API = 'http://localhost:8000';

const ESRI_IMAGERY = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  attribution: 'Tiles &copy; Esri',
  maxZoom: 19,
};

// ─── Map controller ──────────────────────────────────────────────────────────

function DualTrackController({ gpsTrack, currentGPS }) {
  const map = useMap();
  const fitted = useRef(false);

  useEffect(() => {
    if (gpsTrack.length > 1 && !fitted.current) {
      map.fitBounds(gpsTrack.map((p) => [p.lat, p.lon]), { padding: [30, 30] });
      fitted.current = true;
    }
  }, [gpsTrack, map]);

  useEffect(() => {
    if (currentGPS && fitted.current) {
      map.panTo([currentGPS.lat, currentGPS.lon], { animate: true, duration: 0.3 });
    }
  }, [currentGPS, map]);

  return null;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function Toast({ message, onDismiss }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4500);
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

function MetricRow({ label, value, unit = '' }) {
  return (
    <div className="flex flex-col items-center py-2 border-b border-gray-700 last:border-0">
      <span className="text-xs text-gray-500 mb-0.5">{label}</span>
      <span className="text-sm font-mono font-semibold text-gray-100 tabular-nums">
        {value != null ? `${value}${unit}` : '—'}
      </span>
    </div>
  );
}

function RejectBadge({ reason }) {
  if (!reason) return null;
  return (
    <span className="absolute top-2 left-2 z-10 px-2 py-0.5 rounded text-xs font-bold
                     bg-amber-800/90 text-amber-200 uppercase tracking-wide">
      {reason}
    </span>
  );
}

// ─── Main panel ──────────────────────────────────────────────────────────────

export default function AlgorithmPanel() {
  const [sessionDir, setSessionDir] = useState('');
  const [tileDir, setTileDir]       = useState('~/datasets/faiss_index/deolali_z19');
  const [results, setResults]       = useState(null);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [playing, setPlaying]       = useState(false);
  const [logLines, setLogLines]     = useState([]);
  const [running, setRunning]       = useState(false);
  const [showLog, setShowLog]       = useState(false);
  const [framePair, setFramePair]   = useState(null);
  const [fpLoading, setFpLoading]   = useState(false);
  const [toast, setToast]           = useState('');

  const playRef    = useRef(null);
  const logEndRef  = useRef(null);
  const fpCacheRef = useRef(new Map());
  const sseRef     = useRef(null);

  // Auto-load mock on mount
  useEffect(() => {
    fetch(`${API}/pipeline/mock_results`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (data?.frame_count) { setResults(data); setCurrentIdx(0); } })
      .catch(() => {});
  }, []);

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logLines]);

  // Fetch frame pair on index change
  useEffect(() => {
    if (!results) return;
    const frame = results.frames[currentIdx];
    if (!frame) return;
    const ts = frame.timestamp_ms;

    if (fpCacheRef.current.has(ts)) {
      setFramePair(fpCacheRef.current.get(ts));
      return;
    }

    const ctrl = new AbortController();
    setFpLoading(true);
    fetch(`${API}/pipeline/frame-pair/${results.session_name}/${ts}`, { signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        if (fpCacheRef.current.size > 40) {
          const oldest = fpCacheRef.current.keys().next().value;
          fpCacheRef.current.delete(oldest);
        }
        fpCacheRef.current.set(ts, data);
        setFramePair(data);
      })
      .catch(() => {})
      .finally(() => setFpLoading(false));

    return () => ctrl.abort();
  }, [currentIdx, results]);

  // Keyboard nav
  useEffect(() => {
    if (!results) return;
    const handler = (e) => {
      if (e.key === 'ArrowLeft')  { e.preventDefault(); setCurrentIdx((i) => Math.max(0, i - 1)); }
      if (e.key === 'ArrowRight') { e.preventDefault(); setCurrentIdx((i) => Math.min(results.frame_count - 1, i + 1)); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [results]);

  // Playback
  const stopPlay = useCallback(() => {
    if (playRef.current) { clearInterval(playRef.current); playRef.current = null; }
    setPlaying(false);
  }, []);

  const startPlay = useCallback(() => {
    if (playRef.current || !results) return;
    playRef.current = setInterval(() => {
      setCurrentIdx((i) => {
        if (i >= results.frame_count - 1) { stopPlay(); return i; }
        return i + 1;
      });
    }, 100);
    setPlaying(true);
  }, [results, stopPlay]);

  useEffect(() => () => { if (playRef.current) clearInterval(playRef.current); }, []);

  async function handleLoadMock() {
    stopPlay();
    fpCacheRef.current.clear();
    try {
      const r = await fetch(`${API}/pipeline/mock_results`);
      const data = r.ok ? await r.json() : null;
      if (data?.frame_count) { setResults(data); setCurrentIdx(0); setFramePair(null); }
      else setToast('Failed to load mock results');
    } catch { setToast('Cannot reach backend'); }
  }

  function handleRun() {
    if (running) return;
    if (sseRef.current) sseRef.current.close();
    setLogLines([]);
    setShowLog(true);
    setRunning(true);

    const params = new URLSearchParams();
    // POST via fetch + SSE workaround: use GET with query params isn't ideal,
    // so we POST then poll — but for simplicity, show a note
    setLogLines(['[GCS] POST /pipeline/run requires a real drishti-nav-v3 installation.']);
    setLogLines(prev => [...prev, `[GCS] DRISHTI_NAV_PATH must be set in backend/.env`]);
    setRunning(false);
  }

  const frame       = results?.frames?.[currentIdx];
  const gpsTrack    = results?.gps_track ?? [];
  const estTrack    = results?.est_track ?? [];
  const gpsLine     = gpsTrack.map((p) => [p.lat, p.lon]);
  const estLine     = estTrack.map((p) => [p.lat, p.lon]);
  const currentGPS  = frame?.lat     != null ? { lat: frame.lat,     lon: frame.lon     } : null;
  const currentEst  = frame?.est_lat != null ? { lat: frame.est_lat, lon: frame.est_lon } : null;
  const isRejected  = !!frame?.reject_reason;

  const fp = framePair;

  // Confidence: prefer field from API, fall back to inlier_count / 30
  const confidence = fp?.confidence
    ?? (frame?.inlier_count != null ? Math.min(1.0, frame.inlier_count / 30) : null);
  const confidenceColor = confidence == null ? 'text-gray-400'
    : confidence >= 0.67 ? 'text-green-400'
    : confidence >= 0.33 ? 'text-amber-400'
    : 'text-red-400';

  return (
    <div className="flex flex-col h-[calc(100vh-56px)] bg-gray-900 overflow-hidden">

      {/* ── Top bar ── */}
      <div className="shrink-0 bg-gray-950 border-b border-gray-800 px-4 py-2.5
                      flex items-center gap-3 flex-wrap">
        <input
          className="flex-1 min-w-[220px] bg-gray-700 rounded px-3 py-1.5 text-sm
                     font-mono text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
          placeholder="session_dir"
          value={sessionDir}
          onChange={(e) => setSessionDir(e.target.value)}
        />
        <input
          className="flex-1 min-w-[220px] bg-gray-700 rounded px-3 py-1.5 text-sm
                     font-mono text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
          placeholder="tile_index_dir"
          value={tileDir}
          onChange={(e) => setTileDir(e.target.value)}
        />
        <button
          onClick={handleRun}
          disabled={running || !sessionDir}
          className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40
                     rounded text-sm font-semibold text-white transition-colors"
        >
          {running ? 'Running…' : '▶ Run Pipeline'}
        </button>
        <button
          onClick={handleLoadMock}
          className="px-4 py-1.5 bg-gray-600 hover:bg-gray-500
                     rounded text-sm font-semibold text-white transition-colors"
        >
          Load Mock Results
        </button>
        {results && (
          <span className="text-xs text-gray-500 font-mono whitespace-nowrap">
            {results.frame_count} frames · <span className="text-gray-400">{results.session_name}</span>
          </span>
        )}
      </div>

      {/* ── Log terminal ── */}
      {showLog && (
        <div className="shrink-0 bg-black border-b border-gray-800 h-28 overflow-y-auto px-3 py-2">
          <div className="flex justify-between items-center mb-1">
            <span className="text-xs text-gray-500 font-mono">Pipeline log</span>
            <button
              onClick={() => setShowLog(false)}
              className="text-xs text-gray-600 hover:text-gray-400"
            >
              ✕ close
            </button>
          </div>
          <pre className="text-xs font-mono text-green-400 whitespace-pre-wrap">
            {logLines.join('\n')}
          </pre>
          <div ref={logEndRef} />
        </div>
      )}

      {/* ── Timeline ── */}
      {results && (
        <div className="shrink-0 bg-gray-900 border-b border-gray-800 px-4 py-1.5
                        flex items-center gap-3">
          <button
            onClick={playing ? stopPlay : startPlay}
            className="w-16 py-1 bg-emerald-700 hover:bg-emerald-600 rounded
                       text-xs font-bold text-white transition-colors"
          >
            {playing ? '⏸ Pause' : '▶ Play'}
          </button>
          <input
            type="range" min={0} max={results.frame_count - 1} value={currentIdx}
            onChange={(e) => { stopPlay(); setCurrentIdx(Number(e.target.value)); }}
            className="flex-1 accent-blue-500"
          />
          <span className="text-xs font-mono text-gray-400 tabular-nums w-20 text-right">
            {currentIdx + 1} / {results.frame_count}
          </span>
        </div>
      )}

      {/* ── Main content ── */}
      {!results ? (
        <div className="flex-1 flex items-center justify-center text-gray-600 text-base">
          Click <span className="mx-1.5 px-2 py-0.5 bg-gray-800 rounded text-gray-400 font-mono text-sm">Load Mock Results</span> or run the pipeline
        </div>
      ) : (
        <div className="flex-1 flex flex-col overflow-hidden">

          {/* 3-col: live frame | metrics | matched tile */}
          <div className="flex min-h-0 flex-1" style={{ maxHeight: '55%' }}>

            {/* Live frame */}
            <div className="w-[40%] bg-black relative flex flex-col border-r border-gray-800">
              <div className="text-xs text-gray-500 text-center py-1 shrink-0 border-b border-gray-800 font-semibold uppercase tracking-wider">
                Live Frame
              </div>
              <div className="flex-1 relative flex items-center justify-center overflow-hidden">
                {isRejected && (
                  <div className="absolute inset-0 bg-gray-900/60 z-10 flex items-center justify-center">
                    <span className="px-3 py-1.5 bg-amber-800/90 text-amber-200 text-xs font-bold rounded uppercase tracking-widest">
                      {frame.reject_reason}
                    </span>
                  </div>
                )}
                {fp?.live_frame ? (
                  <img
                    src={`data:image/jpeg;base64,${fp.live_frame}`}
                    alt="live frame"
                    className="max-w-full max-h-full object-contain"
                  />
                ) : (
                  <div className="text-gray-700 text-sm">
                    {fpLoading ? 'Loading…' : 'No frame'}
                  </div>
                )}
              </div>
            </div>

            {/* Metrics card */}
            <div className="w-[20%] bg-gray-800 border-r border-gray-700 flex flex-col justify-center px-3 py-2">
              <div className="text-xs text-gray-500 text-center mb-2 uppercase tracking-wider font-semibold">
                Metrics
              </div>
              <MetricRow label="Retrieval rank"    value={fp?.retrieval_rank} />
              <MetricRow label="Inliers"           value={fp?.inlier_count} />
              <MetricRow label="Position error"    value={fp?.position_error_m != null ? fp.position_error_m.toFixed(1) : null} unit=" m" />
              <MetricRow label="Camera GSD"        value={fp?.camera_gsd_m_per_px != null ? fp.camera_gsd_m_per_px.toFixed(3) : null} unit=" m/px" />
              <MetricRow label="Altitude"          value={fp?.altitude_m != null ? fp.altitude_m.toFixed(1) : null} unit=" m" />
              <div className="flex flex-col items-center py-2 border-b border-gray-700">
                <span className="text-xs text-gray-500 mb-0.5">Pre-filter</span>
                {fp?.reject_reason ? (
                  <span className="text-xs font-bold text-amber-400 uppercase">{fp.reject_reason}</span>
                ) : (
                  <span className="text-xs font-bold text-green-400">PASS</span>
                )}
              </div>
              <div className="flex flex-col items-center py-2">
                <span className="text-xs text-gray-500 mb-0.5">Confidence</span>
                <span className={`text-sm font-mono font-semibold tabular-nums ${confidenceColor}`}>
                  {confidence != null ? confidence.toFixed(2) : '—'}
                </span>
              </div>

              {/* Solver timing */}
              <div className="border-t border-gray-700 mt-1 pt-1">
                <div className="text-xs text-gray-500 text-center mb-0.5 uppercase tracking-wider">Solver</div>
                <MetricRow label="Embed"     value={fp?.solver_ms?.embed     != null ? fp.solver_ms.embed.toFixed(1)     : null} unit=" ms" />
                <MetricRow label="FAISS"     value={fp?.solver_ms?.faiss     != null ? fp.solver_ms.faiss.toFixed(1)     : null} unit=" ms" />
                <MetricRow label="LightGlue" value={fp?.solver_ms?.lightglue != null ? fp.solver_ms.lightglue.toFixed(1) : null} unit=" ms" />
                <MetricRow label="Total"     value={fp?.solver_ms?.total     != null ? fp.solver_ms.total.toFixed(1)     : null} unit=" ms" />
              </div>
              <div className="flex flex-col items-center py-2 border-t border-gray-700">
                <span className="text-xs text-gray-500 mb-0.5">Last fix</span>
                {(() => {
                  const lf = fp?.seconds_since_last_fix;
                  const col = lf == null ? 'text-gray-400'
                    : lf < 2 ? 'text-green-400'
                    : lf < 5 ? 'text-amber-400'
                    : 'text-red-400';
                  return (
                    <span className={`text-sm font-mono font-semibold tabular-nums ${col}`}>
                      {lf != null ? `${lf.toFixed(1)} s ago` : '—'}
                    </span>
                  );
                })()}
              </div>
            </div>

            {/* Matched tile */}
            <div className="w-[40%] bg-black flex flex-col">
              <div className="text-xs text-gray-500 text-center py-1 shrink-0 border-b border-gray-800 font-semibold uppercase tracking-wider">
                Matched Tile
              </div>
              <div className="flex-1 flex items-center justify-center overflow-hidden">
                {fp?.matched_tile ? (
                  <img
                    src={`data:image/jpeg;base64,${fp.matched_tile}`}
                    alt="matched tile"
                    className="max-w-full max-h-full object-contain"
                  />
                ) : (
                  <div className="text-gray-700 text-sm">
                    {fpLoading ? 'Loading…' : 'No tile'}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Map */}
          <div className="shrink-0 border-t border-gray-800" style={{ height: '45%' }}>
            <MapContainer
              center={[19.9175, 73.8278]}
              zoom={14}
              style={{ height: '100%', width: '100%' }}
            >
              <TileLayer url={ESRI_IMAGERY.url} attribution={ESRI_IMAGERY.attribution} maxZoom={ESRI_IMAGERY.maxZoom} />

              {/* GPS truth track — blue */}
              {gpsLine.length > 1 && (
                <Polyline positions={gpsLine} pathOptions={{ color: '#3b82f6', weight: 2, opacity: 0.85 }} />
              )}
              {/* Pipeline estimate track — red */}
              {estLine.length > 1 && (
                <Polyline positions={estLine} pathOptions={{ color: '#ef4444', weight: 2, opacity: 0.85 }} />
              )}

              {/* Error vector */}
              {currentGPS && currentEst && (
                <Polyline
                  positions={[[currentGPS.lat, currentGPS.lon], [currentEst.lat, currentEst.lon]]}
                  pathOptions={{ color: '#9ca3af', weight: 1.5, dashArray: '4 3', opacity: 0.8 }}
                />
              )}

              {/* GPS truth marker — blue */}
              {currentGPS && (
                <CircleMarker
                  center={[currentGPS.lat, currentGPS.lon]}
                  radius={7}
                  pathOptions={{ color: '#3b82f6', fillColor: '#3b82f6', fillOpacity: 0.9, weight: 2 }}
                />
              )}
              {/* Estimate marker — red */}
              {currentEst && (
                <CircleMarker
                  center={[currentEst.lat, currentEst.lon]}
                  radius={7}
                  pathOptions={{ color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.9, weight: 2 }}
                />
              )}

              <DualTrackController gpsTrack={gpsTrack} currentGPS={currentGPS} />
            </MapContainer>

            {/* Map legend */}
            <div className="absolute bottom-6 left-4 z-[1000] bg-gray-900/85 rounded-lg px-3 py-2
                            flex gap-4 text-xs border border-gray-700">
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-0.5 bg-blue-500 inline-block" />GPS truth
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-0.5 bg-red-500 inline-block" />Pipeline est.
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-0.5 bg-gray-400 inline-block border-t border-dashed border-gray-400" />Error vector
              </span>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast message={toast} onDismiss={() => setToast('')} />}
    </div>
  );
}

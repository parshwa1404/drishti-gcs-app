import { useState, useEffect, useRef, useCallback } from 'react';
import { MapContainer, TileLayer, CircleMarker, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const API = 'http://localhost:8000';
const DEOLALI = [19.9175, 73.8278];
const MAX_FIXES = 500;

const ESRI_IMAGERY = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  attribution: 'Tiles &copy; Esri',
  maxZoom: 19,
};

function markerColor(hdop) {
  if (hdop <= 1.0) return '#4ade80'; // green-400
  if (hdop <= 2.0) return '#facc15'; // yellow-400
  return '#f87171';                  // red-400
}

// Imperatively pans map to new center without changing zoom
function MapAutoPan({ latestFix }) {
  const map = useMap();
  const hasPanned = useRef(false);

  useEffect(() => {
    if (!latestFix) return;
    if (!hasPanned.current) {
      // First fix: fly in with zoom 16
      map.flyTo([latestFix.lat, latestFix.lon], 16, { duration: 1.2 });
      hasPanned.current = true;
    } else {
      map.panTo([latestFix.lat, latestFix.lon], { animate: true, duration: 0.4 });
    }
  }, [latestFix, map]);

  return null;
}

export default function LiveMapPanel() {
  const [fixes, setFixes]       = useState([]);
  const [latest, setLatest]     = useState(null);
  const [connected, setConnected] = useState(false);
  const sseRef = useRef(null);

  useEffect(() => {
    const es = new EventSource(`${API}/session/live`);
    sseRef.current = es;

    es.onopen = () => setConnected(true);

    es.onmessage = (e) => {
      try {
        const fix = JSON.parse(e.data);
        if (fix.lat == null || fix.lon == null) return; // no GPS yet
        setLatest(fix);
        setFixes((prev) => {
          const next = [...prev, fix];
          return next.length > MAX_FIXES ? next.slice(next.length - MAX_FIXES) : next;
        });
      } catch { /* ignore parse errors */ }
    };

    es.onerror = () => setConnected(false);

    return () => es.close();
  }, []);

  return (
    <div className="relative h-[calc(100vh-56px)] w-full">
      <MapContainer
        center={DEOLALI}
        zoom={14}
        style={{ height: '100%', width: '100%' }}
        zoomControl={true}
      >
        <TileLayer
          url={ESRI_IMAGERY.url}
          attribution={ESRI_IMAGERY.attribution}
          maxZoom={ESRI_IMAGERY.maxZoom}
        />

        {fixes.map((fix, i) => (
          <CircleMarker
            key={i}
            center={[fix.lat, fix.lon]}
            radius={4}
            pathOptions={{
              color: markerColor(fix.hdop),
              fillColor: markerColor(fix.hdop),
              fillOpacity: 0.85,
              weight: 1,
            }}
          />
        ))}

        <MapAutoPan latestFix={latest} />
      </MapContainer>

      {/* Top-right overlay */}
      <div className="absolute top-4 right-4 z-[1000] bg-gray-900/90 backdrop-blur
                      rounded-xl px-4 py-3 min-w-[180px] border border-gray-700 text-sm space-y-1">
        {latest ? (
          <>
            <div className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2">
              Latest Fix
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-gray-400">Lat</span>
              <span className="font-mono text-white">{latest.lat.toFixed(6)}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-gray-400">Lon</span>
              <span className="font-mono text-white">{latest.lon.toFixed(6)}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-gray-400">HDOP</span>
              <span
                className="font-mono font-bold"
                style={{ color: markerColor(latest.hdop) }}
              >
                {latest.hdop.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-gray-400">Fixes</span>
              <span className="font-mono text-white">{fixes.length}</span>
            </div>
          </>
        ) : (
          <div className="text-gray-500 text-xs">Waiting for GPS fix…</div>
        )}
      </div>

      {/* Connection pill */}
      <div className={`absolute top-4 left-4 z-[1000] flex items-center gap-2 px-3 py-1.5
                       rounded-full text-xs font-semibold border
                       ${connected
                         ? 'bg-gray-900/80 border-green-700 text-green-400'
                         : 'bg-gray-900/80 border-gray-700 text-gray-500'}`}>
        <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-400 animate-pulse' : 'bg-gray-600'}`} />
        {connected ? 'Live' : 'Connecting…'}
      </div>

      {/* Placeholder when no fixes yet */}
      {fixes.length === 0 && (
        <div className="absolute inset-0 z-[999] flex items-center justify-center pointer-events-none">
          <div className="bg-gray-900/80 backdrop-blur rounded-xl px-6 py-4 text-gray-400 text-base">
            Waiting for GPS fix…
          </div>
        </div>
      )}
    </div>
  );
}

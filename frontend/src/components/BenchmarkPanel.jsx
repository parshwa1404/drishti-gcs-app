import { useState, useEffect, useRef, useMemo } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, Cell,
  ScatterChart, Scatter, ReferenceArea, CartesianGrid, ResponsiveContainer,
} from 'recharts';

const API = 'http://localhost:8000';
const BASELINE = { median: 32.5, p75: 48.2, p90: 67.8 };

// ─── Helpers ─────────────────────────────────────────────────────────────────

function diffColor(val, base) {
  if (val == null) return 'text-gray-500';
  if (val <= base - 2)  return 'text-green-400';
  if (val >= base + 2)  return 'text-red-400';
  return 'text-gray-400';
}

function diffLabel(val, base) {
  if (val == null) return '—';
  const d = val - base;
  if (Math.abs(d) < 2) return '≈ baseline';
  return `${d > 0 ? '+' : ''}${d.toFixed(1)} m`;
}

// ─── Stats table ─────────────────────────────────────────────────────────────

function StatsTable({ title, stats, color }) {
  if (!stats) return null;
  const rows = [
    { label: 'Frames (total)',    value: stats.n_frames,    unit: '' },
    { label: 'Valid',             value: stats.n_valid,     unit: '' },
    { label: 'Pre-filtered',      value: `${stats.n_filtered} (${stats.filtered_pct}%)`, unit: '' },
    null,
    { label: 'Median error',      value: stats.median,      unit: ' m', baseline: BASELINE.median },
    { label: '75th percentile',   value: stats.p75,         unit: ' m', baseline: BASELINE.p75 },
    { label: '90th percentile',   value: stats.p90,         unit: ' m', baseline: BASELINE.p90 },
    { label: 'Max error',         value: stats.max,         unit: ' m' },
    null,
    { label: '≤ 25 m',           value: `${stats.le_25m} (${stats.le_25m_pct}%)`,  unit: '' },
    { label: '≤ 50 m',           value: `${stats.le_50m} (${stats.le_50m_pct}%)`,  unit: '' },
    { label: '≤ 100 m',          value: `${stats.le_100m} (${stats.le_100m_pct}%)`, unit: '' },
  ];

  return (
    <div className="bg-gray-800 rounded-xl p-4 flex-1">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
        <span className={`w-2.5 h-2.5 rounded-full inline-block ${color}`} />
        {title}
      </h3>
      <table className="w-full text-sm">
        <tbody>
          {rows.map((row, i) =>
            row === null ? (
              <tr key={i}><td colSpan={3} className="py-1"><div className="border-t border-gray-700" /></td></tr>
            ) : (
              <tr key={i} className="hover:bg-gray-700/30 rounded">
                <td className="py-1 pr-3 text-gray-400 text-xs">{row.label}</td>
                <td className="py-1 text-right font-mono text-gray-100 text-xs tabular-nums">
                  {row.value != null ? `${row.value}${row.unit}` : '—'}
                </td>
                {row.baseline != null ? (
                  <td className={`py-1 pl-2 text-right font-mono text-xs tabular-nums ${diffColor(row.value, row.baseline)}`}>
                    {diffLabel(row.value, row.baseline)}
                  </td>
                ) : (
                  <td />
                )}
              </tr>
            )
          )}
        </tbody>
      </table>
      {stats.median != null && (
        <div className="mt-3 pt-2 border-t border-gray-700 text-xs text-gray-600">
          IIT-B baseline: median {BASELINE.median} m · p75 {BASELINE.p75} m · p90 {BASELINE.p90} m
        </div>
      )}
    </div>
  );
}

// ─── Charts ──────────────────────────────────────────────────────────────────

function ErrorHistogram({ frames, gate }) {
  const bins = [
    { name: '0–25 m',   min: 0,   max: 25  },
    { name: '25–50 m',  min: 25,  max: 50  },
    { name: '50–75 m',  min: 50,  max: 75  },
    { name: '75–100 m', min: 75,  max: 100 },
    { name: '100+ m',   min: 100, max: Infinity },
  ];
  const data = bins.map((b) => ({
    name: b.name,
    'Cut A': frames.filter(
      (f) => f.position_error_m != null && f.position_error_m >= b.min && f.position_error_m < b.max
    ).length,
    'Cut B': frames.filter(
      (f) => f.position_error_m != null && f.position_error_m >= b.min && f.position_error_m < b.max &&
             (f.inlier_count ?? 0) >= gate
    ).length,
  }));

  return (
    <div className="bg-gray-800 rounded-xl p-4">
      <div className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">
        Position Error Distribution
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} barGap={2}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#9ca3af' }} />
          <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} />
          <Tooltip contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6 }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="Cut A" fill="#6b7280" radius={[2, 2, 0, 0]} />
          <Bar dataKey="Cut B" fill="#3b82f6" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function InlierDistribution({ frames, gate }) {
  const bins = [
    { name: '0–5',   min: 0,  max: 5  },
    { name: '5–10',  min: 5,  max: 10 },
    { name: '10–15', min: 10, max: 15 },
    { name: '15–20', min: 15, max: 20 },
    { name: '20–30', min: 20, max: 30 },
    { name: '30+',   min: 30, max: Infinity },
  ];
  const data = bins.map((b) => ({
    name: b.name,
    count: frames.filter(
      (f) => f.inlier_count != null && f.inlier_count >= b.min && f.inlier_count < b.max
    ).length,
    aboveGate: b.min >= gate,
  }));

  return (
    <div className="bg-gray-800 rounded-xl p-4">
      <div className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">
        Inlier Count Distribution
        <span className="ml-2 font-normal text-gray-600">(gate = {gate})</span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#9ca3af' }} />
          <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} />
          <Tooltip contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6 }} />
          <Bar dataKey="count" radius={[2, 2, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.aboveGate ? '#3b82f6' : '#f59e0b'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="flex gap-4 mt-2 justify-center text-xs text-gray-500">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-blue-500 inline-block" />≥ gate</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-amber-500 inline-block" />&lt; gate</span>
      </div>
    </div>
  );
}

function HeadingScatter({ frames, gate }) {
  const above = frames
    .filter((f) => f.inlier_count != null && f.compass_hdg_deg != null && f.inlier_count >= gate)
    .map((f) => ({ x: f.compass_hdg_deg, y: f.inlier_count }));
  const below = frames
    .filter((f) => f.inlier_count != null && f.compass_hdg_deg != null && f.inlier_count < gate)
    .map((f) => ({ x: f.compass_hdg_deg, y: f.inlier_count }));

  return (
    <div className="bg-gray-800 rounded-xl p-4">
      <div className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">
        Heading vs Inlier Count
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <ScatterChart margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            type="number" dataKey="x" name="Heading" domain={[0, 360]}
            tick={{ fontSize: 10, fill: '#9ca3af' }}
            label={{ value: 'Heading (°)', position: 'insideBottom', offset: -2, fontSize: 10, fill: '#6b7280' }}
          />
          <YAxis
            type="number" dataKey="y" name="Inliers"
            tick={{ fontSize: 10, fill: '#9ca3af' }}
          />
          <Tooltip
            cursor={{ strokeDasharray: '3 3' }}
            contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6, fontSize: 11 }}
            formatter={(v, n) => [v, n === 'x' ? 'Heading °' : 'Inliers']}
          />
          <ReferenceArea
            x1={210} x2={240}
            fill="#ef4444" fillOpacity={0.12} stroke="#ef4444" strokeOpacity={0.3}
            label={{ value: 'high-rejection zone', position: 'insideTopRight', fontSize: 9, fill: '#f87171' }}
          />
          <Scatter name={`≥ gate (${gate})`} data={above} fill="#3b82f6" opacity={0.75} r={3} />
          <Scatter name={`< gate`}           data={below} fill="#f59e0b" opacity={0.75} r={3} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

function ConfidenceChart({ frames }) {
  const CONF_COLORS = ['#f59e0b', '#a3e635', '#34d399', '#14b8a6', '#0d9488'];
  const bins = [
    { name: '0–0.2',   min: 0,   max: 0.2  },
    { name: '0.2–0.4', min: 0.2, max: 0.4  },
    { name: '0.4–0.6', min: 0.4, max: 0.6  },
    { name: '0.6–0.8', min: 0.6, max: 0.8  },
    { name: '0.8–1.0', min: 0.8, max: 1.01 },
  ];
  const data = bins.map((b, i) => ({
    name: b.name,
    count: frames.filter((f) => {
      const c = f.confidence ?? (f.inlier_count != null ? Math.min(1.0, f.inlier_count / 30) : null);
      return c != null && c >= b.min && c < b.max;
    }).length,
    color: CONF_COLORS[i],
  }));

  return (
    <div className="bg-gray-800 rounded-xl p-4">
      <div className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">
        Frame Confidence Distribution
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#9ca3af' }} />
          <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} />
          <Tooltip contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6 }} />
          <Bar dataKey="count" radius={[2, 2, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="text-xs text-gray-600 text-center mt-1">amber (low) → teal (high)</div>
    </div>
  );
}

// ─── Main panel ──────────────────────────────────────────────────────────────

export default function BenchmarkPanel() {
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [toast, setToast]     = useState('');
  const exportRef = useRef(null);

  useEffect(() => {
    fetch(`${API}/pipeline/mock_results`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d?.frame_count) setResults(d); })
      .catch(() => {});
  }, []);

  async function handleLoad() {
    setLoading(true);
    try {
      const r = await fetch(`${API}/pipeline/mock_results`);
      const d = r.ok ? await r.json() : null;
      if (d?.frame_count) setResults(d);
      else setToast('No results available');
    } catch { setToast('Cannot reach backend'); }
    finally { setLoading(false); }
  }

  async function handleExport() {
    if (!exportRef.current || !results) return;
    try {
      const { default: html2canvas } = await import('html2canvas');
      const canvas = await html2canvas(exportRef.current, {
        backgroundColor: '#111827',
        scale: 2,
        useCORS: true,
      });
      const a = document.createElement('a');
      a.download = `drishti_benchmark_${results.session_name}.png`;
      a.href = canvas.toDataURL('image/png');
      a.click();
    } catch (e) {
      setToast('Export failed: ' + e.message);
    }
  }

  const bm    = results?.benchmark;
  const gate  = bm?.gate ?? 10;
  const frames = results?.frames ?? [];

  return (
    <div className="h-[calc(100vh-56px)] overflow-y-auto bg-gray-900 p-5 space-y-4">

      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-white tracking-wide">Benchmark Summary</h2>
        <div className="flex gap-2">
          <button
            onClick={handleLoad}
            disabled={loading}
            className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-40
                       rounded text-sm font-semibold text-white transition-colors"
          >
            {loading ? 'Loading…' : 'Load Results'}
          </button>
          {results && (
            <button
              onClick={handleExport}
              className="px-3 py-1.5 bg-blue-700 hover:bg-blue-600
                         rounded text-sm font-semibold text-white transition-colors"
            >
              Export PNG
            </button>
          )}
        </div>
      </div>

      {!results ? (
        <div className="flex items-center justify-center h-48 text-gray-600">
          No results loaded — click <span className="mx-1.5 px-2 py-0.5 bg-gray-800 rounded text-gray-400 font-mono text-sm">Load Results</span>
        </div>
      ) : (
        <div ref={exportRef} className="space-y-4">

          {/* Row 1 — stats tables */}
          <div className="flex gap-4">
            <StatsTable title="Cut A — All frames" stats={bm?.cut_a} color="bg-gray-400" />
            <StatsTable title={`Cut B — Inliers ≥ ${gate}`} stats={bm?.cut_b} color="bg-blue-500" />
          </div>

          {/* Row 2 — charts (2×2 grid) */}
          <div className="grid grid-cols-2 gap-4">
            <ErrorHistogram frames={frames} gate={gate} />
            <InlierDistribution frames={frames} gate={gate} />
            <HeadingScatter frames={frames} gate={gate} />
            <ConfidenceChart frames={frames} />
          </div>

          {/* Session info footer */}
          <div className="text-xs text-gray-600 font-mono">
            Session: {results.session_name} · {results.frame_count} frames · gate = {gate}
          </div>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[9999]
                        bg-red-900 border border-red-600 text-red-200
                        px-5 py-3 rounded-lg shadow-xl text-sm font-medium"
             onClick={() => setToast('')}>
          {toast}
        </div>
      )}
    </div>
  );
}

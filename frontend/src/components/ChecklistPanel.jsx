import { useState, useEffect, useRef } from 'react';

const API = 'http://localhost:8000';

const CHECK_LABELS = {
  rpi_connection:  'RPi Connection',
  logger_active:   'Logger Active',
  frame_rate:      'Frame Rate',
  gps_fix:         'GPS Fix',
  altitude_sane:   'Altitude',
  heading_present: 'Heading',
  gps_hdop:        'GPS HDOP',
  satellite_count: 'Satellites',
  disk_free:       'Disk Free',
  camera_exposure: 'Camera Exposure',
  fc_link:         'FC Link',
  tile_db_loaded:  'Tile DB',
};

const STATE_STYLE = {
  pass:        { tile: 'border-green-700 bg-green-950/40', pill: 'bg-green-600 text-white',  label: 'PASS' },
  warn:        { tile: 'border-amber-700 bg-amber-950/40', pill: 'bg-amber-500 text-black',  label: 'WARN' },
  fail:        { tile: 'border-red-700 bg-red-950/40',     pill: 'bg-red-600 text-white',    label: 'FAIL' },
  unavailable: { tile: 'border-gray-800 bg-gray-800/40',   pill: 'bg-gray-700 text-gray-400', label: '—' },
};

const OVERALL_STYLE = {
  'GO':      'border-green-600 bg-green-950/50 text-green-300',
  'CAUTION': 'border-amber-600 bg-amber-950/50 text-amber-300',
  'NO-GO':   'border-red-600 bg-red-950/50 text-red-300',
};

const OVERALL_BADGE = {
  'GO':      'bg-green-600 text-white',
  'CAUTION': 'bg-amber-500 text-black',
  'NO-GO':   'bg-red-600 text-white',
};

function fmtClock(ms) {
  if (!ms) return '—';
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function CheckTile({ check }) {
  const style = STATE_STYLE[check.state] ?? STATE_STYLE.unavailable;
  const label = CHECK_LABELS[check.check_id] ?? check.check_id;
  return (
    <div title={check.message || ''}
         className={`rounded-lg border p-4 flex flex-col gap-2 ${style.tile}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-gray-200">{label}</span>
        <span className={`px-2 py-0.5 rounded text-xs font-bold tracking-wide ${style.pill}`}>
          {style.label}
        </span>
      </div>
      <div className="text-xs font-mono text-gray-400 truncate">
        {check.state === 'unavailable'
          ? <span className="text-gray-600">data unavailable</span>
          : (check.value != null ? String(check.value) : '—')}
      </div>
    </div>
  );
}

export default function ChecklistPanel() {
  const [report, setReport]   = useState(null);
  const [connected, setConn]  = useState(false);
  const [lastRecv, setLast]   = useState(null);
  const [, setTick]           = useState(0);
  const sseRef = useRef(null);

  useEffect(() => {
    const es = new EventSource(`${API}/preflight/status`);
    sseRef.current = es;
    es.onopen = () => setConn(true);
    es.onmessage = (e) => {
      try {
        setReport(JSON.parse(e.data));
        setLast(Date.now());
      } catch { /* ignore */ }
    };
    es.onerror = () => setConn(false);
    return () => es.close();
  }, []);

  // 1 Hz ticker so the data-lag readout advances even without new events.
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const overall = report?.overall;
  const checks = report?.checks ?? [];
  const mandatory = checks.filter((c) => c.state !== 'unavailable');
  const stubbed = checks.filter((c) => c.state === 'unavailable');

  const lagS = lastRecv != null ? (Date.now() - lastRecv) / 1000 : null;
  const stale = lagS != null && lagS > 3;

  return (
    <div className="h-[calc(100vh-56px)] overflow-y-auto bg-gray-900 p-6">
      <div className="max-w-4xl mx-auto space-y-5">

        <div>
          <h2 className="text-xl font-bold text-white tracking-wide">Pre-flight Check</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Live GO/NO-GO from the RPi logger stream. Unavailable checks do not block GO.
          </p>
        </div>

        {/* ── Overall verdict ── */}
        <div className={`rounded-xl border-2 p-6 text-center transition-colors
                         ${OVERALL_STYLE[overall] ?? 'border-gray-700 bg-gray-800/50 text-gray-400'}`}>
          <div className={`inline-block px-8 py-3 rounded-full text-3xl font-black tracking-widest
                           ${OVERALL_BADGE[overall] ?? 'bg-gray-700 text-gray-300'}`}>
            {overall ?? 'WAITING'}
          </div>
          <p className="text-sm font-semibold mt-3">
            {overall === 'GO' && 'All mandatory checks pass — safe to arm'}
            {overall === 'CAUTION' && 'A mandatory check is in warning — review before arming'}
            {overall === 'NO-GO' && 'A mandatory check failed — do not arm'}
            {!overall && 'Waiting for preflight stream…'}
          </p>
        </div>

        {/* ── Mandatory checks ── */}
        {mandatory.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2">Mandatory</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {mandatory.map((c) => <CheckTile key={c.check_id} check={c} />)}
            </div>
          </div>
        )}

        {/* ── Stubbed / awaiting data ── */}
        {stubbed.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2">
              Awaiting data
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {stubbed.map((c) => <CheckTile key={c.check_id} check={c} />)}
            </div>
          </div>
        )}

        {/* ── Bottom strip ── */}
        <div className="flex items-center justify-between text-xs text-gray-500 pt-1 border-t border-gray-800">
          <span>
            {connected ? 'stream connected' : 'stream disconnected'}
            {report && <> · last update {fmtClock(report.timestamp_ms)}</>}
          </span>
          {lagS != null && (
            <span className={stale ? 'text-amber-400 font-semibold' : ''}>
              {stale ? `data lag ${lagS.toFixed(0)}s` : `updated ${lagS.toFixed(0)}s ago`}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

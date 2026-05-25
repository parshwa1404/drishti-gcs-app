import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import ChecklistPanel from './ChecklistPanel';

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    FakeEventSource.instances.push(this);
  }
  close() {}
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});

function emit(report) {
  const es = FakeEventSource.instances.at(-1);
  act(() => es.onmessage({ data: JSON.stringify(report) }));
}

const MANDATORY = (overrides = {}) => [
  { check_id: 'rpi_connection',  state: 'pass', value: 'connected', message: 'SSH connected' },
  { check_id: 'logger_active',   state: 'pass', value: 0.2,         message: 'Last frame 0.2s ago' },
  { check_id: 'frame_rate',      state: 'pass', value: 5.1,         message: '5.1 Hz over 10s' },
  { check_id: 'gps_fix',         state: 'pass', value: '19.91, 73.82', message: 'GPS fix valid' },
  { check_id: 'altitude_sane',   state: 'pass', value: 80.0,        message: '80.0 m' },
  { check_id: 'heading_present', state: 'pass', value: 120.0,       message: '120.0°' },
  ...Object.entries(overrides).map(([check_id, o]) => ({ check_id, ...o })),
];

const STUBBED = [
  { check_id: 'gps_hdop',        state: 'unavailable', value: null, message: 'Awaiting RPi logger field: gps_hdop' },
  { check_id: 'satellite_count', state: 'unavailable', value: null, message: 'Awaiting RPi logger field: satellite_count' },
];

function report(overall, mandatoryOverrides) {
  // last override wins per check_id
  const base = MANDATORY();
  const merged = base.map((c) => mandatoryOverrides?.[c.check_id]
    ? { ...c, ...mandatoryOverrides[c.check_id] }
    : c);
  return { overall, checks: [...merged, ...STUBBED], timestamp_ms: 1717490001000 };
}

describe('ChecklistPanel (Panel 6)', () => {
  it('renders all mandatory and stubbed check tiles', () => {
    render(<ChecklistPanel />);
    emit(report('GO'));

    for (const label of ['RPi Connection', 'Logger Active', 'Frame Rate', 'GPS Fix', 'Altitude', 'Heading']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText('GPS HDOP')).toBeInTheDocument();
    expect(screen.getByText('Satellites')).toBeInTheDocument();
  });

  it('shows GO badge when overall is GO', () => {
    render(<ChecklistPanel />);
    emit(report('GO'));
    expect(screen.getByText('GO')).toBeInTheDocument();
  });

  it('shows NO-GO badge when a mandatory check fails', () => {
    render(<ChecklistPanel />);
    emit(report('NO-GO', { gps_fix: { state: 'fail', value: '0.00000, 0.00000', message: 'GPS sentinel 0,0 — no fix' } }));
    expect(screen.getByText('NO-GO')).toBeInTheDocument();
    // failing tile shows its FAIL pill
    expect(screen.getByText('FAIL')).toBeInTheDocument();
  });

  it('renders stubbed checks as grey "data unavailable" with awaited-field hover', () => {
    render(<ChecklistPanel />);
    emit(report('GO'));
    const tile = screen.getByText('GPS HDOP').closest('div[title]');
    expect(tile).toHaveAttribute('title', 'Awaiting RPi logger field: gps_hdop');
    expect(screen.getAllByText('data unavailable').length).toBeGreaterThan(0);
  });
});

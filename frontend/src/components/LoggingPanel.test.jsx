import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import LoggingPanel from './LoggingPanel';

// LoggingPanel opens an SSE EventSource on mount; jsdom has no EventSource, so
// capture the instance and drive onmessage manually.
class FakeEventSource {
  constructor(url) {
    this.url = url;
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

function emit(payload) {
  const es = FakeEventSource.instances.at(-1);
  act(() => es.onmessage({ data: JSON.stringify(payload) }));
}

describe('LoggingPanel altitude display', () => {
  it('renders altitude_m from the live status stream', () => {
    render(<LoggingPanel />);

    emit({
      frames_captured: 42,
      gps_quality: null,
      heading_deg: 220.0,
      disk_mb_remaining: null,
      fix_count: null,
      lat: 19.9172,
      lon: 73.8272,
      altitude_m: 82.3,
      unix_ms: 1717490001000,
      timestamp_ms: 1717490001000,
      connection_status: 'connected',
    });

    expect(screen.getByText('Altitude')).toBeInTheDocument();
    expect(screen.getByText('82.3 m')).toBeInTheDocument();
    expect(screen.getByText('connected')).toBeInTheDocument();
  });
});

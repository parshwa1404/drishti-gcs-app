import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ReplayPanel from './ReplayPanel';

// Leaflet uses browser APIs not present in jsdom
vi.mock('react-leaflet', () => ({
  MapContainer:  ({ children }) => <div data-testid="map">{children}</div>,
  TileLayer:     () => null,
  Polyline:      () => null,
  CircleMarker:  () => null,
  useMap:        () => ({ fitBounds: vi.fn(), panTo: vi.fn() }),
}));
vi.mock('leaflet/dist/leaflet.css', () => ({}));

const SESSION_INFO = {
  session_id: 'test_session',
  total_rows: 100,
  has_overlay: false,
  unix_ms_start: 1_717_490_000_000,
  unix_ms_end:   1_717_490_019_800,
};

const SESSION_LIST = [
  { session_id: 'test_session', path: '/tmp/test_session' },
];

const FRAME_0 = {
  row: 0,
  unix_ms: 1_717_490_000_000,
  frame_path: 'frames/1717490000000.jpg',
  lat: 19.91,
  lon: 73.82,
  altitude_m: 82.4,
  heading_deg: 210.5,
  hdop: 1.2,
  satellite_count: 8,
  disk_free_gb: 12.34,
  jpeg_available: true,
  overlay: null,
};

function mockFetch(url) {
  if (url.includes('/replay/sessions')) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(SESSION_LIST) });
  }
  if (url.includes('/replay/open/')) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(SESSION_INFO) });
  }
  if (url.match(/\/replay\/[^/]+\/frame\/\d+/)) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(FRAME_0) });
  }
  if (url.match(/\/replay\/[^/]+\/jpeg\/\d+/)) {
    return Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(['img'], { type: 'image/jpeg' })) });
  }
  return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
}

beforeEach(() => { vi.spyOn(global, 'fetch').mockImplementation(mockFetch); });
afterEach(() => { vi.restoreAllMocks(); });

describe('ReplayPanel', () => {
  it('renders session dropdown button', () => {
    render(<ReplayPanel />);
    expect(screen.getByRole('button', { name: /Select Session/ })).toBeInTheDocument();
  });

  it('shows empty state prompt before session is selected', () => {
    render(<ReplayPanel />);
    expect(screen.getByText(/load a flight recording/i)).toBeInTheDocument();
  });

  it('renders session list when dropdown opened', async () => {
    render(<ReplayPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Select Session/ }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/replay/sessions')));
    await waitFor(() => expect(screen.getByText('test_session')).toBeInTheDocument());
  });

  it('loads session metadata after selecting from list', async () => {
    render(<ReplayPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Select Session/ }));
    await waitFor(() => screen.getByText('test_session'));
    fireEvent.click(screen.getByText('test_session'));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/replay/open/test_session'), expect.any(Object)));
    await waitFor(() => expect(screen.getByText(/100 frames/)).toBeInTheDocument());
  });

  it('shows frame counter row N / total after load', async () => {
    render(<ReplayPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Select Session/ }));
    await waitFor(() => screen.getByText('test_session'));
    fireEvent.click(screen.getByText('test_session'));
    await waitFor(() => screen.getByText(/1 \/ 100/));
  });

  it('metadata shows blank cells for missing columns when null', async () => {
    const nullFrame = { ...FRAME_0, hdop: null, satellite_count: null, disk_free_gb: null };
    fetch.mockImplementation((url) => {
      if (url.match(/\/replay\/[^/]+\/frame\/\d+/)) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(nullFrame) });
      }
      return mockFetch(url);
    });
    render(<ReplayPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Select Session/ }));
    await waitFor(() => screen.getByText('test_session'));
    fireEvent.click(screen.getByText('test_session'));
    await waitFor(() => screen.getByText('HDOP'));
    const rows = screen.getAllByText('—');
    // Should render — for null hdop, satellite_count, disk_free_gb
    expect(rows.length).toBeGreaterThanOrEqual(3);
  });

  it('scrubber moves current row when changed', async () => {
    render(<ReplayPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Select Session/ }));
    await waitFor(() => screen.getByText('test_session'));
    fireEvent.click(screen.getByText('test_session'));
    await waitFor(() => screen.getByRole('slider'));
    const slider = screen.getByRole('slider');
    fireEvent.change(slider, { target: { value: '42' } });
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/frame/42')));
  });

  it('prev/next low-inlier buttons greyed when no overlay', async () => {
    render(<ReplayPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Select Session/ }));
    await waitFor(() => screen.getByText('test_session'));
    fireEvent.click(screen.getByText('test_session'));
    await waitFor(() => screen.getByText(/Low ▶/));
    const fwd = screen.getByTitle('Next low-inlier frame');
    const bwd = screen.getByTitle('Prev low-inlier frame');
    expect(fwd).toBeDisabled();
    expect(bwd).toBeDisabled();
  });
});

import { Fragment, useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Clock, RefreshCw, RotateCcw } from 'lucide-react';
import type { WebhookEvent, DeliveryAttempt, ToastType } from '../types';
import { fmt, shortId, statusColor } from '../utils/helpers';

interface EventsPageProps {
  apiBase: string;
  apiFetch: typeof fetch;
  toast: (message: string, type?: ToastType) => void;
}

export const EventsPage = ({ apiBase, apiFetch, toast }: EventsPageProps) => {
  const [events, setEvents] = useState<WebhookEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [attempts, setAttempts] = useState<Record<string, DeliveryAttempt[]>>({});

  const fetchEvents = useCallback(async () => {
    try {
      const res = await apiFetch(`${apiBase}/events?limit=100`);
      if (res.ok) setEvents(await res.json());
    } finally {
      setLoading(false);
    }
  }, [apiBase, apiFetch]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  const handleRefresh = async () => {
    setLoading(true);
    await fetchEvents();
  };

  const toggleExpand = async (eventId: string) => {
    if (expanded === eventId) {
      setExpanded(null);
      return;
    }
    setExpanded(eventId);
    if (!attempts[eventId]) {
      const res = await apiFetch(`${apiBase}/events/${eventId}/attempts`);
      if (res.ok) {
        const data = await res.json();
        setAttempts(prev => ({ ...prev, [eventId]: data }));
      }
    }
  };

  const handleReplay = async (eventId: string) => {
    const res = await apiFetch(`${apiBase}/events/${eventId}/replay`, { method: 'POST' });
    if (res.ok) toast('Event queued for replay');
    else toast('Replay failed', 'error');
  };

  return (
    <div className="animate-in flex-col gap-6">
      <header className="page-header">
        <div>
          <h1 className="page-title">Events Log</h1>
          <p className="page-sub">Every webhook received, with full delivery attempt history.</p>
        </div>
        <button className="btn btn-secondary" onClick={handleRefresh} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh
        </button>
      </header>

      <div className="card">
        <div className="table-wrap">
          {loading ? (
            <div className="empty-state"><div className="spinner" /></div>
          ) : events.length === 0 ? (
            <div className="empty-state">No events yet. Send a webhook to one of your endpoints to get started.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: '28px' }} />
                  <th>Event ID</th>
                  <th>Endpoint</th>
                  <th>Idempotency Key</th>
                  <th>Received At</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {events.map(ev => (
                  <Fragment key={ev.id}>
                    <tr
                      className={expanded === ev.id ? 'row-expanded' : ''}
                      style={{ cursor: 'pointer' }}
                      onClick={() => toggleExpand(ev.id)}
                    >
                      <td>
                        {expanded === ev.id ? (
                          <ChevronDown size={14} style={{ color: 'var(--accent)' }} />
                        ) : (
                          <ChevronRight size={14} style={{ color: 'var(--text-muted)' }} />
                        )}
                      </td>
                      <td className="mono">{shortId(ev.id)}</td>
                      <td className="mono">{shortId(ev.endpoint_id)}</td>
                      <td className="mono">{ev.idempotency_key ?? <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                      <td>{fmt(ev.received_at)}</td>
                      <td onClick={e => e.stopPropagation()}>
                        <button className="btn btn-secondary btn-sm" onClick={() => handleReplay(ev.id)}>
                          <RotateCcw size={12} /> Replay
                        </button>
                      </td>
                    </tr>
                    {expanded === ev.id && (
                      <tr>
                        <td colSpan={6} style={{ padding: 0, background: 'var(--bg-elevated)' }}>
                          <div className="expand-panel flex-col gap-4">
                            <div>
                              <div className="section-label" style={{ marginBottom: '0.5rem' }}>Payload</div>
                              <pre className="code-block">{JSON.stringify(ev.request_body, null, 2)}</pre>
                            </div>
                            <div>
                              <div className="section-label" style={{ marginBottom: '0.5rem' }}>
                                Delivery Attempts ({(attempts[ev.id] ?? []).length})
                              </div>
                              {!attempts[ev.id] ? (
                                <div className="spinner" />
                              ) : attempts[ev.id].length === 0 ? (
                                <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>No delivery attempts recorded.</p>
                              ) : (
                                <div className="timeline">
                                  {attempts[ev.id].map(a => (
                                    <div key={a.id} className="timeline-step">
                                      <div className={`timeline-dot ${a.response_status && a.response_status < 300 ? 'dot-green' : 'dot-red'}`} />
                                      <div className="timeline-body">
                                        <div className="flex items-center gap-2 flex-wrap">
                                          <span className="badge badge-method">Attempt #{a.attempt_number + 1}</span>
                                          {a.response_status && (
                                            <span className={`badge ${statusColor(a.response_status)}`}>{a.response_status}</span>
                                          )}
                                          {a.duration_ms != null && (
                                            <span className="badge badge-inactive"><Clock size={10} /> {a.duration_ms}ms</span>
                                          )}
                                        </div>
                                        <div className="mono mt-2" style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>{a.request_url}</div>
                                        {a.error && <div className="error-text">{a.error}</div>}
                                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>{fmt(a.attempted_at)}</div>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};

import { Fragment, useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ChevronDown, ChevronRight, RefreshCw, RotateCcw, Trash2 } from 'lucide-react';
import type { DlqEvent, ToastType } from '../types';
import { fmt, shortId } from '../utils/helpers';

interface DlqPageProps {
  apiBase: string;
  apiFetch: typeof fetch;
  toast: (message: string, type?: ToastType) => void;
}

export const DlqPage = ({ apiBase, apiFetch, toast }: DlqPageProps) => {
  const [items, setItems] = useState<DlqEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [showDiscarded, setShowDiscarded] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const fetchDlq = useCallback(async (includeDiscarded = showDiscarded) => {
    try {
      const res = await apiFetch(`${apiBase}/dlq?include_discarded=${includeDiscarded}`);
      if (res.ok) setItems(await res.json());
    } finally {
      setLoading(false);
    }
  }, [apiBase, apiFetch, showDiscarded]);

  useEffect(() => {
    fetchDlq();
  }, [fetchDlq]);

  const handleRefresh = async () => {
    setLoading(true);
    await fetchDlq();
  };

  const toggleFilter = () => {
    const next = !showDiscarded;
    setShowDiscarded(next);
    setLoading(true);
    fetchDlq(next);
  };

  const handleReplay = async (eventId: string) => {
    setLoading(true);
    const res = await apiFetch(`${apiBase}/events/${eventId}/replay`, { method: 'POST' });
    if (res.ok) {
      toast('Event queued for replay');
      fetchDlq();
    } else {
      setLoading(false);
      toast('Replay failed', 'error');
    }
  };

  const handleDiscard = async (eventId: string) => {
    setLoading(true);
    const res = await apiFetch(`${apiBase}/dlq/${eventId}/discard`, { method: 'POST' });
    if (res.ok) {
      toast('Event discarded');
      fetchDlq();
    } else {
      setLoading(false);
      toast('Discard failed', 'error');
    }
  };

  const handleRestore = async (eventId: string) => {
    setLoading(true);
    const res = await apiFetch(`${apiBase}/dlq/${eventId}/restore`, { method: 'POST' });
    if (res.ok) {
      toast('Event restored to queue');
      fetchDlq();
    } else {
      setLoading(false);
      toast('Restore failed', 'error');
    }
  };

  const pending = items.filter(i => !i.is_discarded).length;
  const discarded = items.filter(i => i.is_discarded).length;

  return (
    <div className="animate-in flex-col gap-6">
      <header className="page-header">
        <div>
          <h1 className="page-title">Dead Letter Queue</h1>
          <p className="page-sub">Events that exhausted all delivery retries. Replay or discard them here.</p>
        </div>
        <div className="flex gap-2">
          <button className={`btn ${showDiscarded ? 'btn-secondary' : 'btn-primary'} btn-sm`} onClick={toggleFilter}>
            {showDiscarded ? 'Hide Discarded' : 'Show Discarded'}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={handleRefresh} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh
          </button>
        </div>
      </header>

      <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
        <div className="stat-card">
          <div className="stat-label">Pending Failures</div>
          <div className="stat-value" style={{ color: pending > 0 ? 'var(--danger)' : 'var(--success)' }}>{pending}</div>
          <div className="stat-sub">Awaiting action</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Discarded</div>
          <div className="stat-value" style={{ color: 'var(--text-muted)' }}>{discarded}</div>
          <div className="stat-sub">Acknowledged & hidden</div>
        </div>
      </div>

      <div className="card">
        <div className="table-wrap">
          {loading ? (
            <div className="empty-state"><div className="spinner" /></div>
          ) : items.length === 0 ? (
            <div className="empty-state">
              <CheckCircle2 size={36} style={{ color: 'var(--success)', opacity: 0.7 }} />
              <div className="empty-state-title" style={{ marginTop: '0.5rem' }}>Dead Letter Queue is clear</div>
              <div>All deliveries have been successful.</div>
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: '28px' }} />
                  <th>Destination URL</th>
                  <th>Event ID</th>
                  <th>Last Error</th>
                  <th>Status</th>
                  <th>Attempts</th>
                  <th>Received At</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map(item => (
                  <Fragment key={item.event_id}>
                    <tr
                      className={expanded === item.event_id ? 'row-expanded' : ''}
                      style={{ cursor: 'pointer', opacity: item.is_discarded ? 0.5 : 1 }}
                      onClick={() => setExpanded(expanded === item.event_id ? null : item.event_id)}
                    >
                      <td>
                        {expanded === item.event_id ? (
                          <ChevronDown size={14} style={{ color: 'var(--accent)' }} />
                        ) : (
                          <ChevronRight size={14} style={{ color: 'var(--text-muted)' }} />
                        )}
                      </td>
                      <td className="mono" style={{ maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {item.last_url ?? '—'}
                      </td>
                      <td className="mono">{shortId(item.event_id)}</td>
                      <td>
                        <span className="error-text" style={{ fontSize: '0.78rem', display: 'block', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {item.last_error ?? (item.last_status ? `HTTP ${item.last_status}` : 'Unknown')}
                        </span>
                      </td>
                      <td>
                        {item.is_discarded ? (
                          <span className="badge badge-inactive">Discarded</span>
                        ) : (
                          <span className="badge badge-error">Failed</span>
                        )}
                      </td>
                      <td><span className="badge badge-warning">{item.total_attempts}x</span></td>
                      <td>{fmt(item.received_at)}</td>
                      <td onClick={e => e.stopPropagation()}>
                        <div className="flex gap-2">
                          <button className="btn btn-secondary btn-sm" onClick={() => handleReplay(item.event_id)}>
                            <RotateCcw size={12} /> Replay
                          </button>
                          {item.is_discarded ? (
                            <button className="btn btn-secondary btn-sm" onClick={() => handleRestore(item.event_id)}>
                              Restore
                            </button>
                          ) : (
                            <button className="btn btn-danger btn-sm" onClick={() => handleDiscard(item.event_id)}>
                              <Trash2 size={12} /> Discard
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {expanded === item.event_id && (
                      <tr>
                        <td colSpan={8} style={{ padding: 0, background: 'var(--bg-elevated)' }}>
                          <div className="expand-panel flex-col gap-3">
                            <div>
                              <div className="section-label" style={{ marginBottom: '0.5rem' }}>Original Payload</div>
                              <pre className="code-block">{JSON.stringify(item.request_body, null, 2)}</pre>
                            </div>
                            {item.last_error && (
                              <div>
                                <div className="section-label" style={{ marginBottom: '0.5rem' }}>Full Error</div>
                                <pre className="code-block" style={{ color: '#fca5a5' }}>{item.last_error}</pre>
                              </div>
                            )}
                            {item.discarded_at && (
                              <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                                Discarded at: {fmt(item.discarded_at)}
                              </div>
                            )}
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

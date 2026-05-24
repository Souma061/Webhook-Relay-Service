import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import type { Endpoint, WebhookEvent, DlqEvent } from '../types';
import { fmt, shortId } from '../utils/helpers';

export const OverviewPage = () => {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [events, setEvents] = useState<WebhookEvent[]>([]);
  const [dlq, setDlq] = useState<DlqEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    try {
      const [epRes, evRes, dlqRes] = await Promise.all([
        fetch('/api/endpoints/'),
        fetch('/api/events/'),
        fetch('/api/dlq/'),
      ]);
      if (epRes.ok) setEndpoints(await epRes.json());
      if (evRes.ok) setEvents(await evRes.json());
      if (dlqRes.ok) setDlq(await dlqRes.json());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleRefresh = async () => {
    setLoading(true);
    await fetchAll();
  };

  return (
    <div className="animate-in flex-col gap-8">
      <header className="page-header">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-sub">Real-time metrics for your webhook infrastructure.</p>
        </div>
        <button className="btn btn-secondary" onClick={handleRefresh} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh
        </button>
      </header>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-label">Total Endpoints</div>
          <div className="stat-value">{endpoints.length}</div>
          <div className="stat-sub">{endpoints.filter(e => e.is_active).length} active</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Events</div>
          <div className="stat-value">{events.length}</div>
          <div className="stat-sub">Ingested</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Dead Letter Queue</div>
          <div className="stat-value" style={{ color: dlq.length > 0 ? 'var(--danger)' : 'var(--success)' }}>
            {dlq.length}
          </div>
          <div className="stat-sub">Pending failures</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">System Status</div>
          <div className="flex items-center gap-2 mt-2" style={{ color: 'var(--success)' }}>
            <div className="dot dot-green dot-pulse" />
            <span style={{ fontWeight: 600 }}>All Systems Normal</span>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="card-header-title">Recent Endpoints</div>
          <Link to="/endpoints" className="btn btn-sm btn-secondary">View All</Link>
        </div>
        <div className="table-wrap">
          {endpoints.length === 0 ? (
            <div className="empty-state">No endpoints configured yet.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>ID</th>
                  <th>Status</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {endpoints.slice(0, 5).map(ep => (
                  <tr key={ep.id}>
                    <td className="td-primary">{ep.name}</td>
                    <td className="mono">{shortId(ep.id)}</td>
                    <td>
                      <span className={`badge ${ep.is_active ? 'badge-active' : 'badge-inactive'}`}>
                        {ep.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td>{fmt(ep.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};

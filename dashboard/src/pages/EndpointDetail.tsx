import { type FormEvent, useCallback, useEffect, useState } from 'react';
import { KeyRound, Plus, Trash2 } from 'lucide-react';
import type { Endpoint, RouteConfig, ToastType } from '../types';
import { ConfirmModal, type ConfirmModalConfig } from '../components/ConfirmModal';

interface EndpointDetailProps {
  endpoint: Endpoint;
  onBack: () => void;
  toast: (message: string, type?: ToastType) => void;
}

export const EndpointDetail = ({ endpoint, onBack, toast }: EndpointDetailProps) => {
  const [routes, setRoutes] = useState<RouteConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAddingRoute, setIsAddingRoute] = useState(false);
  const [newRoute, setNewRoute] = useState({ name: '', url: '', method: 'POST', filter_expression: '' });
  const [confirmConfig, setConfirmConfig] = useState<ConfirmModalConfig | null>(null);

  const fetchRoutes = useCallback(async () => {
    try {
      const res = await fetch(`/api/endpoints/${endpoint.id}/routes`);
      if (res.ok) setRoutes(await res.json());
    } finally {
      setLoading(false);
    }
  }, [endpoint.id]);

  useEffect(() => {
    fetchRoutes();
  }, [fetchRoutes]);

  const handleAddRoute = async (e: FormEvent) => {
    e.preventDefault();
    if (!newRoute.name || !newRoute.url) return;
    setLoading(true);
    const payload = {
      name: newRoute.name,
      url: newRoute.url,
      method: newRoute.method,
      filter_expression: newRoute.filter_expression.trim() || null,
    };
    const res = await fetch(`/api/endpoints/${endpoint.id}/routes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      setIsAddingRoute(false);
      setNewRoute({ name: '', url: '', method: 'POST', filter_expression: '' });
      fetchRoutes();
      toast('Route added successfully');
    } else {
      setLoading(false);
      const errData = await res.json().catch(() => ({}));
      // FastAPI ValueError from field validator is returned in detail[0].msg
      const errMsg = errData.detail?.[0]?.msg || errData.detail || 'Failed to add route';
      toast(errMsg, 'error');
    }
  };

  const handleRotateSecret = () => {
    setConfirmConfig({
      title: 'Rotate Ingestion Secret',
      message: 'Are you sure you want to rotate the HMAC secret? Existing integrators will fail immediately until updated with the new secret.',
      confirmText: 'Rotate Secret',
      isDanger: true,
      onConfirm: async () => {
        setConfirmConfig(null);
        const res = await fetch(`/api/endpoints/${endpoint.id}/rotate`, { method: 'POST' });
        if (res.ok) toast('Secret rotated — update your webhook source now');
        else toast('Failed to rotate secret', 'error');
      },
      onCancel: () => setConfirmConfig(null),
    });
  };

  const handleDeleteRoute = (routeId: string) => {
    setConfirmConfig({
      title: 'Delete Route',
      message: 'Are you sure you want to delete this delivery route? This destination will no longer receive webhook events.',
      confirmText: 'Delete Route',
      isDanger: true,
      onConfirm: async () => {
        setConfirmConfig(null);
        setLoading(true);
        const res = await fetch(`/api/endpoints/routes/${routeId}`, { method: 'DELETE' });
        if (res.ok) {
          fetchRoutes();
          toast('Route deleted');
        } else {
          setLoading(false);
          toast('Failed to delete route', 'error');
        }
      },
      onCancel: () => setConfirmConfig(null),
    });
  };

  return (
    <div className="animate-in flex-col gap-6">
      <div className="flex items-center gap-4">
        <button className="btn btn-secondary btn-sm" onClick={onBack}>&larr; Back</button>
        <h2 className="page-title" style={{ margin: 0 }}>{endpoint.name}</h2>
        <span className={`badge ${endpoint.is_active ? 'badge-active' : 'badge-inactive'}`}>
          {endpoint.is_active ? 'Active' : 'Inactive'}
        </span>
      </div>

      <div className="card card-p flex-col gap-4">
        <div className="section-label">Ingestion Details</div>
        <div className="flex-col gap-3">
          <div className="flex justify-between items-center">
            <span className="text-muted" style={{ fontSize: '0.82rem' }}>Webhook URL</span>
            <code className="mono" style={{ color: 'var(--text-primary)' }}>
              http://localhost:8000/hooks/{endpoint.id}
            </code>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-muted" style={{ fontSize: '0.82rem' }}>HMAC Secret</span>
            <div className="flex items-center gap-2">
              <span className="secret-box">{endpoint.hmac_secret}</span>
              <button className="btn btn-secondary btn-icon" onClick={handleRotateSecret} title="Rotate Secret">
                <KeyRound size={14} />
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="card-header-title">Delivery Routes</div>
          <button className="btn btn-primary btn-sm" onClick={() => setIsAddingRoute(true)}>
            <Plus size={14} /> Add Route
          </button>
        </div>

        {isAddingRoute && (
          <div className="expand-panel">
            <form onSubmit={handleAddRoute} className="flex-col gap-4" style={{ display: 'flex', flexDirection: 'column', gap: '1rem', width: '100%' }}>
              <div className="flex gap-4 items-start flex-wrap w-full" style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', width: '100%' }}>
                <div className="form-field flex-1" style={{ flex: 1, minWidth: '160px' }}>
                  <label className="form-label">Route Name</label>
                  <input required placeholder="e.g. My Backend" value={newRoute.name}
                    onChange={e => setNewRoute({ ...newRoute, name: e.target.value })} />
                </div>
                <div className="form-field" style={{ width: '110px' }}>
                  <label className="form-label">Method</label>
                  <select value={newRoute.method} onChange={e => setNewRoute({ ...newRoute, method: e.target.value })}>
                    <option>POST</option><option>PUT</option><option>PATCH</option>
                  </select>
                </div>
                <div className="form-field flex-2" style={{ flex: 2, minWidth: '240px' }}>
                  <label className="form-label">Destination URL</label>
                  <input required type="url" placeholder="https://api.example.com/webhooks"
                    value={newRoute.url} onChange={e => setNewRoute({ ...newRoute, url: e.target.value })} />
                </div>
              </div>

              <div className="form-field w-full" style={{ width: '100%' }}>
                <label className="form-label" style={{ display: 'flex', justifyContent: 'between', alignItems: 'center', width: '100%' }}>
                  <span>Filter Condition (JMESPath - Optional)</span>
                </label>
                <input placeholder="e.g. event_type == 'payment.succeeded' or order.amount > `100`" value={newRoute.filter_expression}
                  onChange={e => setNewRoute({ ...newRoute, filter_expression: e.target.value })} />
                <span className="text-muted" style={{ fontSize: '0.75rem', marginTop: '0.25rem' }}>
                  Evaluate incoming payload. Leave blank to match all webhooks. Reference fields directly: <code>event_type == 'payment.succeeded'</code>.
                </span>
              </div>

              <div className="flex items-center gap-2" style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
                <button type="submit" className="btn btn-primary">Save Route</button>
                <button type="button" className="btn btn-secondary" onClick={() => {
                  setIsAddingRoute(false);
                  setNewRoute({ name: '', url: '', method: 'POST', filter_expression: '' });
                }}>Cancel</button>
              </div>
            </form>
          </div>
        )}

        <div className="table-wrap">
          {loading ? (
            <div className="empty-state"><div className="spinner" /></div>
          ) : routes.length === 0 ? (
            <div className="empty-state">No routes configured. Webhooks received here will be discarded.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Method</th>
                  <th>Destination URL</th>
                  <th>Filter Condition</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {routes.map(r => (
                  <tr key={r.id}>
                    <td className="td-primary">{r.name}</td>
                    <td><span className="badge badge-method">{r.method}</span></td>
                    <td className="mono">{r.url}</td>
                    <td>
                      {r.filter_expression ? (
                        <code className="mono" style={{ fontSize: '0.8rem', background: 'rgba(255,255,255,0.06)', padding: '3px 8px', borderRadius: '4px', color: 'var(--text-secondary)' }}>
                          {r.filter_expression}
                        </code>
                      ) : (
                        <span className="text-muted" style={{ fontSize: '0.82rem', fontStyle: 'italic' }}>All Events</span>
                      )}
                    </td>
                    <td>
                      <span className={`badge ${r.is_active ? 'badge-active' : 'badge-inactive'}`}>
                        {r.is_active ? 'Active' : 'Paused'}
                      </span>
                    </td>
                    <td>
                      <button className="btn btn-danger btn-icon" onClick={() => handleDeleteRoute(r.id)} title="Delete Route">
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      <ConfirmModal config={confirmConfig} />
    </div>
  );
};

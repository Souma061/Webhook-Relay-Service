import { type FormEvent, useCallback, useEffect, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import type { Endpoint, ToastType } from '../types';
import { fmt } from '../utils/helpers';
import { EndpointDetail } from './EndpointDetail';

interface EndpointsPageProps {
  toast: (message: string, type?: ToastType) => void;
}

export const EndpointsPage = ({ toast }: EndpointsPageProps) => {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [selected, setSelected] = useState<Endpoint | null>(null);

  const fetchEndpoints = useCallback(async () => {
    try {
      const res = await fetch('/api/endpoints/');
      if (res.ok) setEndpoints(await res.json());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchEndpoints();
  }, [fetchEndpoints]);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setLoading(true);
    const res = await fetch('/api/endpoints/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName }),
    });
    if (res.ok) {
      setNewName('');
      setIsCreating(false);
      fetchEndpoints();
      toast('Endpoint created');
    } else {
      setLoading(false);
      toast('Failed to create endpoint', 'error');
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Delete endpoint "${name}"? This will remove all routes.`)) return;
    setLoading(true);
    const res = await fetch(`/api/endpoints/${id}`, { method: 'DELETE' });
    if (res.ok) {
      fetchEndpoints();
      toast('Endpoint deleted');
    } else {
      setLoading(false);
      toast('Failed to delete endpoint', 'error');
    }
  };

  if (selected) {
    return (
      <EndpointDetail
        endpoint={selected}
        onBack={() => {
          setSelected(null);
          setLoading(true);
          fetchEndpoints();
        }}
        toast={toast}
      />
    );
  }

  return (
    <div className="animate-in flex-col gap-6">
      <header className="page-header">
        <div>
          <h1 className="page-title">Endpoints</h1>
          <p className="page-sub">Manage your webhook ingestion URLs and delivery routes.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setIsCreating(true)}>
          <Plus size={16} /> New Endpoint
        </button>
      </header>

      {isCreating && (
        <div className="inline-form animate-in">
          <form onSubmit={handleCreate} className="flex gap-4 items-end">
            <div className="form-field flex-1">
              <label className="form-label">Endpoint Name</label>
              <input
                type="text"
                placeholder="e.g. Stripe Webhooks"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                autoFocus
                required
              />
            </div>
            <button type="submit" className="btn btn-primary">Create</button>
            <button type="button" className="btn btn-secondary" onClick={() => setIsCreating(false)}>Cancel</button>
          </form>
        </div>
      )}

      <div className="card">
        <div className="table-wrap">
          {loading ? (
            <div className="empty-state"><div className="spinner" /></div>
          ) : endpoints.length === 0 ? (
            <div className="empty-state">No endpoints found. Create one to get started.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>ID</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {endpoints.map(ep => (
                  <tr key={ep.id}>
                    <td className="td-primary">{ep.name}</td>
                    <td className="mono">{ep.id}</td>
                    <td>
                      <span className={`badge ${ep.is_active ? 'badge-active' : 'badge-inactive'}`}>
                        {ep.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td>{fmt(ep.created_at)}</td>
                    <td>
                      <div className="flex gap-2">
                        <button className="btn btn-secondary btn-sm" onClick={() => setSelected(ep)}>Configure</button>
                        <button
                          className="btn btn-danger btn-icon"
                          onClick={() => handleDelete(ep.id, ep.name)}
                          title="Delete endpoint"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </td>
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

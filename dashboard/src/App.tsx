import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Activity, Webhook, Database, Settings, RefreshCw, Plus, Server, CheckCircle2, XCircle, Clock, Trash2, KeyRound } from 'lucide-react';
import './index.css';

// --- Types ---
interface Endpoint {
  id: string;
  name: string;
  is_active: boolean;
  hmac_secret: string;
  created_at: string;
}

interface RouteType {
  id: string;
  name: string;
  url: string;
  method: string;
  is_active: boolean;
  timeout_ms: number;
  max_retries: number;
  created_at: string;
}

interface EventType {
  id: string;
  endpoint_id: string;
  idempotency_key: string | null;
  request_body: any;
  received_at: string;
}

interface DeliveryAttempt {
  id: string;
  event_id: string;
  route_id: string;
  attempt_number: number;
  request_url: string;
  response_status: number | null;
  error: string | null;
  duration_ms: number | null;
  attempted_at: string;
}

// --- Components ---

const Sidebar = () => {
  const location = useLocation();
  
  const navItems = [
    { path: '/', icon: <Activity size={18} />, label: 'Overview' },
    { path: '/endpoints', icon: <Webhook size={18} />, label: 'Endpoints' },
    { path: '/events', icon: <Database size={18} />, label: 'Events Log' },
    { path: '/settings', icon: <Settings size={18} />, label: 'Settings' },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="brand-icon">
          <Server size={20} color="#fff" strokeWidth={2.5} />
        </div>
        <div className="brand-name">Relay<span>HQ</span></div>
      </div>
      
      <nav className="nav">
        {navItems.map((item) => (
          <Link 
            key={item.path} 
            to={item.path}
            className={`nav-link ${location.pathname === item.path ? 'active' : ''}`}
          >
            {item.icon}
            {item.label}
          </Link>
        ))}
      </nav>
      
      <div className="nav-divider"></div>
      <div className="sidebar-footer">
        Webhook Relay Service<br/>Phase 2 Architecture<br/>v1.0.0
      </div>
    </aside>
  );
};

// --- Pages ---

const DashboardHome = () => {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [events, setEvents] = useState<EventType[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [epRes, evRes] = await Promise.all([
        fetch('/api/endpoints/'),
        fetch('/api/events/') // Assuming this exists or returns []
      ]);
      if (epRes.ok) setEndpoints(await epRes.json());
      if (evRes.ok) setEvents(await evRes.json());
    } catch (error) {
      console.error("Fetch failed", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, []);

  return (
    <div className="animate-in flex-col gap-8">
      <header className="page-header">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-sub">Real-time metrics for your webhook infrastructure.</p>
        </div>
        <button className="btn btn-secondary" onClick={fetchData} disabled={loading}>
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
          <div className="stat-label">System Status</div>
          <div className="flex items-center gap-2 mt-2" style={{color: 'var(--success)'}}>
            <div className="dot dot-green dot-pulse"></div>
            <span style={{fontWeight: 600}}>All Systems Normal</span>
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
              <thead><tr><th>Name</th><th>ID</th><th>Status</th><th>Created</th></tr></thead>
              <tbody>
                {endpoints.slice(0, 5).map(ep => (
                  <tr key={ep.id}>
                    <td className="td-primary">{ep.name}</td>
                    <td className="mono">{ep.id.substring(0,8)}...</td>
                    <td>
                      <span className={`badge ${ep.is_active ? 'badge-active' : 'badge-inactive'}`}>
                        {ep.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td>{new Date(ep.created_at).toLocaleString()}</td>
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

const EndpointDetail = ({ endpoint, onBack }: { endpoint: Endpoint, onBack: () => void }) => {
  const [routes, setRoutes] = useState<RouteType[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAddingRoute, setIsAddingRoute] = useState(false);
  const [newRoute, setNewRoute] = useState({ name: '', url: '', method: 'POST' });

  const fetchRoutes = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/endpoints/${endpoint.id}/routes`);
      if (res.ok) setRoutes(await res.json());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchRoutes(); }, [endpoint.id]);

  const handleAddRoute = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRoute.name || !newRoute.url) return;
    try {
      const res = await fetch(`/api/endpoints/${endpoint.id}/routes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newRoute)
      });
      if (res.ok) {
        setIsAddingRoute(false);
        setNewRoute({ name: '', url: '', method: 'POST' });
        fetchRoutes();
      }
    } catch (e) { console.error(e); }
  };

  const handleRotateSecret = async () => {
    if (!confirm("Rotate secret? Existing integrators will fail immediately.")) return;
    try {
      const res = await fetch(`/api/endpoints/${endpoint.id}/rotate`, { method: 'POST' });
      if (res.ok) {
         alert("Secret rotated! Refresh the endpoints list to see the new secret.");
      }
    } catch (e) { console.error(e); }
  };

  return (
    <div className="animate-in flex-col gap-6">
      <div className="flex items-center gap-4">
        <button className="btn btn-secondary btn-sm" onClick={onBack}>&larr; Back</button>
        <h2 className="page-title" style={{margin: 0}}>{endpoint.name}</h2>
        <span className={`badge ${endpoint.is_active ? 'badge-active' : 'badge-inactive'}`}>
          {endpoint.is_active ? 'Active' : 'Inactive'}
        </span>
      </div>

      <div className="card card-p flex-col gap-4">
        <div className="section-label">Ingestion Details</div>
        <div className="flex-col gap-2">
          <div className="flex justify-between items-center">
            <span className="text-muted text-sm">Endpoint URL</span>
            <code className="mono" style={{color: 'var(--text-primary)'}}>
              http://localhost:8000/hooks/{endpoint.id}
            </code>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-muted text-sm">HMAC Secret (Keep secure!)</span>
            <div className="flex items-center gap-2">
              <span className="secret-box">{endpoint.hmac_secret}</span>
              <button className="btn btn-secondary btn-icon" onClick={handleRotateSecret} title="Rotate Secret"><RefreshCw size={14}/></button>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="card-header-title">Delivery Routes</div>
          <button className="btn btn-primary btn-sm" onClick={() => setIsAddingRoute(true)}>
            <Plus size={14}/> Add Route
          </button>
        </div>
        
        {isAddingRoute && (
          <div className="expand-panel">
            <form onSubmit={handleAddRoute} className="flex gap-4 items-start">
              <div className="form-field flex-1">
                <label className="form-label">Route Name</label>
                <input required placeholder="e.g. My Backend" value={newRoute.name} onChange={e => setNewRoute({...newRoute, name: e.target.value})} />
              </div>
              <div className="form-field" style={{width: '120px'}}>
                <label className="form-label">Method</label>
                <select value={newRoute.method} onChange={e => setNewRoute({...newRoute, method: e.target.value})}>
                  <option>POST</option>
                  <option>PUT</option>
                  <option>PATCH</option>
                </select>
              </div>
              <div className="form-field flex-1">
                <label className="form-label">Destination URL</label>
                <input required type="url" placeholder="https://api.example.com/webhooks" value={newRoute.url} onChange={e => setNewRoute({...newRoute, url: e.target.value})} />
              </div>
              <div className="flex items-center gap-2" style={{marginTop: '1.4rem'}}>
                 <button type="submit" className="btn btn-primary">Save</button>
                 <button type="button" className="btn btn-secondary" onClick={() => setIsAddingRoute(false)}>Cancel</button>
              </div>
            </form>
          </div>
        )}

        <div className="table-wrap">
          {loading ? (
             <div className="empty-state"><div className="spinner"></div></div>
          ) : routes.length === 0 ? (
            <div className="empty-state">No routes configured. Webhooks received here will be discarded.</div>
          ) : (
            <table>
              <thead><tr><th>Name</th><th>Method</th><th>URL</th><th>Status</th></tr></thead>
              <tbody>
                {routes.map(r => (
                  <tr key={r.id}>
                    <td className="td-primary">{r.name}</td>
                    <td><span className="badge badge-method">{r.method}</span></td>
                    <td className="mono">{r.url}</td>
                    <td>
                      <span className={`badge ${r.is_active ? 'badge-active' : 'badge-inactive'}`}>
                        {r.is_active ? 'Active' : 'Paused'}
                      </span>
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

const EndpointsPage = () => {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [selectedEndpoint, setSelectedEndpoint] = useState<Endpoint | null>(null);

  const fetchEndpoints = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/endpoints/');
      if (response.ok) setEndpoints(await response.json());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchEndpoints(); }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    try {
      const response = await fetch('/api/endpoints/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName })
      });
      if (response.ok) {
        setNewName(''); setIsCreating(false); fetchEndpoints();
      } else {
        alert("Failed to create endpoint: " + await response.text());
      }
    } catch (error: any) { 
      console.error(error); 
      alert("Network error: " + error.message); 
    }
  };

  if (selectedEndpoint) {
    return <EndpointDetail endpoint={selectedEndpoint} onBack={() => {setSelectedEndpoint(null); fetchEndpoints();}} />
  }

  return (
    <div className="animate-in flex-col gap-6">
      <header className="page-header">
        <div>
          <h1 className="page-title">Endpoints</h1>
          <p className="page-sub">Manage your webhook ingestion URLs and destination routes.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setIsCreating(true)}>
          <Plus size={16} /> New Endpoint
        </button>
      </header>

      {isCreating && (
        <div className="inline-form animate-in mb-6">
          <form onSubmit={handleCreate} className="flex gap-4 items-end">
            <div className="form-field flex-1">
              <label className="form-label">Endpoint Name</label>
              <input 
                type="text" 
                placeholder="e.g. Stripe Webhooks" 
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
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
             <div className="empty-state"><div className="spinner"></div></div>
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
                    <td>{new Date(ep.created_at).toLocaleDateString()}</td>
                    <td>
                      <button className="btn btn-secondary btn-sm" onClick={() => setSelectedEndpoint(ep)}>
                        Configure
                      </button>
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

// --- Main App ---
function App() {
  return (
    <BrowserRouter>
      <div className="layout">
        <Sidebar />
        <main className="main-content">
          <Routes>
            <Route path="/" element={<DashboardHome />} />
            <Route path="/endpoints" element={<EndpointsPage />} />
            <Route path="/events" element={<div className="animate-in"><h1 className="page-title">Events Log</h1><p className="page-sub mt-2">API implementation for Events Log is pending Phase 3.</p></div>} />
            <Route path="*" element={<div className="animate-in"><h1 className="page-title">Coming Soon</h1><p className="page-sub mt-2">This module is under development.</p></div>} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;

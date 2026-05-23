import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Activity, Webhook, Settings, Database, Server, RefreshCw, Plus } from 'lucide-react';
import './index.css';

interface Endpoint {
  id: string;
  name: string;
  is_active: boolean;
  rate_limit_rps: number | null;
  created_at: string;
  updated_at: string;
}

const Sidebar = () => {
  const location = useLocation();
  
  const navItems = [
    { path: '/', icon: <Activity size={20} />, label: 'Dashboard' },
    { path: '/endpoints', icon: <Webhook size={20} />, label: 'Endpoints' },
    { path: '/events', icon: <Database size={20} />, label: 'Events & DLQ' },
    { path: '/settings', icon: <Settings size={20} />, label: 'Settings' },
  ];

  return (
    <div className="sidebar glass-panel flex-col" style={{ width: '250px', minHeight: 'calc(100vh - 4rem)', padding: '1.5rem', borderRight: '1px solid var(--glass-border)', borderRadius: '0' }}>
      <div className="flex-row gap-2" style={{ marginBottom: '2rem', color: '#fff' }}>
        <Server size={28} color="var(--accent-color)" />
        <h2>Relay<span style={{color: 'var(--accent-color)'}}>HQ</span></h2>
      </div>
      
      <nav className="flex-col gap-2">
        {navItems.map((item) => (
          <Link 
            key={item.path} 
            to={item.path}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              padding: '0.75rem 1rem',
              borderRadius: '8px',
              color: location.pathname === item.path ? '#fff' : 'var(--text-secondary)',
              background: location.pathname === item.path ? 'rgba(59, 130, 246, 0.15)' : 'transparent',
              border: location.pathname === item.path ? '1px solid rgba(59, 130, 246, 0.3)' : '1px solid transparent',
              textDecoration: 'none',
              transition: 'all 0.2s ease'
            }}
          >
            {item.icon}
            <span style={{ fontWeight: 500 }}>{item.label}</span>
          </Link>
        ))}
      </nav>
    </div>
  );
};

const DashboardHome = () => {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchEndpoints = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/endpoints/');
      if (response.ok) {
        const data = await response.json();
        setEndpoints(data);
      }
    } catch (error) {
      console.error("Failed to fetch endpoints", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEndpoints();
  }, []);

  return (
    <div className="animate-fade-in flex-col gap-8">
      <div className="flex-row justify-between">
        <div>
          <h1>Overview</h1>
          <p>Real-time metrics for your webhook infrastructure.</p>
        </div>
        <button className="btn btn-primary" onClick={fetchEndpoints}><RefreshCw size={16} /> Refresh</button>
      </div>

      <div className="flex-row gap-4" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
        <div className="glass-panel" style={{ padding: '1.5rem' }}>
          <p style={{ fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>Active Endpoints</p>
          <h2 style={{ fontSize: '2.5rem', margin: 0 }}>{endpoints.filter(e => e.is_active).length}</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: '0.5rem' }}>Out of {endpoints.length} total</p>
        </div>
        
        <div className="glass-panel" style={{ padding: '1.5rem' }}>
          <p style={{ fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>Service Health</p>
          <h2 style={{ fontSize: '2.5rem', margin: 0 }}>Online</h2>
          <div style={{ background: 'rgba(255,255,255,0.1)', height: '4px', borderRadius: '2px', marginTop: '1rem', overflow: 'hidden' }}>
            <div style={{ background: 'var(--success-color)', width: '100%', height: '100%' }}></div>
          </div>
        </div>
      </div>

      <div>
        <div className="flex-row justify-between" style={{ marginBottom: '1rem' }}>
          <h2>Recent Endpoints</h2>
          <Link to="/endpoints" className="btn btn-secondary">View All</Link>
        </div>
        
        <div className="glass-panel" style={{ overflow: 'hidden' }}>
          {loading ? (
            <p style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>Loading endpoints...</p>
          ) : endpoints.length === 0 ? (
            <p style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>No endpoints found. Create one to get started.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>ID</th>
                  <th>Status</th>
                  <th>Created At</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {endpoints.slice(0, 5).map(ep => (
                  <tr key={ep.id}>
                    <td style={{ fontWeight: 500 }}>{ep.name}</td>
                    <td style={{ fontFamily: 'monospace', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>{ep.id.substring(0, 13)}...</td>
                    <td>
                      <span className={`badge ${ep.is_active ? 'badge-active' : 'badge-inactive'}`}>
                        {ep.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td>{new Date(ep.created_at).toLocaleDateString()}</td>
                    <td><button className="btn btn-secondary" style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}>Details</button></td>
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

  const fetchEndpoints = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/endpoints/');
      if (response.ok) {
        const data = await response.json();
        setEndpoints(data);
      }
    } catch (error) {
      console.error("Failed to fetch endpoints", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEndpoints();
  }, []);

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
        setNewName('');
        setIsCreating(false);
        fetchEndpoints();
      }
    } catch (error) {
      console.error("Failed to create endpoint", error);
    }
  };

  return (
    <div className="animate-fade-in flex-col gap-8">
      <div className="flex-row justify-between">
        <div>
          <h1>Endpoints</h1>
          <p>Manage your webhook ingestion URLs and destination routes.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setIsCreating(true)}>
          <Plus size={18} /> New Endpoint
        </button>
      </div>

      {isCreating && (
        <div className="glass-panel animate-fade-in" style={{ padding: '1.5rem', marginBottom: '1rem' }}>
          <h3>Create New Endpoint</h3>
          <form onSubmit={handleCreate} className="flex-row gap-4" style={{ marginTop: '1rem' }}>
            <input 
              type="text" 
              placeholder="Endpoint Name (e.g. Stripe Webhooks)" 
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              style={{ flex: 1, padding: '0.8rem 1rem', borderRadius: '8px', border: '1px solid var(--glass-border)', background: 'rgba(0,0,0,0.2)', color: 'white', outline: 'none' }}
              autoFocus
            />
            <button type="submit" className="btn btn-primary">Create</button>
            <button type="button" className="btn btn-secondary" onClick={() => setIsCreating(false)}>Cancel</button>
          </form>
        </div>
      )}
      
      <div className="glass-panel" style={{ overflow: 'hidden' }}>
          {loading ? (
            <p style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>Loading endpoints...</p>
          ) : endpoints.length === 0 ? (
            <p style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>No endpoints found. Create one to get started.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>ID</th>
                  <th>Status</th>
                  <th>Created At</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {endpoints.map(ep => (
                  <tr key={ep.id}>
                    <td style={{ fontWeight: 500 }}>{ep.name}</td>
                    <td style={{ fontFamily: 'monospace', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>{ep.id}</td>
                    <td>
                      <span className={`badge ${ep.is_active ? 'badge-active' : 'badge-inactive'}`}>
                        {ep.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td>{new Date(ep.created_at).toLocaleDateString()}</td>
                    <td><button className="btn btn-secondary" style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}>Configure</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
    </div>
  );
};

function App() {
  return (
    <BrowserRouter>
      <div style={{ display: 'flex', minHeight: '100vh', width: '100%' }}>
        <Sidebar />
        <main style={{ flex: 1, padding: '2rem 3rem', overflowY: 'auto' }}>
          <div style={{ maxWidth: '1200px', margin: '0 auto' }}>
            <Routes>
              <Route path="/" element={<DashboardHome />} />
              <Route path="/endpoints" element={<EndpointsPage />} />
              <Route path="*" element={<div className="animate-fade-in"><h2>Coming Soon</h2><p>This module is currently under development in Phase 2.</p></div>} />
            </Routes>
          </div>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;

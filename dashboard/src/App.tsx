import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import './index.css';
import { Sidebar } from './components/Sidebar';
import { ToastContainer } from './components/ToastContainer';
import { useToast } from './hooks/useToast';
import { OverviewPage } from './pages/OverviewPage';
import { EndpointsPage } from './pages/EndpointsPage';
import { EventsPage } from './pages/EventsPage';
import { DlqPage } from './pages/DlqPage';
import { SettingsPage } from './pages/SettingsPage';
import type { User, Workspace } from './types';

const TOKEN_KEY = 'relayhq_token';
const WORKSPACE_KEY = 'relayhq_workspace_id';

function App() {
  const { toasts, show: toast } = useToast();
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? '');
  const [user, setUser] = useState<User | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [authLoading, setAuthLoading] = useState(Boolean(token));

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(WORKSPACE_KEY);
    setToken('');
    setUser(null);
    setWorkspace(null);
  }, []);

  const authedFetch = useCallback((input: RequestInfo | URL, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (token) headers.set('Authorization', `Bearer ${token}`);
    return fetch(input, { ...init, headers });
  }, [token]);

  const loadSession = useCallback(async () => {
    if (!token) return;
    setAuthLoading(true);
    try {
      const [meRes, wsRes] = await Promise.all([
        authedFetch('/api/auth/me'),
        authedFetch('/api/workspaces/'),
      ]);
      if (!meRes.ok || !wsRes.ok) {
        logout();
        return;
      }

      const workspaces: Workspace[] = await wsRes.json();
      const savedWorkspaceId = localStorage.getItem(WORKSPACE_KEY);
      const selectedWorkspace =
        workspaces.find(ws => ws.id === savedWorkspaceId) ?? workspaces[0] ?? null;

      setUser(await meRes.json());
      setWorkspace(selectedWorkspace);
      if (selectedWorkspace) localStorage.setItem(WORKSPACE_KEY, selectedWorkspace.id);
    } finally {
      setAuthLoading(false);
    }
  }, [authedFetch, logout, token]);

  useEffect(() => {
    loadSession();
  }, [loadSession]);

  const apiBase = useMemo(
    () => workspace ? `/api/workspaces/${workspace.id}` : '',
    [workspace],
  );

  const handleAuth = (nextToken: string, nextUser: User) => {
    localStorage.setItem(TOKEN_KEY, nextToken);
    setToken(nextToken);
    setUser(nextUser);
  };

  if (!token) {
    return (
      <>
        <AuthScreen onAuthenticated={handleAuth} toast={toast} />
        <ToastContainer toasts={toasts} />
      </>
    );
  }

  if (authLoading) {
    return <div className="auth-shell"><div className="spinner" /></div>;
  }

  if (!workspace) {
    return (
      <>
        <div className="auth-shell">
          <div className="auth-panel">
            <div className="brand-name">Relay<span>HQ</span></div>
            <p className="page-sub">No workspace is available for this account.</p>
            <button className="btn btn-secondary" onClick={logout}>Sign out</button>
          </div>
        </div>
        <ToastContainer toasts={toasts} />
      </>
    );
  }

  return (
    <BrowserRouter>
      <div className="layout">
        <Sidebar />
        <main className="main-content">
          <div className="session-bar">
            <span>{workspace.name}</span>
            <span>{user?.email}</span>
            <button className="btn btn-secondary btn-sm" onClick={logout}>Sign out</button>
          </div>
          <Routes>
            <Route path="/"          element={<OverviewPage apiBase={apiBase} apiFetch={authedFetch} />} />
            <Route path="/endpoints" element={<EndpointsPage apiBase={apiBase} apiFetch={authedFetch} toast={toast} />} />
            <Route path="/events"    element={<EventsPage apiBase={apiBase} apiFetch={authedFetch} toast={toast} />} />
            <Route path="/dlq"       element={<DlqPage apiBase={apiBase} apiFetch={authedFetch} toast={toast} />} />
            <Route path="/settings"  element={<SettingsPage user={user!} workspace={workspace} apiFetch={authedFetch} onLogout={logout} toast={toast} />} />
            <Route path="*"          element={<div className="animate-in"><h1 className="page-title">404</h1><p className="page-sub mt-2">Page not found.</p></div>} />
          </Routes>
        </main>
        <ToastContainer toasts={toasts} />
      </div>
    </BrowserRouter>
  );
}

export default App;

interface AuthScreenProps {
  onAuthenticated: (token: string, user: User) => void;
  toast: (message: string, type?: 'success' | 'error') => void;
}

function AuthScreen({ onAuthenticated, toast }: AuthScreenProps) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    const body = mode === 'login'
      ? { email, password }
      : { email, password, display_name: displayName || email.split('@')[0] };

    const res = await fetch(`/api/auth/${mode}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    setLoading(false);

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      toast(data.detail ?? `Authentication failed (${res.status})`, 'error');
      return;
    }

    const data = await res.json();
    onAuthenticated(data.access_token, data.user);
  };

  return (
    <div className="auth-shell">
      <form className="auth-panel" onSubmit={submit}>
        <div>
          <div className="brand-name">Relay<span>HQ</span></div>
          <p className="page-sub">{mode === 'login' ? 'Sign in to continue.' : 'Create your workspace.'}</p>
        </div>
        {mode === 'register' && (
          <div className="form-field">
            <label className="form-label">Display Name</label>
            <input value={displayName} onChange={e => setDisplayName(e.target.value)} required />
          </div>
        )}
        <div className="form-field">
          <label className="form-label">Email</label>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)} required />
        </div>
        <div className="form-field">
          <label className="form-label">Password</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} minLength={8} required />
        </div>
        <button className="btn btn-primary" type="submit" disabled={loading}>
          {loading ? 'Working...' : mode === 'login' ? 'Sign In' : 'Create Account'}
        </button>
        <button
          className="btn btn-secondary"
          type="button"
          onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
        >
          {mode === 'login' ? 'Create account' : 'Use existing account'}
        </button>
      </form>
    </div>
  );
}

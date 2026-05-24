import { Clock } from 'lucide-react';

export const SettingsPage = () => (
  <div className="animate-in flex-col gap-6">
    <header className="page-header">
      <div>
        <h1 className="page-title">Settings</h1>
        <p className="page-sub">Application configuration and Phase 3 features in progress.</p>
      </div>
    </header>
    <div className="card card-p flex-col gap-4">
      <div className="section-label">Upcoming (Phase 3)</div>
      {[
        'Sandboxed JavaScript payload transformations',
        'Prometheus metrics endpoint',
        'Conditional route filtering (rules engine)',
        'Multi-tenant user management & RBAC',
      ].map(item => (
        <div key={item} className="flex items-center gap-3" style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
          <Clock size={14} style={{ color: 'var(--warning)', flexShrink: 0 }} />
          {item}
        </div>
      ))}
    </div>
  </div>
);

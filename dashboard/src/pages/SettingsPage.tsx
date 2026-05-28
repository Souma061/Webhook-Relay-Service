import { useState } from 'react';
import {
  User, Building2, Key, Bell, Shield, Clock,
  Copy, Check, Eye, EyeOff, ExternalLink,
  Zap, Database, Activity
} from 'lucide-react';
import type { User as UserType, Workspace } from '../types';

interface SettingsPageProps {
  user: UserType;
  workspace: Workspace;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  onLogout: () => void;
  toast: (message: string, type?: 'success' | 'error') => void;
}

// ── Small reusable components ─────────────────────────────────────────────────

function SectionHeader({ icon: Icon, title, description }: {
  icon: React.ElementType;
  title: string;
  description: string;
}) {
  return (
    <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start', marginBottom: '20px' }}>
      <div style={{
        width: 36, height: 36, borderRadius: 8, flexShrink: 0,
        background: 'var(--accent-muted, rgba(124,111,240,0.12))',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Icon size={16} style={{ color: 'var(--accent)' }} />
      </div>
      <div>
        <div style={{ fontWeight: 600, fontSize: '0.9375rem', color: 'var(--text-primary)' }}>{title}</div>
        <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginTop: 2 }}>{description}</div>
      </div>
    </div>
  );
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '10px 0', borderBottom: '1px solid var(--border)',
    }}>
      <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>{label}</span>
      <span style={{
        fontSize: '0.8125rem', color: 'var(--text-primary)', fontWeight: 500,
        fontFamily: mono ? 'var(--font-mono, monospace)' : undefined,
      }}>{value}</span>
    </div>
  );
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={copy}
      style={{
        background: 'none', border: 'none', cursor: 'pointer', padding: '4px',
        color: copied ? 'var(--success, #4caf7d)' : 'var(--text-secondary)',
        display: 'flex', alignItems: 'center', transition: 'color 0.2s',
      }}
      title="Copy to clipboard"
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
    </button>
  );
}

// ── Danger zone confirm button ─────────────────────────────────────────────────
function DangerAction({ label, description, buttonLabel, onConfirm }: {
  label: string;
  description: string;
  buttonLabel: string;
  onConfirm: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '14px 0', borderBottom: '1px solid var(--border)',
      gap: 16,
    }}>
      <div>
        <div style={{ fontSize: '0.875rem', fontWeight: 500, color: 'var(--text-primary)' }}>{label}</div>
        <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginTop: 2 }}>{description}</div>
      </div>
      {confirming ? (
        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          <button className="btn btn-secondary btn-sm" onClick={() => setConfirming(false)}>Cancel</button>
          <button
            className="btn btn-sm"
            style={{ background: 'var(--error, #e57373)', color: '#fff', border: 'none' }}
            onClick={() => { onConfirm(); setConfirming(false); }}
          >
            Confirm
          </button>
        </div>
      ) : (
        <button
          className="btn btn-secondary btn-sm"
          style={{ flexShrink: 0, borderColor: 'var(--error, #e57373)', color: 'var(--error, #e57373)' }}
          onClick={() => setConfirming(true)}
        >
          {buttonLabel}
        </button>
      )}
    </div>
  );
}

// ── Change Password form ───────────────────────────────────────────────────────
function ChangePasswordForm({ apiFetch, toast }: {
  apiFetch: SettingsPageProps['apiFetch'];
  toast: SettingsPageProps['toast'];
}) {
  const [current, setCurrent] = useState('');
  const [next, setNext]       = useState('');
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNext,    setShowNext]    = useState(false);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next.length < 8) { toast('New password must be at least 8 characters', 'error'); return; }
    setLoading(true);
    try {
      const res = await apiFetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        toast(d.detail ?? 'Failed to change password', 'error');
      } else {
        toast('Password changed successfully', 'success');
        setCurrent(''); setNext('');
      }
    } finally {
      setLoading(false);
    }
  };

  const eyeStyle: React.CSSProperties = {
    position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
    background: 'none', border: 'none', cursor: 'pointer',
    color: 'var(--text-secondary)', display: 'flex', alignItems: 'center',
  };

  return (
    <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="form-field" style={{ position: 'relative' }}>
        <label className="form-label">Current Password</label>
        <input
          type={showCurrent ? 'text' : 'password'}
          value={current} onChange={e => setCurrent(e.target.value)}
          required style={{ paddingRight: 36 }}
        />
        <button type="button" style={eyeStyle} onClick={() => setShowCurrent(v => !v)}>
          {showCurrent ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
      <div className="form-field" style={{ position: 'relative' }}>
        <label className="form-label">New Password <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}>(min 8 chars)</span></label>
        <input
          type={showNext ? 'text' : 'password'}
          value={next} onChange={e => setNext(e.target.value)}
          required minLength={8} style={{ paddingRight: 36 }}
        />
        <button type="button" style={eyeStyle} onClick={() => setShowNext(v => !v)}>
          {showNext ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
      <div>
        <button className="btn btn-primary btn-sm" type="submit" disabled={loading || !current || !next}>
          {loading ? 'Saving…' : 'Update Password'}
        </button>
      </div>
    </form>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export const SettingsPage = ({ user, workspace, apiFetch, onLogout, toast }: SettingsPageProps) => {
  const joinedDate = new Date(user.created_at).toLocaleDateString('en-US', {
    year: 'numeric', month: 'long', day: 'numeric',
  });
  const workspaceDate = new Date(workspace.created_at).toLocaleDateString('en-US', {
    year: 'numeric', month: 'long', day: 'numeric',
  });

  const handleLogout = async () => {
    try {
      await apiFetch('/api/auth/logout', { method: 'POST' });
    } finally {
      onLogout();
    }
  };

  const cardStyle: React.CSSProperties = {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 12,
    padding: '24px',
    display: 'flex',
    flexDirection: 'column',
  };

  const upcomingFeatures = [
    { icon: Zap,      label: 'Sandboxed JavaScript payload transformations',  eta: 'Phase 3' },
    { icon: Activity, label: 'Prometheus metrics endpoint',                    eta: 'Phase 3' },
    { icon: Database, label: 'Per-endpoint rate limiting',                     eta: 'Phase 3' },
    { icon: Shield,   label: 'Schema validation for incoming webhooks',        eta: 'Phase 3' },
  ];

  return (
    <div className="animate-in" style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>

      {/* Page header */}
      <header className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Manage your account, workspace, and security preferences.</p>
        </div>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 20 }}>

        {/* ── Account ─────────────────────────────────────────────────────── */}
        <div style={cardStyle}>
          <SectionHeader icon={User} title="Account" description="Your profile and identity" />
          <div style={{ marginBottom: 16 }}>
            <InfoRow label="Display name" value={user.display_name} />
            <InfoRow label="Email" value={user.email} />
            <InfoRow label="Member since" value={joinedDate} />
            <InfoRow label="Account status" value={user.is_active ? 'Active' : 'Inactive'} />
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '10px 0',
            }}>
              <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>User ID</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                  {user.id.slice(0, 8)}…
                </span>
                <CopyButton value={user.id} />
              </div>
            </div>
          </div>
        </div>

        {/* ── Workspace ───────────────────────────────────────────────────── */}
        <div style={cardStyle}>
          <SectionHeader icon={Building2} title="Workspace" description="Your team workspace details" />
          <div style={{ marginBottom: 16 }}>
            <InfoRow label="Name" value={workspace.name} />
            <InfoRow label="Slug" value={workspace.slug} mono />
            <InfoRow label="Your role" value={workspace.role ?? '—'} />
            <InfoRow label="Created" value={workspaceDate} />
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '10px 0',
            }}>
              <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>Workspace ID</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                  {workspace.id.slice(0, 8)}…
                </span>
                <CopyButton value={workspace.id} />
              </div>
            </div>
          </div>
        </div>

        {/* ── Security ────────────────────────────────────────────────────── */}
        <div style={cardStyle}>
          <SectionHeader icon={Key} title="Security" description="Password and authentication" />
          <ChangePasswordForm apiFetch={apiFetch} toast={toast} />
        </div>

        {/* ── Notifications placeholder ────────────────────────────────────── */}
        <div style={cardStyle}>
          <SectionHeader icon={Bell} title="Notifications" description="Alerts and delivery failure emails" />
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column', gap: 8, padding: '24px 0',
            color: 'var(--text-secondary)', textAlign: 'center',
          }}>
            <Bell size={28} style={{ opacity: 0.3 }} />
            <span style={{ fontSize: '0.8125rem' }}>Email alerts coming in Phase 3</span>
            <a
              href="https://github.com/Souma061/Webhook-Relay-Service/issues"
              target="_blank" rel="noreferrer"
              style={{ fontSize: '0.8125rem', color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: 4 }}
            >
              Request a feature <ExternalLink size={11} />
            </a>
          </div>
        </div>

      </div>

      {/* ── Roadmap ─────────────────────────────────────────────────────────── */}
      <div style={cardStyle}>
        <SectionHeader icon={Clock} title="Coming Next" description="Features planned for the next phase" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
          {upcomingFeatures.map(({ icon: Icon, label, eta }) => (
            <div key={label} style={{
              display: 'flex', alignItems: 'flex-start', gap: 10,
              padding: '12px 14px',
              background: 'var(--surface2, rgba(255,255,255,0.03))',
              border: '1px solid var(--border)',
              borderRadius: 8,
            }}>
              <div style={{
                width: 28, height: 28, borderRadius: 6, flexShrink: 0,
                background: 'rgba(255,213,79,0.1)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <Icon size={13} style={{ color: 'var(--warning, #ffd54f)' }} />
              </div>
              <div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--text-primary)', lineHeight: 1.4 }}>{label}</div>
                <div style={{ fontSize: '0.6875rem', color: 'var(--text-secondary)', marginTop: 2 }}>{eta}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Danger zone ──────────────────────────────────────────────────────── */}
      <div style={{ ...cardStyle, borderColor: 'rgba(229,115,115,0.25)' }}>
        <SectionHeader icon={Shield} title="Danger Zone" description="Irreversible actions — proceed with caution" />
        <DangerAction
          label="Sign out of all sessions"
          description="Revokes your current token. You will be redirected to the login screen."
          buttonLabel="Sign out"
          onConfirm={handleLogout}
        />
        <DangerAction
          label="Delete account"
          description="Permanently deletes your account and all associated data. This cannot be undone."
          buttonLabel="Delete account"
          onConfirm={() => toast('Account deletion is not yet available', 'error')}
        />
      </div>

    </div>
  );
};

import { Link, useLocation } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  Database,
  Server,
  Settings,
  Webhook,
} from 'lucide-react';

export const Sidebar = () => {
  const location = useLocation();
  const navItems = [
    { path: '/',          icon: <Activity size={17} />,      label: 'Overview'    },
    { path: '/endpoints', icon: <Webhook size={17} />,       label: 'Endpoints'   },
    { path: '/events',    icon: <Database size={17} />,      label: 'Events Log'  },
    { path: '/dlq',       icon: <AlertTriangle size={17} />, label: 'Dead Letters', danger: true },
    { path: '/settings',  icon: <Settings size={17} />,      label: 'Settings'    },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="brand-icon">
          <Server size={19} color="#fff" strokeWidth={2.5} />
        </div>
        <div className="brand-name">Relay<span>HQ</span></div>
      </div>

      <nav className="nav">
        {navItems.map(item => (
          <Link
            key={item.path}
            to={item.path}
            className={`nav-link ${location.pathname === item.path ? 'active' : ''} ${item.danger ? 'nav-link-danger' : ''}`}
          >
            {item.icon}
            {item.label}
          </Link>
        ))}
      </nav>

      <div className="nav-divider" />
      <div className="sidebar-footer">
        Webhook Relay Service<br />Phase 3 · v1.0.0
      </div>
    </aside>
  );
};

import React from 'react';
import {
  LayoutDashboard,
  Layers,
  PlusCircle,
  Cpu,
  Activity,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';

export type NavPage = 'overview' | 'explorer' | 'submit' | 'workers';

interface SidebarProps {
  currentPage: NavPage;
  onNavigate: (page: NavPage) => void;
  serviceStatus?: string;
  workerCount?: number;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentPage,
  onNavigate,
  serviceStatus = 'healthy',
  workerCount = 0,
}) => {
  const navItems: { id: NavPage; label: string; icon: React.ReactNode }[] = [
    { id: 'overview', label: 'Overview', icon: <LayoutDashboard size={17} /> },
    { id: 'explorer', label: 'Job Explorer', icon: <Layers size={17} /> },
    { id: 'submit', label: 'Submit Job', icon: <PlusCircle size={17} /> },
    { id: 'workers', label: 'Workers & Queue', icon: <Cpu size={17} /> },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <div className="sidebar-logo-icon">
            <Activity size={18} />
          </div>
          <span>TaskMesh</span>
        </div>
        <span className="sidebar-badge">v0.1</span>
      </div>

      <nav className="sidebar-nav">
        {navItems.map((item) => (
          <button
            key={item.id}
            className={`nav-item ${currentPage === item.id ? 'active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span>System Status:</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', color: serviceStatus === 'healthy' ? '#34d399' : '#f87171' }}>
            {serviceStatus === 'healthy' ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
            {serviceStatus}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span>Active Workers:</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: '#fff' }}>{workerCount}</span>
        </div>
      </div>
    </aside>
  );
};

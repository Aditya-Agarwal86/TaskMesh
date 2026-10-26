import React from 'react';

interface MetricCardProps {
  label: string;
  value: number | string;
  subtitle?: string;
  icon?: React.ReactNode;
}

export const MetricCard: React.FC<MetricCardProps> = ({ label, value, subtitle, icon }) => {
  return (
    <div className="metric-card">
      <div className="metric-label">
        <span>{label}</span>
        {icon && <span style={{ opacity: 0.7 }}>{icon}</span>}
      </div>
      <div className="metric-value">{value}</div>
      {subtitle && <div className="metric-subtitle">{subtitle}</div>}
    </div>
  );
};

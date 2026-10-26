import React from 'react';
import { JobStatus } from '../types/api';

interface StatusBadgeProps {
  status: JobStatus | string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  const normStatus = (status || 'QUEUED').toUpperCase();
  const lowerClass = normStatus.toLowerCase();

  return (
    <span className={`status-badge ${lowerClass}`} data-testid={`status-badge-${lowerClass}`}>
      <span className="status-badge-dot" />
      {normStatus}
    </span>
  );
};

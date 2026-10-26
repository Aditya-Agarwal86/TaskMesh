import React from 'react';

interface PriorityTagProps {
  priority: number;
}

export const PriorityTag: React.FC<PriorityTagProps> = ({ priority }) => {
  let label = 'NORMAL (5)';
  let className = 'p-normal';

  if (priority >= 10) {
    label = 'CRITICAL (10)';
    className = 'p-critical';
  } else if (priority >= 8) {
    label = 'HIGH (8)';
    className = 'p-high';
  } else if (priority <= 1) {
    label = 'LOW (1)';
    className = 'p-low';
  }

  return <span className={`priority-tag ${className}`}>{label}</span>;
};

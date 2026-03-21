import React from 'react';
import { Chip } from '@mui/material';

type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

interface Props {
  status: JobStatus;
}

const STATUS_COLOR: Record<JobStatus, 'default' | 'warning' | 'success' | 'error' | 'info'> = {
  pending: 'info',
  running: 'warning',
  completed: 'success',
  failed: 'error',
  cancelled: 'default',
};

const JobStatusChip: React.FC<Props> = ({ status }) => (
  <Chip label={status} color={STATUS_COLOR[status] ?? 'default'} size="small" />
);

export default JobStatusChip;

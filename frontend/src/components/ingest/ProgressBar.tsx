import React from 'react';
import { Box, LinearProgress, Typography } from '@mui/material';

interface Props {
  progress: number;
  currentFile?: string;
  label?: string;
}

const ProgressBar: React.FC<Props> = ({ progress, currentFile, label }) => (
  <Box sx={{ width: '100%' }}>
    {label && (
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
        <Typography variant="caption">{label}</Typography>
        <Typography variant="caption">{Math.round(progress * 100)}%</Typography>
      </Box>
    )}
    <LinearProgress
      variant="determinate"
      value={Math.min(progress * 100, 100)}
      sx={{ height: 8, borderRadius: 4 }}
    />
    {currentFile && (
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ mt: 0.5, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
      >
        {currentFile}
      </Typography>
    )}
  </Box>
);

export default ProgressBar;

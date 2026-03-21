import React from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';

interface Props {
  message?: string;
}

const LoadingOverlay: React.FC<Props> = ({ message }) => (
  <Box
    sx={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: '100vh',
      gap: 2,
    }}
  >
    <CircularProgress size={48} />
    {message && (
      <Typography variant="body2" color="text.secondary">
        {message}
      </Typography>
    )}
  </Box>
);

export default LoadingOverlay;

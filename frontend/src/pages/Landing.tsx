import React from 'react';
import { Box, Typography, Paper, Stack } from '@mui/material';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import GoogleLoginButton from '../components/auth/GoogleLoginButton';
import { useAppSelector } from '../store';
import { Navigate } from 'react-router-dom';

const Landing: React.FC = () => {
  const isAuthenticated = useAppSelector((s) => s.auth.isAuthenticated);
  const user = useAppSelector((s) => s.auth.user);

  if (isAuthenticated || user) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        bgcolor: 'background.default',
        p: 3,
      }}
    >
      <Paper
        elevation={4}
        sx={{
          p: 5,
          maxWidth: 440,
          width: '100%',
          textAlign: 'center',
          borderRadius: 3,
        }}
      >
        <Stack alignItems="center" spacing={2} mb={4}>
          <AccountTreeIcon sx={{ fontSize: 60, color: 'primary.main' }} />
          <Typography variant="h4" fontWeight={700}>
            Knowledge Graph Builder
          </Typography>
          <Typography variant="body1" color="text.secondary">
            Ingest your documents, extract entities, explore relationships, and search your
            knowledge graph with AI-powered hybrid search.
          </Typography>
        </Stack>

        <GoogleLoginButton />
      </Paper>
    </Box>
  );
};

export default Landing;

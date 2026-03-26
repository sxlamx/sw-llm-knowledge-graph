import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Paper from '@mui/material/Paper';
import Typography from '@mui/material/Typography';
import Divider from '@mui/material/Divider';
import Button from '@mui/material/Button';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import { useAppSelector } from '../store';

export default function LoginPage() {
  const navigate = useNavigate();
  const token = useAppSelector((s) => s.auth.accessToken);

  useEffect(() => {
    if (token) navigate('/dashboard', { replace: true });
  }, [token, navigate]);

  const handleSignIn = () => {
    window.location.href = '/api/v1/auth/google/redirect';
  };

  return (
    <Box
      display="flex"
      alignItems="center"
      justifyContent="center"
      minHeight="100vh"
      sx={{ bgcolor: 'background.default' }}
    >
      <Paper
        elevation={3}
        sx={{ p: 5, maxWidth: 400, width: '100%', textAlign: 'center', borderRadius: 3 }}
      >
        <AccountTreeIcon sx={{ fontSize: 56, color: 'primary.main', mb: 2 }} />
        <Typography variant="h5" fontWeight={700} gutterBottom>
          Knowledge Graph Builder
        </Typography>
        <Typography variant="body2" color="text.secondary" mb={3}>
          LLM-powered semantic search and knowledge extraction
        </Typography>
        <Divider sx={{ mb: 3 }} />
        <Button
          variant="outlined"
          size="large"
          fullWidth
          onClick={handleSignIn}
          sx={{ textTransform: 'none', fontSize: '1rem' }}
        >
          Sign in with Google
        </Button>
      </Paper>
    </Box>
  );
}

import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Paper from '@mui/material/Paper';
import Typography from '@mui/material/Typography';
import Divider from '@mui/material/Divider';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import { GoogleLogin, CredentialResponse } from '@react-oauth/google';
import { useAppDispatch, useAppSelector } from '../store';
import { setCredentials } from '../store/authSlice';
import { useGoogleLoginMutation } from '../api/authApi';

export default function LoginPage() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const token = useAppSelector((s) => s.auth.token);
  const [googleLogin, { isLoading }] = useGoogleLoginMutation();

  useEffect(() => {
    if (token) navigate('/dashboard', { replace: true });
  }, [token, navigate]);

  const handleSuccess = async (credentialResponse: CredentialResponse) => {
    if (!credentialResponse.credential) return;
    try {
      const result = await googleLogin({ token: credentialResponse.credential }).unwrap();
      dispatch(setCredentials({ token: result.access_token, user: result.user }));
      navigate('/dashboard', { replace: true });
    } catch (err) {
      console.error('Login failed', err);
    }
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
        {isLoading ? (
          <Typography variant="body2" color="text.secondary">Signing in...</Typography>
        ) : (
          <Box display="flex" justifyContent="center">
            <GoogleLogin
              onSuccess={handleSuccess}
              onError={() => console.error('Google login error')}
              useOneTap
              size="large"
            />
          </Box>
        )}
      </Paper>
    </Box>
  );
}

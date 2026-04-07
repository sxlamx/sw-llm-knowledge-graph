import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import Typography from '@mui/material/Typography';
import { useAppDispatch } from '../store';
import { setCredentials } from '../store/authSlice';
import { wsConnect } from '../store/wsMiddleware';
import { useExchangeCodeMutation } from '../api/authApi';

const REDIRECT_URI = `${window.location.origin}/auth/callback/google`;

export default function CallbackPage() {
  const navigate = useNavigate();
  const dispatch = useAppDispatch();
  const [exchangeCode] = useExchangeCodeMutation();
  const called = useRef(false);
  const error = useRef<string | null>(null);

  useEffect(() => {
    if (called.current) return;
    called.current = true;

    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const errParam = params.get('error');

    if (errParam) {
      error.current = `Google declined: ${errParam}`;
      navigate('/', { replace: true });
      return;
    }

    if (!code) {
      navigate('/', { replace: true });
      return;
    }

    exchangeCode({ code, redirect_uri: REDIRECT_URI })
      .unwrap()
      .then((result) => {
        dispatch(setCredentials({ accessToken: result.access_token, user: result.user }));
        dispatch(wsConnect());
        navigate('/dashboard', { replace: true });
      })
      .catch((err) => {
        console.error('OAuth exchange failed', err);
        navigate('/', { replace: true });
      });
  }, []);

  return (
    <Box display="flex" flexDirection="column" alignItems="center" justifyContent="center" minHeight="100vh" gap={2}>
      <CircularProgress />
      <Typography variant="body1" color="text.secondary">Signing you in…</Typography>
    </Box>
  );
}

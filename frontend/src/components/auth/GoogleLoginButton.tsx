import React from 'react';
import { GoogleLogin, CredentialResponse } from '@react-oauth/google';
import { Box, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { useAppDispatch } from '../../store';
import { setCredentials, setLoading } from '../../store/authSlice';
import { useGoogleLoginMutation } from '../../api/authApi';
import { showSnackbar } from '../../store/slices/uiSlice';
import { wsConnect } from '../../store/wsMiddleware';

const GoogleLoginButton: React.FC = () => {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const [googleLogin] = useGoogleLoginMutation();

  const handleSuccess = async (credentialResponse: CredentialResponse) => {
    if (!credentialResponse.credential) return;

    dispatch(setLoading(true));
    try {
      const result = await googleLogin({ token: credentialResponse.credential }).unwrap();
      dispatch(setCredentials({ user: result.user, accessToken: result.access_token }));
      dispatch(wsConnect());
      navigate('/dashboard');
    } catch {
      dispatch(showSnackbar({ message: 'Login failed. Please try again.', severity: 'error' }));
    } finally {
      dispatch(setLoading(false));
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
      <GoogleLogin
        onSuccess={handleSuccess}
        onError={() =>
          dispatch(showSnackbar({ message: 'Google sign-in failed.', severity: 'error' }))
        }
      />
      <Typography variant="caption" color="text.secondary">
        Sign in with your Google account to continue
      </Typography>
    </Box>
  );
};

export default GoogleLoginButton;

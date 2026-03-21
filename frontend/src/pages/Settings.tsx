import React from 'react';
import {
  Box,
  Typography,
  Paper,
  Stack,
  Avatar,
  Divider,
  List,
  ListItem,
  ListItemText,
  ListItemSecondaryAction,
  Switch,
  Button,
} from '@mui/material';
import { useAppSelector, useAppDispatch } from '../store';
import { toggleTheme } from '../store/slices/uiSlice';
import { logout } from '../store/slices/authSlice';
import { wsDisconnect } from '../store/wsMiddleware';
import { useLogoutUserMutation } from '../api/authApi';
import { useNavigate } from 'react-router-dom';

const Settings: React.FC = () => {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const user = useAppSelector((s) => s.auth.user);
  const themeMode = useAppSelector((s) => s.ui.themeMode);
  const [logoutUser] = useLogoutUserMutation();

  const handleLogout = async () => {
    try { await logoutUser(); } catch { /* ignore */ }
    dispatch(wsDisconnect());
    dispatch(logout());
    navigate('/');
  };

  return (
    <Box maxWidth={600}>
      <Typography variant="h5" fontWeight={600} mb={3}>
        Settings
      </Typography>

      {/* Profile */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" fontWeight={600} gutterBottom>Profile</Typography>
        <Divider sx={{ mb: 2 }} />
        <Stack direction="row" alignItems="center" spacing={2}>
          <Avatar src={user?.picture} alt={user?.name} sx={{ width: 56, height: 56 }} />
          <Box>
            <Typography variant="body1" fontWeight={500}>{user?.name ?? 'Unknown'}</Typography>
            <Typography variant="body2" color="text.secondary">{user?.email ?? ''}</Typography>
          </Box>
        </Stack>
      </Paper>

      {/* Appearance */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" fontWeight={600} gutterBottom>Appearance</Typography>
        <Divider sx={{ mb: 1 }} />
        <List disablePadding>
          <ListItem disablePadding>
            <ListItemText
              primary="Dark mode"
              secondary="Switch between light and dark theme"
            />
            <ListItemSecondaryAction>
              <Switch
                checked={themeMode === 'dark'}
                onChange={() => dispatch(toggleTheme())}
              />
            </ListItemSecondaryAction>
          </ListItem>
        </List>
      </Paper>

      {/* Account */}
      <Paper sx={{ p: 2 }}>
        <Typography variant="subtitle1" fontWeight={600} gutterBottom>Account</Typography>
        <Divider sx={{ mb: 2 }} />
        <Button variant="outlined" color="error" onClick={handleLogout}>
          Sign out
        </Button>
      </Paper>
    </Box>
  );
};

export default Settings;

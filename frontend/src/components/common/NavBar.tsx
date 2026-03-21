import React from 'react';
import {
  AppBar,
  Toolbar,
  Typography,
  IconButton,
  Avatar,
  Button,
  Box,
  Tooltip,
} from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import Brightness4Icon from '@mui/icons-material/Brightness4';
import Brightness7Icon from '@mui/icons-material/Brightness7';
import { useNavigate } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../../store';
import { toggleTheme, setDrawerOpen } from '../../store/slices/uiSlice';
import { logout } from '../../store/slices/authSlice';
import { useLogoutUserMutation } from '../../api/authApi';
import { wsDisconnect } from '../../store/wsMiddleware';

const NavBar: React.FC = () => {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const user = useAppSelector((s) => s.auth.user);
  const themeMode = useAppSelector((s) => s.ui.themeMode);
  const [logoutUser] = useLogoutUserMutation();

  const handleLogout = async () => {
    try {
      await logoutUser();
    } catch {
      // ignore
    }
    dispatch(wsDisconnect());
    dispatch(logout());
    navigate('/');
  };

  return (
    <AppBar position="fixed" sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}>
      <Toolbar>
        <IconButton
          color="inherit"
          edge="start"
          onClick={() => dispatch(setDrawerOpen(true))}
          sx={{ mr: 1 }}
        >
          <MenuIcon />
        </IconButton>

        <AccountTreeIcon sx={{ mr: 1 }} />
        <Typography
          variant="h6"
          component="div"
          sx={{ cursor: 'pointer', flexGrow: 1 }}
          onClick={() => navigate('/dashboard')}
        >
          Knowledge Graph Builder
        </Typography>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Tooltip title="Toggle theme">
            <IconButton color="inherit" onClick={() => dispatch(toggleTheme())}>
              {themeMode === 'dark' ? <Brightness7Icon /> : <Brightness4Icon />}
            </IconButton>
          </Tooltip>

          {user && (
            <>
              <Tooltip title={user.email}>
                <Avatar
                  src={user.picture}
                  alt={user.name}
                  sx={{ width: 32, height: 32, cursor: 'pointer' }}
                  onClick={() => navigate('/settings')}
                />
              </Tooltip>
              <Button color="inherit" size="small" onClick={handleLogout}>
                Logout
              </Button>
            </>
          )}
        </Box>
      </Toolbar>
    </AppBar>
  );
};

export default NavBar;

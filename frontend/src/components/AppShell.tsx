import { useState } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import AppBar from '@mui/material/AppBar';
import Toolbar from '@mui/material/Toolbar';
import Typography from '@mui/material/Typography';
import IconButton from '@mui/material/IconButton';
import Avatar from '@mui/material/Avatar';
import Menu from '@mui/material/Menu';
import MenuItem from '@mui/material/MenuItem';
import Box from '@mui/material/Box';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import { useAppDispatch, useAppSelector } from '../store';
import { clearCredentials } from '../store/authSlice';
import { useLogoutMutation } from '../api/authApi';

export default function AppShell() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const user = useAppSelector((s) => s.auth.user);
  const [logout] = useLogoutMutation();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  const handleLogout = async () => {
    setAnchor(null);
    try { await logout(); } catch { /* ignore */ }
    dispatch(clearCredentials());
    navigate('/');
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <AppBar position="static" elevation={1}>
        <Toolbar>
          <AccountTreeIcon sx={{ mr: 1 }} />
          <Typography
            variant="h6"
            component="a"
            onClick={() => navigate('/dashboard')}
            sx={{ flexGrow: 1, cursor: 'pointer', textDecoration: 'none', color: 'inherit', fontWeight: 700 }}
          >
            Knowledge Graph
          </Typography>
          {user && (
            <>
              <IconButton onClick={(e) => setAnchor(e.currentTarget)} size="small">
                <Avatar src={user.avatar_url} sx={{ width: 32, height: 32 }}>
                  {user.name?.[0]}
                </Avatar>
              </IconButton>
              <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}>
                <MenuItem disabled>
                  <Typography variant="body2" color="text.secondary">{user.email}</Typography>
                </MenuItem>
                <MenuItem onClick={handleLogout}>Logout</MenuItem>
              </Menu>
            </>
          )}
        </Toolbar>
      </AppBar>
      <Box component="main" sx={{ flex: 1, p: 3 }}>
        <Outlet />
      </Box>
    </Box>
  );
}

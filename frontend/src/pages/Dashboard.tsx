import React, { useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Stack,
  Chip,
  IconButton,
  Tooltip,
  CircularProgress,
} from '@mui/material';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import SearchIcon from '@mui/icons-material/Search';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import FolderIcon from '@mui/icons-material/Folder';
import { useNavigate } from 'react-router-dom';
import {
  useListCollectionsQuery,
  useCreateCollectionMutation,
  useDeleteCollectionMutation,
  Collection,
} from '../api/collectionsApi';
import { useAppDispatch } from '../store';
import { setActiveCollection } from '../store/slices/collectionsSlice';
import { showSnackbar } from '../store/slices/uiSlice';

const Dashboard: React.FC = () => {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');

  const { data, isLoading } = useListCollectionsQuery();
  const [createCollection, { isLoading: isCreating }] = useCreateCollectionMutation();
  const [deleteCollection] = useDeleteCollectionMutation();

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      await createCollection({ name: newName, description: newDescription }).unwrap();
      dispatch(showSnackbar({ message: 'Collection created.', severity: 'success' }));
      setCreateOpen(false);
      setNewName('');
      setNewDescription('');
    } catch {
      dispatch(showSnackbar({ message: 'Failed to create collection.', severity: 'error' }));
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteCollection(id).unwrap();
      dispatch(showSnackbar({ message: 'Collection deleted.', severity: 'success' }));
    } catch {
      dispatch(showSnackbar({ message: 'Failed to delete collection.', severity: 'error' }));
    }
  };

  const columns: GridColDef<Collection>[] = [
    { field: 'name', headerName: 'Name', flex: 1, minWidth: 180 },
    {
      field: 'doc_count',
      headerName: 'Documents',
      width: 110,
      type: 'number',
    },
    {
      field: 'status',
      headerName: 'Status',
      width: 130,
      renderCell: ({ value }) => (
        <Chip
          label={value as string}
          color={
            value === 'active' ? 'success' : value === 'ingesting' ? 'warning' : 'default'
          }
          size="small"
        />
      ),
    },
    {
      field: 'created_at',
      headerName: 'Created',
      width: 160,
      valueFormatter: (value: string | undefined) =>
        value ? new Date(value).toLocaleDateString() : '—',
    },
    {
      field: 'actions',
      headerName: 'Actions',
      width: 150,
      sortable: false,
      renderCell: ({ row }) => (
        <Stack direction="row" spacing={0.5}>
          <Tooltip title="Open collection">
            <IconButton
              size="small"
              onClick={() => {
                dispatch(setActiveCollection(row.id));
                navigate(`/collection/${row.id}`);
              }}
            >
              <FolderIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Search in collection">
            <IconButton
              size="small"
              onClick={() => {
                dispatch(setActiveCollection(row.id));
                navigate('/search');
              }}
            >
              <SearchIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="View graph">
            <IconButton
              size="small"
              onClick={() => navigate(`/graph/${row.id}`)}
            >
              <AccountTreeIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Delete">
            <IconButton
              size="small"
              color="error"
              onClick={() => handleDelete(row.id)}
            >
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ];

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={3}>
        <Typography variant="h5" fontWeight={600}>
          My Collections
        </Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setCreateOpen(true)}
        >
          New Collection
        </Button>
      </Stack>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      ) : (
        <DataGrid
          rows={data?.collections ?? []}
          columns={columns}
          autoHeight
          pageSizeOptions={[10, 25, 50]}
          initialState={{ pagination: { paginationModel: { pageSize: 10 } } }}
          disableRowSelectionOnClick
          sx={{ bgcolor: 'background.paper' }}
        />
      )}

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Create Collection</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label="Name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              autoFocus
              fullWidth
            />
            <TextField
              label="Description (optional)"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              multiline
              rows={2}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleCreate}
            disabled={isCreating || !newName.trim()}
          >
            {isCreating ? 'Creating…' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default Dashboard;

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import CardActions from '@mui/material/CardActions';
import Grid from '@mui/material/Grid';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogContent from '@mui/material/DialogContent';
import DialogActions from '@mui/material/DialogActions';
import TextField from '@mui/material/TextField';
import IconButton from '@mui/material/IconButton';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Alert from '@mui/material/Alert';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import SearchIcon from '@mui/icons-material/Search';
import FolderIcon from '@mui/icons-material/Folder';
import {
  useListCollectionsQuery,
  useCreateCollectionMutation,
  useDeleteCollectionMutation,
} from '../api/collectionsApi';

export default function DashboardPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useListCollectionsQuery();
  const [createCollection] = useCreateCollectionMutation();
  const [deleteCollection] = useDeleteCollectionMutation();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!name.trim()) return;
    setCreating(true);
    try {
      const col = await createCollection({ name: name.trim(), description: description.trim() || undefined }).unwrap();
      setDialogOpen(false);
      setName('');
      setDescription('');
      navigate(`/collection/${col.id}`);
    } catch (err) {
      console.error('Create failed', err);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this collection?')) return;
    try { await deleteCollection(id).unwrap(); } catch { /* ignore */ }
  };

  const collections = data?.collections ?? [];

  return (
    <Box>
      <Box display="flex" alignItems="center" justifyContent="space-between" mb={3}>
        <Typography variant="h5" fontWeight={700}>Collections</Typography>
        <Box display="flex" gap={1}>
          <Button
            variant="outlined"
            startIcon={<SearchIcon />}
            onClick={() => navigate('/search')}
          >
            Search
          </Button>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setDialogOpen(true)}
          >
            New Collection
          </Button>
        </Box>
      </Box>

      {isLoading && <CircularProgress />}
      {error && <Alert severity="error">Failed to load collections</Alert>}

      {!isLoading && collections.length === 0 && (
        <Box textAlign="center" py={8}>
          <FolderIcon sx={{ fontSize: 64, color: 'text.disabled', mb: 2 }} />
          <Typography variant="h6" color="text.secondary">No collections yet</Typography>
          <Typography variant="body2" color="text.disabled" mb={3}>
            Create a collection and ingest documents to get started
          </Typography>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
            Create Collection
          </Button>
        </Box>
      )}

      <Grid container spacing={2}>
        {collections.map((col) => (
          <Grid item xs={12} sm={6} md={4} key={col.id}>
            <Card
              sx={{ cursor: 'pointer', '&:hover': { boxShadow: 4 } }}
              onClick={() => navigate(`/collection/${col.id}`)}
            >
              <CardContent>
                <Typography variant="h6" fontWeight={600} noWrap>{col.name}</Typography>
                {col.description && (
                  <Typography variant="body2" color="text.secondary" noWrap mt={0.5}>
                    {col.description}
                  </Typography>
                )}
                <Box mt={1.5} display="flex" gap={1} alignItems="center">
                  <Chip label={`${col.doc_count} docs`} size="small" variant="outlined" />
                  <Chip
                    label={col.status}
                    size="small"
                    color={col.status === 'active' ? 'success' : 'default'}
                  />
                </Box>
              </CardContent>
              <CardActions sx={{ justifyContent: 'flex-end', pt: 0 }}>
                <IconButton
                  size="small"
                  color="error"
                  onClick={(e) => handleDelete(col.id, e)}
                  title="Delete collection"
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </CardActions>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>New Collection</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            label="Name"
            fullWidth
            margin="normal"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
          />
          <TextField
            label="Description (optional)"
            fullWidth
            margin="normal"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            multiline
            rows={2}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleCreate}
            disabled={!name.trim() || creating}
          >
            {creating ? 'Creating...' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

import React, { useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  Box,
  Grid,
  Typography,
  Button,
  Paper,
  CircularProgress,
  Alert,
  List,
  ListItem,
  ListItemText,
  ListItemButton,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Stack,
  Chip,
} from '@mui/material';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import AddIcon from '@mui/icons-material/Add';
import {
  useGetOntologyQuery,
  useUpdateOntologyMutation,
  useGenerateOntologyMutation,
  EntityTypeDef,
} from '../api/ontologyApi';
import { useAppDispatch } from '../store';
import { showSnackbar } from '../store/slices/uiSlice';

const OntologyEditor: React.FC = () => {
  const { collectionId } = useParams<{ collectionId: string }>();
  const dispatch = useAppDispatch();
  const [addEntityOpen, setAddEntityOpen] = useState(false);
  const [newEntityName, setNewEntityName] = useState('');
  const [newEntityDesc, setNewEntityDesc] = useState('');
  const [selectedType, setSelectedType] = useState<string | null>(null);

  const { data: ontology, isLoading, isError } = useGetOntologyQuery(
    { collection_id: collectionId! },
    { skip: !collectionId }
  );
  const [updateOntology, { isLoading: isUpdating }] = useUpdateOntologyMutation();
  const [generateOntology, { isLoading: isGenerating }] = useGenerateOntologyMutation();

  const handleAddEntityType = async () => {
    if (!newEntityName.trim() || !ontology) return;
    const updated: Record<string, EntityTypeDef> = {
      ...ontology.entity_types,
      [newEntityName]: { description: newEntityDesc },
    };
    try {
      await updateOntology({ collection_id: collectionId!, entity_types: updated }).unwrap();
      dispatch(showSnackbar({ message: 'Entity type added.', severity: 'success' }));
      setAddEntityOpen(false);
      setNewEntityName('');
      setNewEntityDesc('');
    } catch {
      dispatch(showSnackbar({ message: 'Failed to update ontology.', severity: 'error' }));
    }
  };

  const handleGenerate = async () => {
    try {
      await generateOntology({ collection_id: collectionId! }).unwrap();
      dispatch(showSnackbar({ message: 'Ontology generated from documents.', severity: 'success' }));
    } catch {
      dispatch(showSnackbar({ message: 'Failed to generate ontology.', severity: 'error' }));
    }
  };

  const relationRows = Object.entries(ontology?.relationship_types ?? {}).map(([name, def]) => ({
    id: name,
    name,
    domain: def.domain.join(', '),
    range: def.range.join(', '),
    description: def.description ?? '',
  }));

  const relationColumns: GridColDef[] = [
    { field: 'name', headerName: 'Relation', flex: 1 },
    { field: 'domain', headerName: 'Domain', flex: 1 },
    { field: 'range', headerName: 'Range', flex: 1 },
    { field: 'description', headerName: 'Description', flex: 2 },
  ];

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (isError) {
    return <Alert severity="error">Failed to load ontology.</Alert>;
  }

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={3}>
        <Box>
          <Typography variant="h5" fontWeight={600}>Ontology Editor</Typography>
          {ontology && (
            <Typography variant="caption" color="text.secondary">
              Version {ontology.version}
            </Typography>
          )}
        </Box>
        <Button
          variant="outlined"
          startIcon={<AutoAwesomeIcon />}
          onClick={handleGenerate}
          disabled={isGenerating}
        >
          {isGenerating ? 'Generating…' : 'Generate from Documents'}
        </Button>
      </Stack>

      <Grid container spacing={3}>
        {/* Entity Types */}
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2, height: '100%' }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
              <Typography variant="subtitle1" fontWeight={600}>Entity Types</Typography>
              <Button size="small" startIcon={<AddIcon />} onClick={() => setAddEntityOpen(true)}>
                Add
              </Button>
            </Stack>
            <Divider sx={{ mb: 1 }} />
            <List dense disablePadding>
              {Object.entries(ontology?.entity_types ?? {}).map(([name, def]) => (
                <ListItem key={name} disablePadding>
                  <ListItemButton
                    selected={selectedType === name}
                    onClick={() => setSelectedType(selectedType === name ? null : name)}
                    sx={{ borderRadius: 1 }}
                  >
                    <ListItemText
                      primary={name}
                      secondary={def.description}
                      primaryTypographyProps={{ variant: 'body2', fontWeight: 500 }}
                      secondaryTypographyProps={{ variant: 'caption', noWrap: true }}
                    />
                  </ListItemButton>
                </ListItem>
              ))}
            </List>
            {selectedType && ontology?.entity_types[selectedType]?.examples && (
              <Box sx={{ mt: 1, px: 1 }}>
                <Typography variant="caption" color="text.secondary">Examples:</Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
                  {ontology.entity_types[selectedType].examples!.map((ex) => (
                    <Chip key={ex} label={ex} size="small" variant="outlined" sx={{ fontSize: '0.65rem' }} />
                  ))}
                </Box>
              </Box>
            )}
          </Paper>
        </Grid>

        {/* Relationship Types */}
        <Grid item xs={12} md={8}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight={600} gutterBottom>
              Relationship Types
            </Typography>
            <DataGrid
              rows={relationRows}
              columns={relationColumns}
              autoHeight
              disableRowSelectionOnClick
              pageSizeOptions={[10, 25]}
              initialState={{ pagination: { paginationModel: { pageSize: 10 } } }}
              sx={{ mt: 1 }}
            />
          </Paper>
        </Grid>
      </Grid>

      {/* Add entity type dialog */}
      <Dialog open={addEntityOpen} onClose={() => setAddEntityOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Entity Type</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label="Name"
              value={newEntityName}
              onChange={(e) => setNewEntityName(e.target.value)}
              autoFocus
              fullWidth
              size="small"
            />
            <TextField
              label="Description (optional)"
              value={newEntityDesc}
              onChange={(e) => setNewEntityDesc(e.target.value)}
              fullWidth
              size="small"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAddEntityOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleAddEntityType}
            disabled={isUpdating || !newEntityName.trim()}
          >
            Add
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default OntologyEditor;

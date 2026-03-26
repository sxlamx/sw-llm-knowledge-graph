import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Grid,
  Paper,
  Stack,
  Button,
  Chip,
  CircularProgress,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  Tooltip,
  IconButton,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import TuneIcon from '@mui/icons-material/Tune';
import DeleteIcon from '@mui/icons-material/Delete';
import PsychologyIcon from '@mui/icons-material/Psychology';
import ModelTrainingIcon from '@mui/icons-material/ModelTraining';
import LabelIcon from '@mui/icons-material/Label';
import { useGetCollectionQuery } from '../api/collectionsApi';
import { useListDocumentsQuery, useDeleteDocumentMutation } from '../api/documentsApi';
import { useTriggerNerPassMutation } from '../api/ingestApi';
import IngestPanel from '../components/ingest/IngestPanel';
import { useAppDispatch } from '../store';
import { showSnackbar } from '../store/slices/uiSlice';

const Collection: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const dispatch = useAppDispatch();

  const { data: collection, isLoading: loadingCollection } = useGetCollectionQuery(id ?? '', {
    skip: !id,
  });
  const { data: docData, isLoading: loadingDocs } = useListDocumentsQuery(
    { collection_id: id ?? '' },
    { skip: !id }
  );
  const [deleteDocument] = useDeleteDocumentMutation();
  const [triggerNer, { isLoading: nerRunning }] = useTriggerNerPassMutation();

  const handleNerPass = async () => {
    try {
      const result = await triggerNer(id!).unwrap();
      dispatch(showSnackbar({ message: `NER tagging started (job: ${result.job_id.slice(0, 8)}…). This may take a while.`, severity: 'info' }));
    } catch {
      dispatch(showSnackbar({ message: 'Failed to start NER tagging.', severity: 'error' }));
    }
  };

  const handleDeleteDoc = async (docId: string) => {
    try {
      await deleteDocument({ doc_id: docId, collection_id: id! }).unwrap();
      dispatch(showSnackbar({ message: 'Document deleted.', severity: 'success' }));
    } catch {
      dispatch(showSnackbar({ message: 'Failed to delete document.', severity: 'error' }));
    }
  };

  if (loadingCollection) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!collection) {
    return <Typography color="error">Collection not found.</Typography>;
  }

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} mb={3}>
        <IconButton onClick={() => navigate('/dashboard')}>
          <ArrowBackIcon />
        </IconButton>
        <Box flex={1}>
          <Typography variant="h5" fontWeight={600}>{collection.name}</Typography>
          {collection.description && (
            <Typography variant="body2" color="text.secondary">{collection.description}</Typography>
          )}
        </Box>
        <Chip label={`${collection.doc_count} docs`} variant="outlined" size="small" />
        <Button
          variant="outlined"
          startIcon={<AccountTreeIcon />}
          onClick={() => navigate(`/graph/${id}`)}
          size="small"
        >
          View Graph
        </Button>
        <Button
          variant="outlined"
          startIcon={<TuneIcon />}
          onClick={() => navigate(`/ontology/${id}`)}
          size="small"
        >
          Ontology
        </Button>
        <Button
          variant="outlined"
          startIcon={<PsychologyIcon />}
          onClick={() => navigate(`/agent/${id}`)}
          size="small"
        >
          Agent Query
        </Button>
        <Button
          variant="outlined"
          startIcon={<ModelTrainingIcon />}
          onClick={() => navigate(`/finetune/${id}`)}
          size="small"
        >
          Fine-Tune
        </Button>
        <Tooltip title="Run spaCy + regex NER tagging on all untagged chunks (needed for Graph view)">
          <Button
            variant="contained"
            color="secondary"
            startIcon={nerRunning ? <CircularProgress size={16} color="inherit" /> : <LabelIcon />}
            onClick={handleNerPass}
            disabled={nerRunning}
            size="small"
          >
            Tag NER
          </Button>
        </Tooltip>
      </Stack>

      <Grid container spacing={3}>
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2 }}>
            <IngestPanel collectionId={id!} />
          </Paper>
        </Grid>

        <Grid item xs={12} md={8}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight={600} gutterBottom>
              Documents
            </Typography>

            {loadingDocs ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
                <CircularProgress size={24} />
              </Box>
            ) : docData?.documents.length ? (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Title</TableCell>
                    <TableCell>Type</TableCell>
                    <TableCell align="right">Chunks</TableCell>
                    <TableCell align="right">Status</TableCell>
                    <TableCell />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {docData.documents.map((doc) => (
                    <TableRow key={doc.id} hover>
                      <TableCell>
                        <Typography variant="body2" noWrap sx={{ maxWidth: 240 }}>
                          {doc.title}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Chip label={doc.file_type ?? '?'} size="small" variant="outlined" sx={{ fontSize: '0.65rem' }} />
                      </TableCell>
                      <TableCell align="right">{doc.chunk_count}</TableCell>
                      <TableCell align="right">
                        <Chip
                          label={doc.status}
                          size="small"
                          color={doc.status === 'indexed' ? 'success' : 'default'}
                        />
                      </TableCell>
                      <TableCell align="right">
                        <Tooltip title="View document graph">
                          <IconButton
                            size="small"
                            onClick={() => navigate(`/graph/${id}?doc_id=${doc.id}`)}
                          >
                            <AccountTreeIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Delete document">
                          <IconButton size="small" color="error" onClick={() => handleDeleteDoc(doc.id)}>
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
                No documents yet. Use the ingest panel to add documents.
              </Typography>
            )}
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
};

export default Collection;

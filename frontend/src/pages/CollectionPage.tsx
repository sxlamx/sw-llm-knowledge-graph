import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Paper from '@mui/material/Paper';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import LinearProgress from '@mui/material/LinearProgress';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Alert from '@mui/material/Alert';
import Breadcrumbs from '@mui/material/Breadcrumbs';
import Link from '@mui/material/Link';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import SearchIcon from '@mui/icons-material/Search';
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile';
import { useGetCollectionQuery } from '../api/collectionsApi';
import { useStartIngestMutation, useGetJobQuery } from '../api/ingestApi';
import { useListDocumentsQuery } from '../api/documentsApi';

function JobStatusChip({ status }: { status: string }) {
  const colorMap: Record<string, 'default' | 'info' | 'success' | 'error' | 'warning'> = {
    pending: 'default',
    running: 'info',
    completed: 'success',
    failed: 'error',
    cancelled: 'warning',
  };
  return <Chip label={status} size="small" color={colorMap[status] ?? 'default'} />;
}

function ActiveJob({ jobId }: { jobId: string }) {
  const [pollInterval, setPollInterval] = useState(2000);
  const { data: job } = useGetJobQuery(jobId, { pollingInterval: pollInterval });

  useEffect(() => {
    if (job && job.status !== 'running' && job.status !== 'pending') {
      setPollInterval(0);
    }
  }, [job?.status]);

  if (!job) return null;

  return (
    <Paper sx={{ p: 2, mt: 2 }}>
      <Box display="flex" alignItems="center" gap={1} mb={1}>
        <Typography variant="subtitle2">Ingest Job</Typography>
        <JobStatusChip status={job.status} />
      </Box>
      {(job.status === 'running' || job.status === 'pending') && (
        <>
          <LinearProgress
            variant="determinate"
            value={Math.round(job.progress * 100)}
            sx={{ mb: 1 }}
          />
          <Typography variant="caption" color="text.secondary">
            {job.processed_docs} / {job.total_docs || '?'} documents
          </Typography>
        </>
      )}
      {job.status === 'completed' && (
        <Typography variant="body2" color="success.main">
          Completed — {job.processed_docs} documents ingested
        </Typography>
      )}
      {job.status === 'failed' && (
        <Typography variant="body2" color="error.main">
          Failed: {job.error_msg}
        </Typography>
      )}
    </Paper>
  );
}

export default function CollectionPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: collection, isLoading: colLoading } = useGetCollectionQuery(id!);
  const { data: docsData, isLoading: docsLoading } = useListDocumentsQuery(
    { collection_id: id! },
    { skip: !id },
  );
  const [startIngest] = useStartIngestMutation();

  const [folderPath, setFolderPath] = useState('');
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState('');

  // Pre-populate folder path from saved collection.folder_path
  useEffect(() => {
    if (collection?.folder_path && !folderPath) {
      setFolderPath(collection.folder_path);
    }
  }, [collection?.folder_path]);

  const handleIngest = async () => {
    if (!folderPath.trim() || !id) return;
    setIngesting(true);
    setIngestError('');
    try {
      const job = await startIngest({
        collection_id: id,
        folder_path: folderPath.trim(),
      }).unwrap();
      setActiveJobId(job.id);
      // keep folderPath so user can re-run without retyping
    } catch (err: any) {
      setIngestError(err?.data?.detail ?? 'Failed to start ingest');
    } finally {
      setIngesting(false);
    }
  };

  if (colLoading) return <CircularProgress />;
  if (!collection) return <Alert severity="error">Collection not found</Alert>;

  const documents = docsData?.documents ?? [];

  return (
    <Box>
      <Breadcrumbs sx={{ mb: 2 }}>
        <Link underline="hover" color="inherit" sx={{ cursor: 'pointer' }} onClick={() => navigate('/dashboard')}>
          Collections
        </Link>
        <Typography color="text.primary">{collection.name}</Typography>
      </Breadcrumbs>

      <Box display="flex" alignItems="center" justifyContent="space-between" mb={2}>
        <Box>
          <Typography variant="h5" fontWeight={700}>{collection.name}</Typography>
          {collection.description && (
            <Typography variant="body2" color="text.secondary">{collection.description}</Typography>
          )}
        </Box>
        <Button
          variant="outlined"
          startIcon={<SearchIcon />}
          onClick={() => navigate(`/search?collection_id=${id}`)}
        >
          Search
        </Button>
      </Box>

      <Paper sx={{ p: 2.5, mb: 3 }}>
        <Typography variant="subtitle1" fontWeight={600} mb={1.5}>Ingest Documents</Typography>
        <Box display="flex" gap={1}>
          <TextField
            label="Folder path"
            placeholder="/data/documents/my-folder"
            fullWidth
            size="small"
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleIngest()}
          />
          <Button
            variant="contained"
            startIcon={<PlayArrowIcon />}
            onClick={handleIngest}
            disabled={ingesting || !folderPath.trim()}
            sx={{ whiteSpace: 'nowrap' }}
          >
            Start
          </Button>
        </Box>
        {ingestError && <Alert severity="error" sx={{ mt: 1 }}>{ingestError}</Alert>}
        {activeJobId && <ActiveJob jobId={activeJobId} />}
      </Paper>

      <Typography variant="subtitle1" fontWeight={600} mb={1.5}>
        Documents ({docsData?.total ?? 0})
      </Typography>

      {docsLoading ? (
        <CircularProgress size={24} />
      ) : documents.length === 0 ? (
        <Box textAlign="center" py={4}>
          <InsertDriveFileIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
          <Typography variant="body2" color="text.secondary">
            No documents yet — ingest a folder to get started
          </Typography>
        </Box>
      ) : (
        <TableContainer component={Paper}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Title</TableCell>
                <TableCell>Type</TableCell>
                <TableCell>Path</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {documents.map((doc) => (
                <TableRow key={doc.id} hover>
                  <TableCell>{doc.title}</TableCell>
                  <TableCell>
                    <Chip label={doc.file_type} size="small" variant="outlined" />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary" noWrap sx={{ maxWidth: 300, display: 'block' }}>
                      {doc.path ?? '—'}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}

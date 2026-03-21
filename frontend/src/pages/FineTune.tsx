import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Paper,
  Typography,
  Button,
  Stack,
  Chip,
  Alert,
  IconButton,
  Divider,
  TextField,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  LinearProgress,
  Tooltip,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import DatasetIcon from '@mui/icons-material/Dataset';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import { useGetCollectionQuery } from '../api/collectionsApi';
import {
  useExportDatasetMutation,
  useStartFineTuneMutation,
  useGetFineTuneStatusQuery,
  FineTuneExample,
} from '../api/finetuneApi';
import { useAppDispatch } from '../store';
import { showSnackbar } from '../store/slices/uiSlice';

// ---------------------------------------------------------------------------
// Job status polling sub-component
// ---------------------------------------------------------------------------

const JobStatusPanel: React.FC<{ jobId: string }> = ({ jobId }) => {
  const { data, isLoading, refetch } = useGetFineTuneStatusQuery(
    { job_id: jobId },
    { pollingInterval: 15_000 }
  );

  const statusColor = (s?: string) => {
    if (!s) return 'default' as const;
    if (s === 'succeeded') return 'success' as const;
    if (s === 'failed' || s === 'cancelled') return 'error' as const;
    return 'info' as const;
  };

  return (
    <Paper sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1} mb={1}>
        <Typography variant="subtitle2" fontWeight={600}>Job Status</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace' }}>
          {jobId}
        </Typography>
        <Box flex={1} />
        <Tooltip title="Refresh">
          <IconButton size="small" onClick={refetch} disabled={isLoading}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Stack>

      {isLoading ? (
        <LinearProgress />
      ) : data ? (
        <Stack spacing={1}>
          <Stack direction="row" spacing={1} alignItems="center">
            {data.status === 'succeeded' ? (
              <CheckCircleIcon color="success" fontSize="small" />
            ) : data.status === 'failed' ? (
              <ErrorIcon color="error" fontSize="small" />
            ) : (
              <CircularProgress size={16} />
            )}
            <Chip label={data.status} size="small" color={statusColor(data.status)} />
            {data.model && (
              <Typography variant="caption" color="text.secondary">
                base: {data.model}
              </Typography>
            )}
          </Stack>

          {data.fine_tuned_model && (
            <Alert severity="success" icon={false}>
              <Typography variant="caption" fontWeight={600}>Fine-tuned model ID:</Typography>
              <Typography variant="caption" sx={{ fontFamily: 'monospace', display: 'block' }}>
                {data.fine_tuned_model}
              </Typography>
            </Alert>
          )}

          {data.trained_tokens != null && data.trained_tokens > 0 && (
            <Typography variant="caption" color="text.secondary">
              Trained tokens: {data.trained_tokens.toLocaleString()}
            </Typography>
          )}

          {data.error && (
            <Alert severity="error" icon={false}>
              <Typography variant="caption">{data.error}</Typography>
            </Alert>
          )}
        </Stack>
      ) : (
        <Typography variant="caption" color="error">Failed to load job status.</Typography>
      )}
    </Paper>
  );
};

// ---------------------------------------------------------------------------
// Dataset preview sub-component
// ---------------------------------------------------------------------------

const DatasetPreview: React.FC<{ examples: FineTuneExample[]; total: number }> = ({ examples, total }) => (
  <Paper sx={{ p: 2 }}>
    <Stack direction="row" alignItems="center" mb={1}>
      <Typography variant="subtitle2" fontWeight={600}>Dataset Preview</Typography>
      <Box flex={1} />
      <Chip label={`${total} examples`} size="small" color="primary" />
    </Stack>
    <Divider sx={{ mb: 1.5 }} />
    {examples.slice(0, 5).map((ex, i) => (
      <Box key={i} sx={{ mb: 1.5, p: 1, bgcolor: 'action.hover', borderRadius: 1 }}>
        <Typography variant="caption" fontWeight={600} color="primary" gutterBottom display="block">
          Example {i + 1}
        </Typography>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ py: 0.25, fontSize: '0.65rem' }}>Role</TableCell>
              <TableCell sx={{ py: 0.25, fontSize: '0.65rem' }}>Content</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {ex.messages.map((msg, j) => (
              <TableRow key={j}>
                <TableCell sx={{ py: 0.25 }}>
                  <Chip
                    label={msg.role}
                    size="small"
                    variant="outlined"
                    color={msg.role === 'assistant' ? 'success' : msg.role === 'user' ? 'primary' : 'default'}
                    sx={{ height: 18, fontSize: '0.6rem' }}
                  />
                </TableCell>
                <TableCell sx={{ py: 0.25 }}>
                  <Typography variant="caption" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                    {msg.content.slice(0, 200)}{msg.content.length > 200 ? '…' : ''}
                  </Typography>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    ))}
    {total > 5 && (
      <Typography variant="caption" color="text.secondary">
        …and {total - 5} more examples (first 5 shown).
      </Typography>
    )}
  </Paper>
);

// ---------------------------------------------------------------------------
// FineTune page
// ---------------------------------------------------------------------------

const FineTune: React.FC = () => {
  const { collectionId } = useParams<{ collectionId: string }>();
  const navigate = useNavigate();
  const dispatch = useAppDispatch();

  const [baseModel, setBaseModel] = useState('gpt-4o-mini-2024-07-18');
  const [suffix, setSuffix] = useState('kg-extraction');
  const [nEpochs, setNEpochs] = useState(3);
  const [maxExamples, setMaxExamples] = useState(5000);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const { data: collection } = useGetCollectionQuery(collectionId ?? '', { skip: !collectionId });
  const [exportDataset, { data: exportData, isLoading: exporting }] = useExportDatasetMutation();
  const [startFineTune, { isLoading: starting }] = useStartFineTuneMutation();

  const handleExport = async () => {
    if (!collectionId) return;
    try {
      await exportDataset({ collection_id: collectionId, max_examples: maxExamples }).unwrap();
      dispatch(showSnackbar({ message: 'Dataset exported successfully.', severity: 'success' }));
    } catch {
      dispatch(showSnackbar({ message: 'Export failed.', severity: 'error' }));
    }
  };

  const handleStart = async () => {
    if (!collectionId) return;
    try {
      const result = await startFineTune({
        collection_id: collectionId,
        base_model: baseModel,
        suffix,
        n_epochs: nEpochs,
        max_examples: maxExamples,
      }).unwrap();
      setActiveJobId(result.job_id);
      dispatch(showSnackbar({ message: `Fine-tuning job started: ${result.job_id}`, severity: 'success' }));
    } catch (err: unknown) {
      const msg = (err as { data?: { detail?: string } })?.data?.detail ?? 'Failed to start fine-tuning.';
      dispatch(showSnackbar({ message: msg, severity: 'error' }));
    }
  };

  return (
    <Box sx={{ maxWidth: 800, mx: 'auto' }}>
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={1} mb={3}>
        <IconButton onClick={() => navigate(`/collection/${collectionId}`)}>
          <ArrowBackIcon />
        </IconButton>
        <Box flex={1}>
          <Typography variant="h5" fontWeight={600}>LLM Fine-Tuning</Typography>
          {collection && (
            <Typography variant="body2" color="text.secondary">{collection.name}</Typography>
          )}
        </Box>
      </Stack>

      <Alert severity="info" sx={{ mb: 2 }}>
        Fine-tuning exports positive user feedback records from this collection as training examples,
        then uploads them to OpenAI to create a domain-adapted extraction model.
      </Alert>

      {/* Configuration */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle2" fontWeight={600} gutterBottom>Configuration</Typography>
        <Divider sx={{ mb: 2 }} />
        <Stack spacing={2}>
          <Stack direction="row" spacing={2}>
            <TextField
              label="Base model"
              value={baseModel}
              onChange={(e) => setBaseModel(e.target.value)}
              size="small"
              sx={{ flex: 2 }}
            />
            <TextField
              label="Suffix"
              value={suffix}
              onChange={(e) => setSuffix(e.target.value)}
              size="small"
              sx={{ flex: 1 }}
              helperText="Max 40 chars"
              inputProps={{ maxLength: 40 }}
            />
          </Stack>
          <Stack direction="row" spacing={2}>
            <TextField
              label="Epochs"
              type="number"
              value={nEpochs}
              onChange={(e) => setNEpochs(Number(e.target.value))}
              size="small"
              inputProps={{ min: 1, max: 10 }}
              sx={{ flex: 1 }}
            />
            <TextField
              label="Max examples"
              type="number"
              value={maxExamples}
              onChange={(e) => setMaxExamples(Number(e.target.value))}
              size="small"
              inputProps={{ min: 10, max: 50000 }}
              sx={{ flex: 1 }}
            />
          </Stack>
        </Stack>
      </Paper>

      {/* Actions */}
      <Stack direction="row" spacing={2} mb={2}>
        <Button
          variant="outlined"
          startIcon={exporting ? <CircularProgress size={16} /> : <DatasetIcon />}
          onClick={handleExport}
          disabled={exporting || starting}
        >
          Preview Dataset
        </Button>
        <Button
          variant="contained"
          startIcon={starting ? <CircularProgress size={16} /> : <PlayArrowIcon />}
          onClick={handleStart}
          disabled={exporting || starting}
        >
          Start Fine-Tuning
        </Button>
      </Stack>

      {/* Dataset preview */}
      {exportData && (
        <Box sx={{ mb: 2 }}>
          <DatasetPreview examples={exportData.examples} total={exportData.total} />
        </Box>
      )}

      {/* Active job status */}
      {activeJobId && (
        <Box sx={{ mb: 2 }}>
          <JobStatusPanel jobId={activeJobId} />
        </Box>
      )}
    </Box>
  );
};

export default FineTune;

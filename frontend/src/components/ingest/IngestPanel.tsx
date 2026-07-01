import React, { useState, useEffect } from 'react';
import {
  Box,
  Button,
  TextField,
  Typography,
  Stack,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Slider,
  FormControlLabel,
  Switch,
} from '@mui/material';
import FolderOpenIcon from '@mui/icons-material/FolderOpen';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { useAppDispatch } from '../../store';
import { useStartIngestJobMutation } from '../../api/ingestApi';
import { showSnackbar } from '../../store/slices/uiSlice';
import { api } from '../../api/baseApi';
import ProgressBar from './ProgressBar';
import JobStatusChip from './JobStatusChip';
import TemplatePicker from './TemplatePicker';

interface Props {
  collectionId: string;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

const IngestPanel: React.FC<Props> = ({ collectionId }) => {
  const dispatch = useAppDispatch();
  const [folderPath, setFolderPath] = useState('');
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [currentFile, setCurrentFile] = useState<string | undefined>();
  const [jobStatus, setJobStatus] = useState<string>('pending');
  const [chunkSize, setChunkSize] = useState(512);
  const [chunkOverlap, setChunkOverlap] = useState(50);
  const [extractEntities, setExtractEntities] = useState(true);
  const [template, setTemplate] = useState<string | null>(null);

  const [sseError, setSseError] = useState<'reconnecting' | 'failed' | null>(null);

  const [startIngest, { isLoading }] = useStartIngestJobMutation();

  const handleSelectFolder = async () => {
    try {
      const dirHandle = await (window as Window & typeof globalThis & { showDirectoryPicker?: (opts?: object) => Promise<{ name: string }> }).showDirectoryPicker?.({ mode: 'read' });
      if (dirHandle) {
        setFolderPath(dirHandle.name);
      }
    } catch {
      // user cancelled or not supported
    }
  };

  const handleStart = async () => {
    if (!folderPath.trim()) {
      dispatch(showSnackbar({ message: 'Please enter or select a folder path.', severity: 'warning' }));
      return;
    }
    try {
      const result = await startIngest({
        collection_id: collectionId,
        folder_path: folderPath,
        options: { chunk_size_tokens: chunkSize, chunk_overlap_tokens: chunkOverlap, extract_entities: extractEntities, template: template || undefined },
      }).unwrap();
      setActiveJobId(result.job_id ?? result.id);
      setProgress(0);
      setJobStatus('running');
    } catch {
      dispatch(showSnackbar({ message: 'Failed to start ingest job.', severity: 'error' }));
    }
  };

  useEffect(() => {
    if (!activeJobId) return;

    let sseRetryCount = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let eventSource: EventSource | null = null;

    const createEventSource = () => {
      eventSource = new EventSource(
        `${API_BASE}/ingest/jobs/${activeJobId}/stream`,
        { withCredentials: true }
      );

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string) as {
            type: string;
            progress?: number;
            current_file?: string;
            status?: string;
          };
          if (data.type === 'progress') {
            setProgress(data.progress ?? 0);
            setCurrentFile(data.current_file);
          }
          if (data.type === 'completed') {
            setProgress(1.0);
            setJobStatus('completed');
            eventSource?.close();
            dispatch(api.util.invalidateTags([{ type: 'Collection', id: collectionId }, 'Document']));
            dispatch(showSnackbar({ message: 'Ingest completed successfully.', severity: 'success' }));
          }
          if (data.type === 'failed') {
            setJobStatus('failed');
            eventSource?.close();
            dispatch(showSnackbar({ message: 'Ingest job failed.', severity: 'error' }));
          }
        } catch {
          // ignore
        }
      };

      eventSource.onerror = () => {
        eventSource?.close();
        eventSource = null;
        sseRetryCount++;
        if (sseRetryCount >= 3) {
          setSseError('failed');
        } else {
          setSseError('reconnecting');
          reconnectTimer = setTimeout(() => {
            createEventSource();
          }, 3000);
        }
      };

      if (sseRetryCount > 0) {
        sseRetryCount = 0;
        setSseError(null);
      }
    };

    createEventSource();

    return () => {
      eventSource?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, [activeJobId, collectionId, dispatch]);

  return (
    <Box>
      <Typography variant="subtitle1" gutterBottom fontWeight={600}>
        Ingest Documents
      </Typography>

      <Stack spacing={2}>
        <Stack direction="row" spacing={1} alignItems="center">
          {'showDirectoryPicker' in window && (
            <Button
              variant="outlined"
              startIcon={<FolderOpenIcon />}
              onClick={handleSelectFolder}
              size="small"
            >
              Browse
            </Button>
          )}
          <TextField
            label="Folder path"
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
            size="small"
            placeholder="/path/to/documents"
            fullWidth
          />
        </Stack>

        <Accordion disableGutters elevation={0} sx={{ border: 1, borderColor: 'divider' }}>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="body2">Advanced options</Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Stack spacing={2}>
              <Box>
                <Typography variant="caption" gutterBottom>
                  Chunk size: {chunkSize} tokens
                </Typography>
                <Slider
                  value={chunkSize}
                  onChange={(_, v) => setChunkSize(v as number)}
                  min={128}
                  max={1024}
                  step={64}
                  size="small"
                />
              </Box>
              <Box>
                <Typography variant="caption" gutterBottom>
                  Chunk overlap: {chunkOverlap} tokens
                </Typography>
                <Slider
                  value={chunkOverlap}
                  onChange={(_, v) => setChunkOverlap(v as number)}
                  min={0}
                  max={200}
                  step={10}
                  size="small"
                />
              </Box>
              <FormControlLabel
                control={
                  <Switch
                    checked={extractEntities}
                    onChange={(e) => setExtractEntities(e.target.checked)}
                    size="small"
                  />
                }
                label={<Typography variant="body2">Extract entities &amp; relations</Typography>}
              />
              <Box mt={1}>
                <TemplatePicker value={template} onChange={setTemplate} />
              </Box>
            </Stack>
          </AccordionDetails>
        </Accordion>

        <Button
          variant="contained"
          onClick={handleStart}
          disabled={isLoading || jobStatus === 'running'}
          fullWidth
        >
          {isLoading ? 'Starting...' : 'Start Ingest'}
        </Button>

        {sseError === 'reconnecting' && (
          <Typography variant="caption" color="warning.main">
            Connection lost. Reconnecting...
          </Typography>
        )}
        {sseError === 'failed' && (
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="caption" color="error.main">
              Unable to reconnect.
            </Typography>
            <Button
              size="small"
              variant="outlined"
              onClick={() => setSseError(null)}
            >
              Retry
            </Button>
          </Stack>
        )}
        {activeJobId && (
          <Box>
            <Stack direction="row" alignItems="center" spacing={1} mb={1}>
              <Typography variant="caption">Job:</Typography>
              <JobStatusChip status={jobStatus as 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'} />
            </Stack>
            <ProgressBar
              progress={progress}
              currentFile={currentFile}
              label="Processing"
            />
          </Box>
        )}
      </Stack>
    </Box>
  );
};

export default IngestPanel;

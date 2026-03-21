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

interface Props {
  collectionId: string;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

const IngestPanel: React.FC<Props> = ({ collectionId }) => {
  const dispatch = useAppDispatch();
  const [folderPath, setFolderPath] = useState('');
  const [showPathInput, setShowPathInput] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [currentFile, setCurrentFile] = useState<string | undefined>();
  const [jobStatus, setJobStatus] = useState<string>('pending');
  const [chunkSize, setChunkSize] = useState(512);
  const [chunkOverlap, setChunkOverlap] = useState(50);
  const [extractEntities, setExtractEntities] = useState(true);

  const [startIngest, { isLoading }] = useStartIngestJobMutation();

  const handleSelectFolder = async () => {
    try {
      const dirHandle = await (window as Window & typeof globalThis & { showDirectoryPicker?: (opts?: object) => Promise<{ name: string }> }).showDirectoryPicker?.({ mode: 'read' });
      if (dirHandle) setFolderPath(dirHandle.name);
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setShowPathInput(true);
      }
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
        options: { chunk_size: chunkSize, chunk_overlap: chunkOverlap, extract_entities: extractEntities },
      }).unwrap();
      setActiveJobId(result.id);
      setProgress(0);
      setJobStatus('running');
    } catch {
      dispatch(showSnackbar({ message: 'Failed to start ingest job.', severity: 'error' }));
    }
  };

  useEffect(() => {
    if (!activeJobId) return;

    const eventSource = new EventSource(
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
          eventSource.close();
          dispatch(api.util.invalidateTags([{ type: 'Collection', id: collectionId }, 'Document']));
          dispatch(showSnackbar({ message: 'Ingest completed successfully.', severity: 'success' }));
        }
        if (data.type === 'failed') {
          setJobStatus('failed');
          eventSource.close();
          dispatch(showSnackbar({ message: 'Ingest job failed.', severity: 'error' }));
        }
      } catch {
        // ignore
      }
    };

    eventSource.onerror = () => {
      eventSource.close();
    };

    return () => eventSource.close();
  }, [activeJobId, collectionId, dispatch]);

  return (
    <Box>
      <Typography variant="subtitle1" gutterBottom fontWeight={600}>
        Ingest Documents
      </Typography>

      <Stack spacing={2}>
        {!showPathInput ? (
          <Button
            variant="outlined"
            startIcon={<FolderOpenIcon />}
            onClick={handleSelectFolder}
          >
            {folderPath ? folderPath : 'Select Folder'}
          </Button>
        ) : null}

        {(showPathInput || !('showDirectoryPicker' in window)) && (
          <TextField
            label="Folder path"
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
            size="small"
            placeholder="/path/to/documents"
            fullWidth
          />
        )}

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

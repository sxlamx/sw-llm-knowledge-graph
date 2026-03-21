import React, { useState, useRef, useCallback, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  Stack,
  Chip,
  CircularProgress,
  Alert,
  Divider,
  IconButton,
  Slider,
  Tooltip,
  LinearProgress,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import SendIcon from '@mui/icons-material/Send';
import StopIcon from '@mui/icons-material/Stop';
import PsychologyIcon from '@mui/icons-material/Psychology';
import VisibilityIcon from '@mui/icons-material/Visibility';
import LightbulbIcon from '@mui/icons-material/Lightbulb';
import { useGetCollectionQuery } from '../api/collectionsApi';
import { useGetAgentStatusQuery, streamAgentQuery, AgentEvent } from '../api/agentApi';
import { useAppSelector } from '../store';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
// Strip trailing /api/v1 to get server root for the streaming fetch
const SERVER_BASE = API_BASE.endsWith('/api/v1')
  ? API_BASE.slice(0, -7)
  : '';

interface AgentStep {
  type: AgentEvent['type'];
  hop?: number;
  content: string;
}

const EventIcon: React.FC<{ type: AgentEvent['type'] }> = ({ type }) => {
  switch (type) {
    case 'thought': return <LightbulbIcon fontSize="small" color="warning" />;
    case 'observation': return <VisibilityIcon fontSize="small" color="info" />;
    case 'answer': return <PsychologyIcon fontSize="small" color="success" />;
    default: return null;
  }
};

const AgentQuery: React.FC = () => {
  const { collectionId } = useParams<{ collectionId: string }>();
  const navigate = useNavigate();
  const token = useAppSelector((s) => s.auth.accessToken ?? '');

  const [query, setQuery] = useState('');
  const [maxHops, setMaxHops] = useState(4);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [answer, setAnswer] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const answerRef = useRef('');
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data: collection } = useGetCollectionQuery(collectionId ?? '', { skip: !collectionId });
  const { data: status } = useGetAgentStatusQuery(
    { collection_id: collectionId ?? '' },
    { skip: !collectionId }
  );

  // Auto-scroll to bottom as events stream in
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [steps, answer]);

  const handleSubmit = useCallback(() => {
    if (!query.trim() || isStreaming || !collectionId) return;

    setSteps([]);
    setAnswer('');
    answerRef.current = '';
    setError(null);
    setIsStreaming(true);

    const onEvent = (event: AgentEvent) => {
      if (event.type === 'token') {
        answerRef.current += event.content ?? '';
        setAnswer(answerRef.current);
      } else if (event.type === 'error') {
        setError(event.content ?? 'Agent error');
      } else if (['thought', 'observation', 'start', 'answer'].includes(event.type)) {
        setSteps((prev) => [
          ...prev,
          { type: event.type, hop: event.hop, content: event.content ?? '' },
        ]);
      }
    };

    const onDone = () => setIsStreaming(false);
    const onError = (msg: string) => { setError(msg); setIsStreaming(false); };

    abortRef.current = streamAgentQuery(
      SERVER_BASE,
      token,
      collectionId,
      query.trim(),
      maxHops,
      onEvent,
      onDone,
      onError,
    );
  }, [query, isStreaming, collectionId, token, maxHops]);

  const handleStop = () => {
    abortRef.current?.abort();
    setIsStreaming(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit();
  };

  return (
    <Box sx={{ maxWidth: 860, mx: 'auto' }}>
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={1} mb={3}>
        <IconButton onClick={() => navigate(`/collection/${collectionId}`)}>
          <ArrowBackIcon />
        </IconButton>
        <Box flex={1}>
          <Typography variant="h5" fontWeight={600}>Agent Query</Typography>
          {collection && (
            <Typography variant="body2" color="text.secondary">{collection.name}</Typography>
          )}
        </Box>
        {status && (
          <Chip
            label={status.ready ? `${status.node_count} nodes ready` : 'Graph empty'}
            color={status.ready ? 'success' : 'warning'}
            size="small"
            variant="outlined"
          />
        )}
      </Stack>

      {status && !status.ready && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          The knowledge graph for this collection is empty. Ingest some documents first to enable agent queries.
        </Alert>
      )}

      {/* Query input */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <TextField
          fullWidth
          multiline
          minRows={2}
          maxRows={6}
          placeholder='Ask a multi-hop question, e.g. "Who founded companies that later partnered with OpenAI?"'
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isStreaming}
          sx={{ mb: 2 }}
        />

        <Stack direction="row" alignItems="center" spacing={3}>
          <Box sx={{ minWidth: 200 }}>
            <Typography variant="caption" color="text.secondary" gutterBottom display="block">
              Max hops: {maxHops}
            </Typography>
            <Slider
              value={maxHops}
              onChange={(_, v) => setMaxHops(v as number)}
              min={1}
              max={6}
              step={1}
              marks
              size="small"
              disabled={isStreaming}
            />
          </Box>

          <Box flex={1} />

          {isStreaming ? (
            <Button
              variant="outlined"
              color="error"
              startIcon={<StopIcon />}
              onClick={handleStop}
            >
              Stop
            </Button>
          ) : (
            <Tooltip title="Ctrl+Enter to submit">
              <span>
                <Button
                  variant="contained"
                  endIcon={<SendIcon />}
                  onClick={handleSubmit}
                  disabled={!query.trim() || !status?.ready}
                >
                  Ask Agent
                </Button>
              </span>
            </Tooltip>
          )}
        </Stack>

        {isStreaming && <LinearProgress sx={{ mt: 1.5 }} />}
      </Paper>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Reasoning steps */}
      {steps.length > 0 && (
        <Paper sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle2" fontWeight={600} gutterBottom>
            Agent Reasoning
          </Typography>
          <Stack spacing={1}>
            {steps.map((step, i) => {
              if (step.type === 'answer') return null;  // answer shown separately
              return (
                <Stack key={i} direction="row" spacing={1} alignItems="flex-start">
                  <Box sx={{ pt: 0.25, flexShrink: 0 }}>
                    <EventIcon type={step.type} />
                  </Box>
                  <Box>
                    <Stack direction="row" spacing={0.5} alignItems="center" mb={0.25}>
                      <Chip
                        label={step.type}
                        size="small"
                        variant="outlined"
                        sx={{ height: 18, fontSize: '0.6rem', textTransform: 'capitalize' }}
                        color={
                          step.type === 'thought' ? 'warning' :
                          step.type === 'observation' ? 'info' : 'default'
                        }
                      />
                      {step.hop != null && (
                        <Typography variant="caption" color="text.secondary">
                          hop {step.hop}
                        </Typography>
                      )}
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      {step.content}
                    </Typography>
                  </Box>
                </Stack>
              );
            })}
          </Stack>
        </Paper>
      )}

      {/* Streaming answer */}
      {(answer || isStreaming) && (
        <Paper sx={{ p: 2 }}>
          <Stack direction="row" alignItems="center" spacing={1} mb={1}>
            <PsychologyIcon color="success" fontSize="small" />
            <Typography variant="subtitle2" fontWeight={600}>Answer</Typography>
            {isStreaming && <CircularProgress size={14} />}
          </Stack>
          <Divider sx={{ mb: 1.5 }} />
          <Typography
            variant="body1"
            sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.7 }}
          >
            {answer}
            {isStreaming && <Box component="span" sx={{ display: 'inline-block', width: 2, height: '1em', bgcolor: 'text.primary', ml: 0.25, animation: 'blink 1s step-end infinite', '@keyframes blink': { '0%, 100%': { opacity: 1 }, '50%': { opacity: 0 } } }} />}
          </Typography>
        </Paper>
      )}

      <div ref={bottomRef} />
    </Box>
  );
};

export default AgentQuery;

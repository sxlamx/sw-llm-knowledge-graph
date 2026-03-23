import React, { useState } from 'react';
import {
  Drawer,
  Box,
  Typography,
  Stack,
  IconButton,
  Chip,
  List,
  ListItem,
  TextField,
  Button,
  Divider,
  CircularProgress,
  Collapse,
  Tooltip,
  Pagination,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import EditIcon from '@mui/icons-material/Edit';
import SaveIcon from '@mui/icons-material/Save';
import ImageIcon from '@mui/icons-material/Image';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import RefreshIcon from '@mui/icons-material/Refresh';
import { GraphNode, useGetGraphNodeQuery, useUpdateGraphNodeMutation, useGetNodeSummaryQuery } from '../../api/graphApi';
import { useAppDispatch } from '../../store';
import { showSnackbar } from '../../store/slices/uiSlice';

interface LinkedChunk {
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  text: string;
  page?: number;
  has_image?: boolean;
  image_b64?: string;
}

const CHUNK_PREVIEW_LEN = 120;

const LinkedChunkItem: React.FC<{ chunk: LinkedChunk }> = ({ chunk }) => {
  const [imgExpanded, setImgExpanded] = useState(false);
  const [textExpanded, setTextExpanded] = useState(false);

  const isLong = chunk.text.length > CHUNK_PREVIEW_LEN;

  return (
    <ListItem disablePadding sx={{ mb: 0.75, display: 'block' }}>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        {chunk.has_image && <ImageIcon fontSize="small" color="secondary" sx={{ fontSize: '0.85rem' }} />}
        <Typography variant="caption" fontWeight={600} sx={{ flex: 1 }}>
          {chunk.doc_title}{chunk.page != null ? ` · p.${chunk.page}` : ''}
        </Typography>
        {chunk.has_image && chunk.image_b64 && (
          <IconButton size="small" onClick={() => setImgExpanded((v) => !v)} sx={{ p: 0.25 }}>
            {imgExpanded ? <ExpandLessIcon sx={{ fontSize: '0.85rem' }} /> : <ExpandMoreIcon sx={{ fontSize: '0.85rem' }} />}
          </IconButton>
        )}
      </Stack>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
        {textExpanded ? chunk.text : chunk.text.slice(0, CHUNK_PREVIEW_LEN)}
        {!textExpanded && isLong && '…'}
      </Typography>
      {isLong && (
        <Typography
          variant="caption"
          color="primary"
          sx={{ cursor: 'pointer', fontSize: '0.6rem', '&:hover': { textDecoration: 'underline' } }}
          onClick={() => setTextExpanded((v) => !v)}
        >
          {textExpanded ? 'Show less' : 'Show more'}
        </Typography>
      )}
      {chunk.has_image && chunk.image_b64 && (
        <Collapse in={imgExpanded}>
          <Box
            component="img"
            src={`data:image/jpeg;base64,${chunk.image_b64}`}
            alt={`Page ${chunk.page ?? ''}`}
            sx={{
              mt: 0.5,
              maxWidth: '100%',
              maxHeight: 200,
              objectFit: 'contain',
              borderRadius: 1,
              border: '1px solid',
              borderColor: 'divider',
            }}
          />
        </Collapse>
      )}
    </ListItem>
  );
};

const ENTITY_TYPE_COLORS: Record<string, 'primary' | 'secondary' | 'success' | 'warning' | 'error' | 'info' | 'default'> = {
  Person: 'success',
  Organization: 'primary',
  Location: 'warning',
  Concept: 'secondary',
  Event: 'error',
  Document: 'default',
  Topic: 'info',
};

interface Props {
  node: GraphNode;
  collectionId: string;
  onClose: () => void;
}

const NodeDetailPanel: React.FC<Props> = ({ node, collectionId, onClose }) => {
  const dispatch = useAppDispatch();
  const [isEditing, setIsEditing] = useState(false);
  const [editLabel, setEditLabel] = useState(node.label);
  const [editDescription, setEditDescription] = useState(node.description ?? '');
  const [forceRegen, setForceRegen] = useState(false);
  const [chunkPage, setChunkPage] = useState(1);
  const CHUNKS_PER_PAGE = 10;

  const { data: nodeDetail, isLoading } = useGetGraphNodeQuery(
    { id: node.id, collection_id: collectionId, depth: 1 },
    { skip: !node.id }
  );
  React.useEffect(() => { setChunkPage(1); }, [node.id]);
  const {
    data: summaryData,
    isLoading: summaryLoading,
    isFetching: summaryFetching,
    refetch: refetchSummary,
  } = useGetNodeSummaryQuery(
    { node_id: node.id, collection_id: collectionId, force: forceRegen },
    { skip: !node.id }
  );
  const [updateNode, { isLoading: isSaving }] = useUpdateGraphNodeMutation();

  const handleSave = async () => {
    try {
      await updateNode({
        id: node.id,
        collection_id: collectionId,
        label: editLabel,
        description: editDescription,
      }).unwrap();
      setIsEditing(false);
      dispatch(showSnackbar({ message: 'Node updated.', severity: 'success' }));
    } catch {
      dispatch(showSnackbar({ message: 'Failed to update node.', severity: 'error' }));
    }
  };

  return (
    <Drawer anchor="right" open onClose={onClose} sx={{ zIndex: 1300 }}>
      <Box sx={{ p: 2, width: 380 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
          {isEditing ? (
            <TextField
              value={editLabel}
              onChange={(e) => setEditLabel(e.target.value)}
              size="small"
              variant="standard"
              sx={{ flex: 1 }}
            />
          ) : (
            <Typography variant="h6" sx={{ flex: 1 }}>{nodeDetail?.label ?? node.label}</Typography>
          )}
          <Stack direction="row">
            {isEditing ? (
              <IconButton size="small" onClick={handleSave} disabled={isSaving}>
                <SaveIcon fontSize="small" />
              </IconButton>
            ) : (
              <IconButton size="small" onClick={() => setIsEditing(true)}>
                <EditIcon fontSize="small" />
              </IconButton>
            )}
            <IconButton size="small" onClick={onClose}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Stack>
        </Stack>

        <Chip
          label={node.entity_type}
          color={ENTITY_TYPE_COLORS[node.entity_type] ?? 'default'}
          size="small"
          sx={{ mb: 1.5 }}
        />

        {isEditing ? (
          <TextField
            label="Description"
            value={editDescription}
            onChange={(e) => setEditDescription(e.target.value)}
            size="small"
            multiline
            rows={3}
            fullWidth
            sx={{ mb: 1 }}
          />
        ) : (
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {nodeDetail?.description ?? node.description ?? 'No description.'}
          </Typography>
        )}

        <Typography variant="caption" color="text.secondary">
          Confidence: {Math.round((nodeDetail?.confidence ?? node.confidence) * 100)}%
        </Typography>

        <Divider sx={{ my: 1.5 }} />

        {/* LLM-generated summary */}
        <Stack direction="row" alignItems="center" justifyContent="space-between" mb={0.5}>
          <Typography variant="subtitle2">Summary</Typography>
          <Tooltip title={summaryData?.from_cache ? 'Cached — click to regenerate' : 'Regenerate summary'}>
            <span>
            <IconButton
              size="small"
              disabled={summaryLoading || summaryFetching}
              onClick={() => {
                setForceRegen(true);
                setTimeout(() => {
                  refetchSummary();
                  setForceRegen(false);
                }, 50);
              }}
            >
              {(summaryLoading || summaryFetching)
                ? <CircularProgress size={14} />
                : <RefreshIcon sx={{ fontSize: '0.9rem' }} />}
            </IconButton>
            </span>
          </Tooltip>
        </Stack>

        {summaryLoading || summaryFetching ? (
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
            <CircularProgress size={14} />
            <Typography variant="caption" color="text.secondary">Generating summary…</Typography>
          </Stack>
        ) : summaryData ? (
          <Box sx={{ mb: 1, p: 1, bgcolor: 'action.hover', borderRadius: 1 }}>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
              {summaryData.summary}
            </Typography>
            {summaryData.updated_at && (
              <Typography variant="caption" color="text.disabled" display="block" mt={0.5}>
                {summaryData.from_cache ? 'Cached' : 'Generated'} ·{' '}
                {new Date(summaryData.updated_at / 1000).toLocaleString()}
              </Typography>
            )}
          </Box>
        ) : null}

        <Divider sx={{ my: 1.5 }} />

        <Stack direction="row" alignItems="center" justifyContent="space-between" mb={0.5}>
          <Typography variant="subtitle2">Source Chunks</Typography>
          {!!nodeDetail?.linked_chunks?.length && (
            <Typography variant="caption" color="text.secondary">
              {nodeDetail.linked_chunks.length} chunk{nodeDetail.linked_chunks.length !== 1 ? 's' : ''}
            </Typography>
          )}
        </Stack>

        {isLoading ? (
          <CircularProgress size={20} />
        ) : nodeDetail?.linked_chunks?.length ? (
          <>
            <List dense disablePadding>
              {nodeDetail.linked_chunks
                .slice((chunkPage - 1) * CHUNKS_PER_PAGE, chunkPage * CHUNKS_PER_PAGE)
                .map((chunk) => (
                  <LinkedChunkItem key={chunk.chunk_id} chunk={chunk} />
                ))}
            </List>
            {nodeDetail.linked_chunks.length > CHUNKS_PER_PAGE && (
              <Pagination
                count={Math.ceil(nodeDetail.linked_chunks.length / CHUNKS_PER_PAGE)}
                page={chunkPage}
                onChange={(_, p) => setChunkPage(p)}
                size="small"
                sx={{ mt: 1 }}
              />
            )}
          </>
        ) : (
          <Typography variant="caption" color="text.secondary">
            No linked source chunks.
          </Typography>
        )}

        {isEditing && (
          <Button
            variant="outlined"
            size="small"
            onClick={() => setIsEditing(false)}
            sx={{ mt: 1 }}
          >
            Cancel
          </Button>
        )}
      </Box>
    </Drawer>
  );
};

export default NodeDetailPanel;

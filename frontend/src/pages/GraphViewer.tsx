import React, { useState, useCallback, useEffect } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Box,
  Paper,
  Typography,
  CircularProgress,
  Alert,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  AppBar,
  Toolbar,
  IconButton,
  Divider,
  List,
  ListItem,
  ListItemText,
  Chip,
  Stack,
  Tooltip,
  Avatar,
  AvatarGroup,
  InputAdornment,
  TextField,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import BarChartIcon from '@mui/icons-material/BarChart';
import BubbleChartIcon from '@mui/icons-material/BubbleChart';
import LabelIcon from '@mui/icons-material/Label';
import { useGetGraphDataQuery, useGetNerKeywordsQuery, GraphNode } from '../api/graphApi';
import { useListCollectionsQuery } from '../api/collectionsApi';
import { useListDocumentsQuery } from '../api/documentsApi';
import { useGetPageRankQuery, useGetAnalyticsSummaryQuery, useGetClusterTopicsQuery } from '../api/analyticsApi';
import { useAppDispatch, useAppSelector } from '../store';
import {
  setSelectedNode,
  setPathFinderMode,
  setPathEndpoint,
  setDepth,
  setEdgeTypeFilters,
  setEntityTypeFilters,
  setNerLabelFilters,
  toggleClustering,
  toggleClusterLabels,
  setSelectedCluster,
  type PresenceEntry,
} from '../store/slices/graphSlice';
import { useCollabRoom } from '../hooks/useCollabRoom';
import ForceGraph from '../components/graph/ForceGraph';
import NodeDetailPanel from '../components/graph/NodeDetailPanel';
import GraphControls from '../components/graph/GraphControls';
import PathFinder from '../components/graph/PathFinder';

// ---------------------------------------------------------------------------
// Analytics overlay panel
// ---------------------------------------------------------------------------

interface AnalyticsPanelProps {
  collectionId: string;
}

const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({ collectionId }) => {
  const { data: summary, isLoading } = useGetAnalyticsSummaryQuery({ collection_id: collectionId });

  if (isLoading) {
    return (
      <Paper elevation={3} sx={{ p: 2, minWidth: 260 }}>
        <CircularProgress size={20} />
      </Paper>
    );
  }

  if (!summary) return null;

  return (
    <Paper elevation={3} sx={{ p: 2, minWidth: 260, maxHeight: 420, overflowY: 'auto' }}>
      <Typography variant="subtitle2" gutterBottom fontWeight={700}>
        Graph Analytics
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {summary.node_count} nodes · {summary.edge_count} edges · {summary.num_communities} communities
      </Typography>

      <Divider sx={{ my: 1 }} />

      <Typography variant="caption" fontWeight={600} color="primary">
        Top by PageRank
      </Typography>
      <List dense disablePadding>
        {summary.top_pagerank.map((item, i) => (
          <ListItem key={item.id} disablePadding sx={{ py: 0.25 }}>
            <ListItemText
              primary={
                <Stack direction="row" alignItems="center" justifyContent="space-between">
                  <Typography variant="caption" noWrap sx={{ maxWidth: 160 }}>
                    {i + 1}. {item.label}
                  </Typography>
                  <Chip
                    label={item.score.toFixed(3)}
                    size="small"
                    color="primary"
                    variant="outlined"
                    sx={{ height: 18, fontSize: '0.6rem' }}
                  />
                </Stack>
              }
            />
          </ListItem>
        ))}
      </List>

      <Divider sx={{ my: 1 }} />

      <Typography variant="caption" fontWeight={600} color="secondary">
        Top by Betweenness
      </Typography>
      <List dense disablePadding>
        {summary.top_betweenness.map((item, i) => (
          <ListItem key={item.id} disablePadding sx={{ py: 0.25 }}>
            <ListItemText
              primary={
                <Stack direction="row" alignItems="center" justifyContent="space-between">
                  <Typography variant="caption" noWrap sx={{ maxWidth: 160 }}>
                    {i + 1}. {item.label}
                  </Typography>
                  <Chip
                    label={item.score.toFixed(3)}
                    size="small"
                    color="secondary"
                    variant="outlined"
                    sx={{ height: 18, fontSize: '0.6rem' }}
                  />
                </Stack>
              }
            />
          </ListItem>
        ))}
      </List>

      <Divider sx={{ my: 1 }} />
      <Typography variant="caption" color="text.secondary">
        Node size = PageRank score
      </Typography>
    </Paper>
  );
};

// ---------------------------------------------------------------------------
// NER keyword frequency panel
// ---------------------------------------------------------------------------

const NER_LABEL_COLORS: Record<string, string> = {
  PERSON: '#4CAF50', ORGANIZATION: '#2196F3', LOCATION: '#FF9800',
  DATE: '#9E9E9E', MONEY: '#795548', PERCENT: '#607D8B', LAW: '#E91E63',
  LEGISLATION_TITLE: '#9C27B0', LEGISLATION_REFERENCE: '#673AB7',
  STATUTE_SECTION: '#3F51B5', COURT_CASE: '#F44336', CASE_CITATION: '#B71C1C',
  JURISDICTION: '#FF5722', LEGAL_CONCEPT: '#009688', DEFINED_TERM: '#00BCD4',
  COURT: '#C62828', JUDGE: '#6A1B9A', LAWYER: '#1565C0',
  PETITIONER: '#2E7D32', RESPONDENT: '#E65100', WITNESS: '#4E342E',
};

interface NerKeywordPanelProps {
  collectionId: string;
  activeLabels: string[];
  selectedKeywords: string[];
  onKeywordToggle: (text: string) => void;
  onKeywordsClear: () => void;
}

const TOP_N_OPTIONS = [10, 20, 30, 50, 100];

const NerKeywordPanel: React.FC<NerKeywordPanelProps> = ({
  collectionId, activeLabels, selectedKeywords, onKeywordToggle, onKeywordsClear,
}) => {
  const [topN, setTopN] = React.useState(30);
  const [search, setSearch] = React.useState('');
  const { data, isFetching } = useGetNerKeywordsQuery(
    { collection_id: collectionId, labels: activeLabels, top_n: topN },
    { skip: !collectionId || activeLabels.length === 0 }
  );

  if (activeLabels.length === 0) return null;

  return (
    <Paper
      elevation={3}
      sx={{
        position: 'absolute',
        top: 80,
        right: 16,
        zIndex: 10,
        p: 2,
        width: 260,
        maxHeight: 'calc(100vh - 100px)',
        overflowY: 'auto',
        borderRadius: 2,
      }}
    >
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={0.5}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography variant="subtitle2" fontWeight={600}>NER Keywords</Typography>
          {selectedKeywords.length > 0 && (
            <Chip
              label={`${selectedKeywords.length} active`}
              size="small"
              color="primary"
              onDelete={onKeywordsClear}
              sx={{ height: 18, fontSize: '0.6rem' }}
            />
          )}
        </Stack>
        <FormControl size="small" sx={{ minWidth: 72 }}>
          <Select
            value={topN}
            onChange={(e) => setTopN(Number(e.target.value))}
            variant="standard"
            sx={{ fontSize: '0.7rem' }}
          >
            {TOP_N_OPTIONS.map(n => (
              <MenuItem key={n} value={n} sx={{ fontSize: '0.75rem' }}>Top {n}</MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>
      <TextField
        size="small"
        placeholder="Filter keywords…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        fullWidth
        sx={{ mb: 1.5, '& .MuiInputBase-input': { fontSize: '0.75rem', py: 0.5 } }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <SearchIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
            </InputAdornment>
          ),
        }}
      />
      {isFetching ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
          <CircularProgress size={20} />
        </Box>
      ) : !data || Object.keys(data).length === 0 ? (
        <Typography variant="caption" color="text.secondary">No keywords found for selected labels.</Typography>
      ) : (
        activeLabels
          .filter(lbl => data[lbl] && data[lbl].length > 0)
          .map(lbl => {
            const term = search.trim().toLowerCase();
            const entries = term
              ? data[lbl].filter(({ text }) => text.toLowerCase().includes(term))
              : data[lbl];
            if (entries.length === 0) return null;
            const color = NER_LABEL_COLORS[lbl] ?? '#999';
            return (
              <Box key={lbl} mb={1.5}>
                <Typography
                  variant="caption"
                  fontWeight={700}
                  sx={{ color, display: 'block', mb: 0.5 }}
                >
                  {lbl}
                </Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.4 }}>
                  {entries.map(({ text, count }) => {
                    const active = selectedKeywords.includes(text);
                    return (
                      <Tooltip key={text} title={active ? 'Click to remove filter' : `Filter graph to "${text}" — ${count} occurrence${count !== 1 ? 's' : ''}`}>
                        <Chip
                          label={`${text} (${count})`}
                          size="small"
                          clickable
                          onClick={() => onKeywordToggle(text)}
                          sx={{
                            height: 18,
                            fontSize: '0.6rem',
                            borderColor: color,
                            color: active ? '#fff' : color,
                            bgcolor: active ? color : 'transparent',
                            fontWeight: active ? 700 : 400,
                            '&:hover': { bgcolor: active ? color : color + '33' },
                          }}
                          variant="outlined"
                        />
                      </Tooltip>
                    );
                  })}
                </Box>
              </Box>
            );
          })
      )}
    </Paper>
  );
};

// ---------------------------------------------------------------------------
// GraphViewer
// ---------------------------------------------------------------------------

const GraphViewer: React.FC = () => {
  const { collectionId: paramCollectionId } = useParams<{ collectionId?: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const dispatch = useAppDispatch();

  const [selectedCollectionId, setSelectedCollectionId] = useState(paramCollectionId ?? '');
  const [selectedDocId, setSelectedDocId] = useState(searchParams.get('doc_id') ?? '');
  const prevCollectionIdRef = React.useRef(paramCollectionId ?? '');
  const [highlightedNodeIds, setHighlightedNodeIds] = useState<string[]>([]);
  const [detailNode, setDetailNode] = useState<GraphNode | null>(null);
  const [analyticsOpen, setAnalyticsOpen] = useState(false);
  const [nerKeywordFilters, setNerKeywordFilters] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const tooltipRef = React.useRef<HTMLDivElement>(null);

  const {
    selectedNodeId, pathFinderMode, depth, edgeTypeFilters, entityTypeFilters, nerLabelFilters,
    presence, clusteringEnabled, showClusterLabels, selectedClusterId,
  } = useAppSelector((s) => s.graph);

  // Collab room — presence indicators + real-time graph sync
  const { sendPresence } = useCollabRoom(selectedCollectionId || undefined);

  // Build node_id → list of viewers for presence overlay (memoised to avoid ForceGraph re-renders)
  const nodeViewers = React.useMemo(
    () => (Object.values(presence) as PresenceEntry[]).reduce<Record<string, string[]>>(
      (acc, p) => { (acc[p.node_id] ??= []).push(p.name); return acc; },
      {}
    ),
    [presence]
  );

  // Update tooltip position via direct DOM mutation — never calls setState on mousemove,
  // which was causing "Maximum update depth exceeded" by triggering React re-renders
  // on every mouse event during the commit phase.
  React.useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (tooltipRef.current) {
        tooltipRef.current.style.left = `${e.clientX + 14}px`;
        tooltipRef.current.style.top = `${e.clientY + 14}px`;
      }
    };
    document.addEventListener('mousemove', onMove);
    return () => document.removeEventListener('mousemove', onMove);
  }, []);
  const { data: collectionsData } = useListCollectionsQuery();
  const { data: docsData } = useListDocumentsQuery(
    { collection_id: selectedCollectionId },
    { skip: !selectedCollectionId }
  );

  const { data: graphData, isLoading, isError } = useGetGraphDataQuery(
    {
      collection_id: selectedCollectionId,
      depth,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      doc_id: selectedDocId || undefined,
      entity_type_filters: entityTypeFilters.length > 0 ? entityTypeFilters : undefined,
      ner_label_filters: nerLabelFilters.length > 0 ? nerLabelFilters : undefined,
      ner_keyword_filters: nerKeywordFilters.length > 0 ? nerKeywordFilters : undefined,
    },
    { skip: !selectedCollectionId }
  );

  const { data: pageRankData } = useGetPageRankQuery(
    { collection_id: selectedCollectionId },
    { skip: !selectedCollectionId || !analyticsOpen }
  );

  const { data: clusterData, isFetching: clusterFetching } = useGetClusterTopicsQuery(
    { collection_id: selectedCollectionId },
    { skip: !selectedCollectionId || !clusteringEnabled }
  );

  // Build node_id → score map for overlay colouring
  const analyticsScores: Record<string, number> | undefined = analyticsOpen && pageRankData
    ? Object.fromEntries(pageRankData.scores.map((s) => [s.node_id, s.score]))
    : undefined;

  // Build cluster colour + label maps
  const clusterColors = React.useMemo<Record<string, string> | undefined>(() => {
    if (!clusteringEnabled || !clusterData) return undefined;
    const map: Record<string, string> = {};
    for (const c of clusterData.clusters) {
      for (const nid of c.node_ids) map[nid] = c.color;
    }
    return map;
  }, [clusteringEnabled, clusterData]);

  // Only the first node of each cluster gets the label rendered on canvas
  const nodeClusterLabels = React.useMemo<Record<string, string> | undefined>(() => {
    if (!clusteringEnabled || !showClusterLabels || !clusterData) return undefined;
    const map: Record<string, string> = {};
    for (const c of clusterData.clusters) {
      if (c.node_ids.length > 0) map[c.node_ids[0]] = c.topic;
    }
    return map;
  }, [clusteringEnabled, showClusterLabels, clusterData]);

  // Highlighted node IDs when a cluster is selected in the legend
  const clusterHighlightedIds = React.useMemo<string[]>(() => {
    if (selectedClusterId === null || !clusterData) return [];
    return clusterData.clusters.find((c) => c.id === selectedClusterId)?.node_ids ?? [];
  }, [selectedClusterId, clusterData]);

  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      if (pathFinderMode) {
        dispatch(setPathEndpoint(node.id));
      } else {
        dispatch(setSelectedNode(node.id));
        setDetailNode(node);
        sendPresence(node.id);
      }
    },
    [pathFinderMode, dispatch, sendPresence]
  );

  useEffect(() => {
    if (paramCollectionId && paramCollectionId !== prevCollectionIdRef.current) {
      prevCollectionIdRef.current = paramCollectionId;
      setSelectedCollectionId(paramCollectionId);
      setSelectedDocId(''); // reset doc filter only when collection actually changes
    } else if (paramCollectionId) {
      setSelectedCollectionId(paramCollectionId);
    }
  }, [paramCollectionId]);

  // Clear keyword filters when NER label selection changes (keywords belong to specific labels)
  useEffect(() => {
    setNerKeywordFilters([]);
  }, [nerLabelFilters]);


  const warningThreshold = (graphData?.total_nodes ?? 0) > 5000;

  return (
    <Box sx={{ position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden', bgcolor: 'background.default' }}>
      {/* Top bar */}
      <AppBar position="absolute" color="default" elevation={1} sx={{ zIndex: 20 }}>
        <Toolbar variant="dense">
          <IconButton edge="start" size="small" onClick={() => navigate(-1)} sx={{ mr: 1 }}>
            <ArrowBackIcon />
          </IconButton>
          <Typography variant="subtitle1" fontWeight={600} sx={{ mr: 2 }}>
            Graph Viewer
          </Typography>
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel>Collection</InputLabel>
            <Select
              label="Collection"
              value={collectionsData?.collections.some(c => c.id === selectedCollectionId) ? selectedCollectionId : ''}
              onChange={(e) => setSelectedCollectionId(e.target.value)}
            >
              <MenuItem value="">Select collection…</MenuItem>
              {collectionsData?.collections.map((c) => (
                <MenuItem key={c.id} value={c.id}>
                  {c.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {selectedCollectionId && docsData && docsData.documents.length > 0 && (
            <FormControl size="small" sx={{ minWidth: 200, ml: 1 }}>
              <InputLabel>Document</InputLabel>
              <Select
                label="Document"
                value={selectedDocId}
                onChange={(e) => setSelectedDocId(e.target.value)}
              >
                <MenuItem value="">All documents</MenuItem>
                {docsData.documents.map((d) => (
                  <MenuItem key={d.id} value={d.id}>
                    {d.title}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}

          {graphData && (
            <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
              {graphData.total_nodes} nodes · {graphData.total_edges} edges
            </Typography>
          )}

          {/* Timeline date range filter */}
          {selectedCollectionId && (
            <Stack direction="row" alignItems="center" spacing={0.5} sx={{ ml: 2 }}>
              <Typography variant="caption" color="text.secondary">From</Typography>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                style={{ fontSize: '0.75rem', padding: '2px 4px', borderRadius: 4, border: '1px solid #ccc' }}
              />
              <Typography variant="caption" color="text.secondary">To</Typography>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                style={{ fontSize: '0.75rem', padding: '2px 4px', borderRadius: 4, border: '1px solid #ccc' }}
              />
              {(dateFrom || dateTo) && (
                <Chip
                  label="Clear"
                  size="small"
                  onDelete={() => { setDateFrom(''); setDateTo(''); }}
                  sx={{ height: 20, fontSize: '0.65rem' }}
                />
              )}
            </Stack>
          )}

          {/* Collab presence — other users in this room */}
          {Object.keys(presence).length > 0 && (
            <Tooltip title={(Object.values(presence) as PresenceEntry[]).map((p) => p.name).join(', ')}>
              <AvatarGroup max={4} sx={{ ml: 1 }}>
                {(Object.values(presence) as PresenceEntry[]).map((p) => (
                  <Avatar key={p.user_id} sx={{ width: 24, height: 24, fontSize: '0.65rem' }}>
                    {p.name[0]?.toUpperCase() ?? '?'}
                  </Avatar>
                ))}
              </AvatarGroup>
            </Tooltip>
          )}

          {selectedCollectionId && (
            <Stack direction="row" alignItems="center" spacing={0.5} sx={{ ml: 'auto' }}>
              <Tooltip title={analyticsOpen ? 'Hide analytics overlay' : 'Show analytics overlay'}>
                <IconButton
                  size="small"
                  onClick={() => setAnalyticsOpen((v) => !v)}
                  color={analyticsOpen ? 'primary' : 'default'}
                >
                  <BarChartIcon />
                </IconButton>
              </Tooltip>
              <Tooltip title={clusteringEnabled ? 'Disable clustering' : 'Enable community clustering'}>
                <IconButton
                  size="small"
                  onClick={() => dispatch(toggleClustering())}
                  color={clusteringEnabled ? 'secondary' : 'default'}
                >
                  {clusterFetching
                    ? <CircularProgress size={16} />
                    : <BubbleChartIcon />}
                </IconButton>
              </Tooltip>
              {clusteringEnabled && (
                <Tooltip title={showClusterLabels ? 'Hide cluster labels' : 'Show cluster labels'}>
                  <IconButton
                    size="small"
                    onClick={() => dispatch(toggleClusterLabels())}
                    color={showClusterLabels ? 'secondary' : 'default'}
                  >
                    <LabelIcon />
                  </IconButton>
                </Tooltip>
              )}
            </Stack>
          )}
        </Toolbar>
      </AppBar>

      {/* Graph controls overlay */}
      {selectedCollectionId && (
        <GraphControls
          depth={depth}
          onDepthChange={(d) => dispatch(setDepth(d))}
          pathFinderMode={pathFinderMode}
          onPathFinderToggle={() => dispatch(setPathFinderMode(!pathFinderMode))}
          activeEdgeTypes={edgeTypeFilters}
          onEdgeTypeFiltersChange={(f) => dispatch(setEdgeTypeFilters(f))}
          entityTypeFilters={entityTypeFilters}
          onEntityTypeFiltersChange={(f) => dispatch(setEntityTypeFilters(f))}
          nerLabelFilters={nerLabelFilters}
          onNerLabelFiltersChange={(f) => dispatch(setNerLabelFilters(f))}
        />
      )}

      {/* Document title banner */}
      {selectedDocId && docsData && (
        <Box sx={{ position: 'absolute', top: 56, left: '50%', transform: 'translateX(-50%)', zIndex: 15 }}>
          <Chip
            label={`Document: ${docsData.documents.find((d) => d.id === selectedDocId)?.title ?? selectedDocId}`}
            onDelete={() => setSelectedDocId('')}
            color="primary"
            variant="outlined"
            size="small"
          />
        </Box>
      )}

      {/* NER keyword frequency panel — shown when NER label filters are active */}
      {nerLabelFilters.length > 0 && selectedCollectionId && (
        <NerKeywordPanel
          collectionId={selectedCollectionId}
          activeLabels={nerLabelFilters}
          selectedKeywords={nerKeywordFilters}
          onKeywordToggle={(text) =>
            setNerKeywordFilters(prev =>
              prev.includes(text) ? prev.filter(k => k !== text) : [...prev, text]
            )
          }
          onKeywordsClear={() => setNerKeywordFilters([])}
        />
      )}

      {/* Analytics overlay panel */}
      {analyticsOpen && selectedCollectionId && (
        <Box
          sx={{
            position: 'absolute',
            bottom: 16,
            left: 16,
            zIndex: 15,
          }}
        >
          <AnalyticsPanel collectionId={selectedCollectionId} />
        </Box>
      )}

      {/* Cluster legend panel */}
      {clusteringEnabled && clusterData && clusterData.clusters.length > 0 && (
        <Box
          sx={{
            position: 'absolute',
            bottom: 16,
            right: 16,
            zIndex: 15,
            maxHeight: 400,
            overflowY: 'auto',
          }}
        >
          <Paper elevation={3} sx={{ p: 1.5, minWidth: 220 }}>
            <Typography variant="subtitle2" fontWeight={700} gutterBottom>
              Clusters ({clusterData.total_clusters})
            </Typography>
            <List dense disablePadding>
              {clusterData.clusters.map((c) => (
                <ListItem
                  key={c.id}
                  disablePadding
                  sx={{
                    py: 0.25,
                    cursor: 'pointer',
                    borderRadius: 1,
                    bgcolor: selectedClusterId === c.id ? 'action.selected' : 'transparent',
                    '&:hover': { bgcolor: 'action.hover' },
                  }}
                  onClick={() => dispatch(setSelectedCluster(selectedClusterId === c.id ? null : c.id))}
                >
                  <ListItemText
                    primary={
                      <Stack direction="row" alignItems="center" spacing={0.75}>
                        <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: c.color, flexShrink: 0 }} />
                        <Typography variant="caption" noWrap sx={{ maxWidth: 160 }}>
                          {c.topic}
                        </Typography>
                        <Chip label={c.size} size="small" sx={{ height: 16, fontSize: '0.6rem', ml: 'auto' }} />
                      </Stack>
                    }
                  />
                </ListItem>
              ))}
            </List>
          </Paper>
        </Box>
      )}

      {/* Main graph area */}
      <Box sx={{ pt: '48px', height: '100%' }}>
        {!selectedCollectionId ? (
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <Paper sx={{ p: 4, textAlign: 'center' }}>
              <Typography color="text.secondary">Select a collection to view its graph.</Typography>
            </Paper>
          </Box>
        ) : isLoading ? (
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <CircularProgress />
          </Box>
        ) : isError ? (
          <Box sx={{ p: 4 }}>
            <Alert severity="error">Failed to load graph data.</Alert>
          </Box>
        ) : (
          <>
            {warningThreshold && (
              <Alert severity="warning" sx={{ position: 'absolute', top: 56, left: '50%', transform: 'translateX(-50%)', zIndex: 15, width: 'auto' }}>
                Large graph: showing first 5000 nodes / 7000 edges.
              </Alert>
            )}
            <ForceGraph
              graphData={graphData}
              highlightedNodeIds={clusterHighlightedIds.length > 0 ? clusterHighlightedIds : highlightedNodeIds}
              onNodeClick={handleNodeClick}
              onNodeHover={setHoveredNode}
              analyticsScores={analyticsScores}
              nodeViewers={nodeViewers}
              clusterColors={clusterColors}
              nodeClusterLabels={nodeClusterLabels}
            />
          </>
        )}
      </Box>

      {/* Node detail panel */}
      {detailNode && selectedNodeId && (
        <NodeDetailPanel
          node={detailNode}
          collectionId={selectedCollectionId}
          onClose={() => {
            setDetailNode(null);
            dispatch(setSelectedNode(null));
          }}
        />
      )}

      {/* Hover tooltip — always mounted so tooltipRef stays attached; visibility toggled via display */}
      <Paper
        ref={tooltipRef}
        elevation={4}
        sx={{
          position: 'fixed',
          display: hoveredNode && !detailNode ? 'block' : 'none',
          p: 1.5,
          zIndex: 1300,
          maxWidth: 280,
          pointerEvents: 'none',
        }}
      >
        {hoveredNode && (
          <>
            <Typography variant="subtitle2" fontWeight={700}>{hoveredNode.label}</Typography>
            <Chip
              label={hoveredNode.entity_type}
              size="small"
              sx={{ my: 0.5, height: 18, fontSize: '0.65rem' }}
            />
            {hoveredNode.description && (
              <Typography variant="caption" display="block" color="text.secondary">
                {hoveredNode.description.slice(0, 200)}
              </Typography>
            )}
            <Typography variant="caption" color="text.disabled">
              Confidence: {Math.round(hoveredNode.confidence * 100)}%
            </Typography>
          </>
        )}
      </Paper>

      {/* Path finder overlay */}
      {pathFinderMode && selectedCollectionId && (
        <PathFinder
          collectionId={selectedCollectionId}
          onPathHighlight={setHighlightedNodeIds}
        />
      )}
    </Box>
  );
};

export default GraphViewer;

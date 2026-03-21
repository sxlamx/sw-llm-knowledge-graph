import React, { useState, useCallback, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Paper,
  Typography,
  CircularProgress,
  Button,
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
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import BarChartIcon from '@mui/icons-material/BarChart';
import { useGetGraphDataQuery, GraphNode } from '../api/graphApi';
import { useListCollectionsQuery } from '../api/collectionsApi';
import { useGetPageRankQuery, useGetAnalyticsSummaryQuery } from '../api/analyticsApi';
import { useAppDispatch, useAppSelector } from '../store';
import {
  setSelectedNode,
  setPathFinderMode,
  setPathEndpoint,
  setDepth,
  setEdgeTypeFilters,
} from '../store/slices/graphSlice';
import ForceGraph from '../components/graph/ForceGraph';
import NodeDetailPanel from '../components/graph/NodeDetailPanel';
import GraphControls from '../components/graph/GraphControls';
import PathFinder from '../components/graph/PathFinder';

// ---------------------------------------------------------------------------
// Analytics overlay panel
// ---------------------------------------------------------------------------

interface AnalyticsPanelProps {
  collectionId: string;
  pageRankScores: Record<string, number>;
}

const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({ collectionId, pageRankScores }) => {
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
// GraphViewer
// ---------------------------------------------------------------------------

const GraphViewer: React.FC = () => {
  const { collectionId: paramCollectionId } = useParams<{ collectionId?: string }>();
  const navigate = useNavigate();
  const dispatch = useAppDispatch();

  const [selectedCollectionId, setSelectedCollectionId] = useState(paramCollectionId ?? '');
  const [highlightedNodeIds, setHighlightedNodeIds] = useState<string[]>([]);
  const [detailNode, setDetailNode] = useState<GraphNode | null>(null);
  const [analyticsOpen, setAnalyticsOpen] = useState(false);

  const { selectedNodeId, pathFinderMode, depth, edgeTypeFilters } = useAppSelector(
    (s) => s.graph
  );
  const { data: collectionsData } = useListCollectionsQuery();

  const { data: graphData, isLoading, isError } = useGetGraphDataQuery(
    { collection_id: selectedCollectionId, depth },
    { skip: !selectedCollectionId }
  );

  const { data: pageRankData } = useGetPageRankQuery(
    { collection_id: selectedCollectionId },
    { skip: !selectedCollectionId || !analyticsOpen }
  );

  // Build node_id → score map for overlay colouring
  const analyticsScores: Record<string, number> | undefined = analyticsOpen && pageRankData
    ? Object.fromEntries(pageRankData.scores.map((s) => [s.node_id, s.score]))
    : undefined;

  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      if (pathFinderMode) {
        dispatch(setPathEndpoint(node.id));
      } else {
        dispatch(setSelectedNode(node.id));
        setDetailNode(node);
      }
    },
    [pathFinderMode, dispatch]
  );

  useEffect(() => {
    if (paramCollectionId) setSelectedCollectionId(paramCollectionId);
  }, [paramCollectionId]);

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
              value={selectedCollectionId}
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

          {graphData && (
            <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
              {graphData.total_nodes} nodes · {graphData.total_edges} edges
            </Typography>
          )}

          {selectedCollectionId && (
            <Tooltip title={analyticsOpen ? 'Hide analytics overlay' : 'Show analytics overlay'}>
              <IconButton
                size="small"
                onClick={() => setAnalyticsOpen((v) => !v)}
                color={analyticsOpen ? 'primary' : 'default'}
                sx={{ ml: 'auto' }}
              >
                <BarChartIcon />
              </IconButton>
            </Tooltip>
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
          onEdgeTypeToggle={(t) => {
            const next = edgeTypeFilters.includes(t)
              ? edgeTypeFilters.filter((x) => x !== t)
              : [...edgeTypeFilters, t];
            dispatch(setEdgeTypeFilters(next));
          }}
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
          <AnalyticsPanel
            collectionId={selectedCollectionId}
            pageRankScores={analyticsScores ?? {}}
          />
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
              highlightedNodeIds={highlightedNodeIds}
              onNodeClick={handleNodeClick}
              analyticsScores={analyticsScores}
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

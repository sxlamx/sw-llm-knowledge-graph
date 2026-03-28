import React, { useCallback, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { GraphData, GraphNode, GraphEdge } from '../../api/graphApi';

export const ENTITY_TYPE_COLORS: Record<string, string> = {
  // Canonical labels produced by ner_tagger.py (SPACY_TO_CANONICAL mapping)
  PERSON: '#4CAF50',
  ORGANIZATION: '#2196F3',
  LOCATION: '#FF9800',
  LAW: '#607D8B',
  DATE: '#78909C',
  MONEY: '#8BC34A',
  PERCENT: '#B0BEC5',
  // LLM extractor labels (TitleCase) — kept for forward-compat
  Person: '#4CAF50',
  Organization: '#2196F3',
  Location: '#FF9800',
  Concept: '#9C27B0',
  Event: '#F44336',
  Document: '#607D8B',
  Topic: '#00BCD4',
};

interface ForceGraphNode extends GraphNode {
  x?: number;
  y?: number;
}

interface ForceGraphLink extends GraphEdge {
  source: string;
  target: string;
}

interface Props {
  graphData: GraphData | undefined;
  highlightedNodeIds?: string[];
  onNodeClick: (node: GraphNode) => void;
  onNodeHover?: (node: GraphNode | null) => void;
  /** node_id → normalised score [0, 1]; scales node radius when provided */
  analyticsScores?: Record<string, number>;
  /** node_id → list of viewer names currently viewing that node */
  nodeViewers?: Record<string, string[]>;
  /** node_id → hex color (cluster coloring mode) */
  clusterColors?: Record<string, string>;
  /** node_id → cluster topic label (only representative nodes have an entry) */
  nodeClusterLabels?: Record<string, string>;
  /** Draw node labels directly on the canvas */
  showLabels?: boolean;
  width?: number;
  height?: number;
}

const MAX_NODES = 5000;
const MAX_EDGES = 7000;

const ForceGraph: React.FC<Props> = ({
  graphData,
  highlightedNodeIds = [],
  onNodeClick,
  onNodeHover,
  analyticsScores,
  nodeViewers,
  clusterColors,
  nodeClusterLabels,
  showLabels = false,
  width,
  height,
}) => {
  const fgRef = useRef<ReturnType<typeof ForceGraph2D> | null>(null);

  // Spread each object so react-force-graph-2d can mutate them (Redux freezes state objects)
  const nodes: ForceGraphNode[] = (graphData?.nodes ?? []).slice(0, MAX_NODES).map((n) => ({ ...n }));
  const nodeIds = new Set(nodes.map((n) => n.id));
  const links: ForceGraphLink[] = (graphData?.edges ?? [])
    .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
    .slice(0, MAX_EDGES)
    .map((e) => ({ ...e }));

  const getNodeColor = useCallback(
    (node: object) => {
      const n = node as ForceGraphNode;
      if (highlightedNodeIds.length > 0) {
        return highlightedNodeIds.includes(n.id)
          ? (clusterColors?.[n.id] ?? ENTITY_TYPE_COLORS[n.entity_type] ?? '#888')
          : '#ccc';
      }
      if (clusterColors?.[n.id]) return clusterColors[n.id];
      return ENTITY_TYPE_COLORS[n.entity_type] ?? '#888';
    },
    [highlightedNodeIds, clusterColors]
  );

  const getLinkColor = useCallback(
    (link: object) => {
      const l = link as ForceGraphLink;
      if (highlightedNodeIds.length > 0) {
        const src = typeof l.source === 'object' ? (l.source as ForceGraphNode).id : l.source;
        const tgt = typeof l.target === 'object' ? (l.target as ForceGraphNode).id : l.target;
        return highlightedNodeIds.includes(src) && highlightedNodeIds.includes(tgt)
          ? '#f44336'
          : '#e0e0e0';
      }
      return '#b0bec5';
    },
    [highlightedNodeIds]
  );

  const getNodeVal = useCallback(
    (node: object) => {
      if (!analyticsScores) return 1;
      const n = node as ForceGraphNode;
      const score = analyticsScores[n.id] ?? 0;
      // Scale: base 1 + up to 8x for top-scoring nodes
      return 1 + score * 8;
    },
    [analyticsScores]
  );

  // Draw presence ring, cluster topic label, and/or node name label
  const nodeCanvasObject = useCallback(
    (node: object, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const n = node as ForceGraphNode;
      const viewers = nodeViewers?.[n.id];
      const clusterLabel = nodeClusterLabels?.[n.id];

      if (!viewers?.length && !clusterLabel && !showLabels) return;

      const r = (5 * (analyticsScores ? 1 + (analyticsScores[n.id] ?? 0) * 8 : 1)) / Math.sqrt(globalScale);
      const x = n.x ?? 0;
      const y = n.y ?? 0;

      ctx.save();

      // Presence ring (other viewers)
      if (viewers?.length) {
        ctx.beginPath();
        ctx.arc(x, y, r + 3, 0, 2 * Math.PI);
        ctx.strokeStyle = '#FF9800';
        ctx.lineWidth = 2;
        ctx.stroke();

        const badge = viewers.length === 1 ? viewers[0][0] : `${viewers.length}`;
        const fontSize = Math.max(4, 8 / globalScale);
        ctx.font = `bold ${fontSize}px Sans-Serif`;
        ctx.fillStyle = '#FF9800';
        ctx.textAlign = 'center';
        ctx.fillText(badge, x, y - r - 4);
      }

      // Cluster topic label
      if (clusterLabel) {
        const fontSize = Math.max(6, 10 / globalScale);
        ctx.font = `bold ${fontSize}px Sans-Serif`;
        ctx.fillStyle = clusterColors?.[n.id] ?? '#444';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        const textWidth = ctx.measureText(clusterLabel).width;
        const pad = 2 / globalScale;
        ctx.fillStyle = 'rgba(255,255,255,0.75)';
        ctx.fillRect(x - textWidth / 2 - pad, y + r + 2, textWidth + pad * 2, fontSize + pad * 2);
        ctx.fillStyle = clusterColors?.[n.id] ?? '#333';
        ctx.fillText(clusterLabel, x, y + r + 2 + pad);
      }

      // Node name label
      if (showLabels) {
        const label = n.label ?? '';
        const fontSize = Math.max(4, 10 / globalScale);
        ctx.font = `${fontSize}px Sans-Serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        const textWidth = ctx.measureText(label).width;
        const pad = 1.5 / globalScale;
        const yBase = y + r + 2;
        ctx.fillStyle = 'rgba(255,255,255,0.80)';
        ctx.fillRect(x - textWidth / 2 - pad, yBase, textWidth + pad * 2, fontSize + pad * 2);
        ctx.fillStyle = '#222';
        ctx.fillText(label, x, yBase + pad);
      }

      ctx.restore();
    },
    [nodeViewers, analyticsScores, nodeClusterLabels, clusterColors, showLabels]
  );

  return (
    <ForceGraph2D
      ref={fgRef as React.MutableRefObject<never>}
      graphData={{ nodes, links }}
      nodeId="id"
      nodeLabel={(n: object) => (n as ForceGraphNode).label}
      nodeColor={getNodeColor}
      nodeVal={analyticsScores ? getNodeVal : undefined}
      linkColor={getLinkColor}
      linkWidth={(l: object) => Math.max((l as ForceGraphLink).weight * 2, 0.5)}
      onNodeClick={(node: object) => onNodeClick(node as GraphNode)}
      onNodeHover={onNodeHover ? (node: object | null) => onNodeHover(node as GraphNode | null) : undefined}
      nodeCanvasObjectMode={() => (nodeViewers || nodeClusterLabels || showLabels ? 'after' : undefined)}
      nodeCanvasObject={nodeViewers || nodeClusterLabels || showLabels ? nodeCanvasObject : undefined}
      width={width}
      height={height}
      nodeRelSize={5}
      enableNodeDrag
      enableZoomInteraction
      cooldownTicks={100}
    />
  );
};

export default React.memo(ForceGraph);

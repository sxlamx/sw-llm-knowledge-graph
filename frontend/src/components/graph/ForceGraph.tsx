import React, { useCallback, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { GraphData, GraphNode, GraphEdge } from '../../api/graphApi';

export const ENTITY_TYPE_COLORS: Record<string, string> = {
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
  /** node_id → normalised score [0, 1]; scales node radius when provided */
  analyticsScores?: Record<string, number>;
  width?: number;
  height?: number;
}

const MAX_NODES = 5000;
const MAX_EDGES = 7000;

const ForceGraph: React.FC<Props> = ({
  graphData,
  highlightedNodeIds = [],
  onNodeClick,
  analyticsScores,
  width,
  height,
}) => {
  const fgRef = useRef<ReturnType<typeof ForceGraph2D> | null>(null);

  const nodes: ForceGraphNode[] = (graphData?.nodes ?? []).slice(0, MAX_NODES);
  const nodeIds = new Set(nodes.map((n) => n.id));
  const links: ForceGraphLink[] = (graphData?.edges ?? [])
    .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
    .slice(0, MAX_EDGES);

  const getNodeColor = useCallback(
    (node: object) => {
      const n = node as ForceGraphNode;
      if (highlightedNodeIds.length > 0) {
        return highlightedNodeIds.includes(n.id)
          ? (ENTITY_TYPE_COLORS[n.entity_type] ?? '#888')
          : '#ccc';
      }
      return ENTITY_TYPE_COLORS[n.entity_type] ?? '#888';
    },
    [highlightedNodeIds]
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

  return (
    <ForceGraph2D
      ref={fgRef as React.MutableRefObject<null>}
      graphData={{ nodes, links }}
      nodeId="id"
      nodeLabel={(n: object) => (n as ForceGraphNode).label}
      nodeColor={getNodeColor}
      nodeVal={analyticsScores ? getNodeVal : undefined}
      linkColor={getLinkColor}
      linkWidth={(l: object) => Math.max((l as ForceGraphLink).weight * 2, 0.5)}
      onNodeClick={(node: object) => onNodeClick(node as GraphNode)}
      width={width}
      height={height}
      nodeRelSize={5}
      enableNodeDrag
      enableZoomInteraction
      cooldownTicks={100}
    />
  );
};

export default ForceGraph;

import * as d3 from 'd3-force';

interface NodeData {
  id: string;
  x?: number;
  y?: number;
  [key: string]: unknown;
}

interface EdgeData {
  source: string;
  target: string;
  [key: string]: unknown;
}

interface LayoutMessage {
  type: 'layout';
  graph: {
    nodes: NodeData[];
    edges: EdgeData[];
  };
}

self.onmessage = ({ data }: MessageEvent<LayoutMessage>) => {
  if (data.type !== 'layout') return;

  const nodes = data.graph.nodes.map((n) => ({ ...n }));
  const links = data.graph.edges.map((e) => ({ ...e }));

  const simulation = d3
    .forceSimulation(nodes as d3.SimulationNodeDatum[])
    .force(
      'link',
      d3
        .forceLink(links)
        .id((d) => (d as NodeData).id)
        .distance(60)
    )
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(0, 0))
    .stop();

  simulation.tick(300);

  self.postMessage({ type: 'layout_done', nodes, edges: links });
};

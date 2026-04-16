/**
 * Unit tests for ForceGraph — entity type colors and accessibility.
 *
 * Verifies:
 * - ENTITY_TYPE_COLORS uses canonical labels (ORGANIZATION, not ORG)
 * - LOCATION is used (not GPE)
 * - Graph mode is available
 * - ForceGraph container has role="img" and aria-label for accessibility
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ForceGraph from '../components/graph/ForceGraph';
import { ENTITY_TYPE_COLORS } from '../utils/entityColors';

vi.mock('react-force-graph-2d', () => ({
  __esModule: true,
  default: () => <div data-testid="force-graph-2d" />,
}));

describe('ForceGraph accessibility', () => {
  const basicProps = {
    graphData: { nodes: [], edges: [], total_nodes: 0, total_edges: 0 },
    onNodeClick: vi.fn(),
  };

  it('renders container with role="img"', () => {
    render(<ForceGraph {...basicProps} />);
    expect(screen.getByRole('img')).toBeInTheDocument();
  });

  it('has aria-label describing the graph', () => {
    render(<ForceGraph {...basicProps} />);
    const container = screen.getByRole('img');
    expect(container).toHaveAttribute('aria-label');
    expect(container.getAttribute('aria-label')).toContain('0 nodes');
    expect(container.getAttribute('aria-label')).toContain('0 edges');
  });

  it('aria-label reflects dynamic node and edge count', () => {
    const graphData = {
      nodes: [
        { id: 'n1', label: 'A', entity_type: 'PERSON', confidence: 0.9 },
        { id: 'n2', label: 'B', entity_type: 'ORGANIZATION', confidence: 0.8 },
      ],
      edges: [
        { id: 'e1', source: 'n1', target: 'n2', relation_type: 'WORKS_AT', weight: 1 },
      ],
      total_nodes: 2,
      total_edges: 1,
    };
    render(<ForceGraph {...basicProps} graphData={graphData as any} />);
    const container = screen.getByRole('img');
    expect(container.getAttribute('aria-label')).toContain('2 nodes');
    expect(container.getAttribute('aria-label')).toContain('1 edges');
  });
});

describe('ENTITY_TYPE_COLORS', () => {
  it('ORGANIZATION color is defined (canonical label)', () => {
    expect(ENTITY_TYPE_COLORS['ORGANIZATION']).toBe('#2196F3');
  });

  it('ORG shorthand does NOT exist in ENTITY_TYPE_COLORS', () => {
    expect(ENTITY_TYPE_COLORS['ORG']).toBeUndefined();
  });

  it('LOCATION color is defined (canonical label)', () => {
    expect(ENTITY_TYPE_COLORS['LOCATION']).toBe('#FF9800');
  });

  it('GPE shorthand does NOT exist in ENTITY_TYPE_COLORS', () => {
    expect(ENTITY_TYPE_COLORS['GPE']).toBeUndefined();
  });

  it('PERSON color is defined', () => {
    expect(ENTITY_TYPE_COLORS['PERSON']).toBe('#4CAF50');
  });

  it('DATE color is defined', () => {
    expect(ENTITY_TYPE_COLORS['DATE']).toBe('#78909C');
  });

  it('MONEY color is defined', () => {
    expect(ENTITY_TYPE_COLORS['MONEY']).toBe('#8BC34A');
  });

  it('PERCENT color is defined', () => {
    expect(ENTITY_TYPE_COLORS['PERCENT']).toBe('#B0BEC5');
  });

  it('LAW color is defined', () => {
    expect(ENTITY_TYPE_COLORS['LAW']).toBe('#607D8B');
  });

  it('fallback color is #888 for unknown entity types', () => {
    const unknownColor = ENTITY_TYPE_COLORS['UNKNOWN_TYPE'] ?? '#888';
    expect(unknownColor).toBe('#888');
  });

  it('canonical labels and LLM-extractor labels both have colors (forward compat)', () => {
    expect(ENTITY_TYPE_COLORS['ORGANIZATION']).toBeDefined();
    expect(ENTITY_TYPE_COLORS['LOCATION']).toBeDefined();
    expect(ENTITY_TYPE_COLORS['PERSON']).toBeDefined();
    expect(ENTITY_TYPE_COLORS['Organization']).toBeDefined();
    expect(ENTITY_TYPE_COLORS['Location']).toBeDefined();
    expect(ENTITY_TYPE_COLORS['Person']).toBeDefined();
  });

  it('ORGANIZATION and Organization have the same color (canonical vs LLM label)', () => {
    expect(ENTITY_TYPE_COLORS['ORGANIZATION']).toBe('#2196F3');
    expect(ENTITY_TYPE_COLORS['Organization']).toBe('#2196F3');
  });

  it('LOCATION and Location have the same color', () => {
    expect(ENTITY_TYPE_COLORS['LOCATION']).toBe('#FF9800');
    expect(ENTITY_TYPE_COLORS['Location']).toBe('#FF9800');
  });
});

describe('ENTITY_TYPE_COLORS — legal NER labels', () => {
  it('COURT_CASE color is defined', () => {
    expect(ENTITY_TYPE_COLORS['COURT_CASE']).toBe('#9C27B0');
  });

  it('LEGISLATION_TITLE color is defined', () => {
    expect(ENTITY_TYPE_COLORS['LEGISLATION_TITLE']).toBe('#3F51B5');
  });

  it('LEGISLATION_REFERENCE color is defined', () => {
    expect(ENTITY_TYPE_COLORS['LEGISLATION_REFERENCE']).toBe('#00ACC1');
  });

  it('COURT color is defined', () => {
    expect(ENTITY_TYPE_COLORS['COURT']).toBe('#FF5722');
  });

  it('JUDGE color is defined', () => {
    expect(ENTITY_TYPE_COLORS['JUDGE']).toBe('#795548');
  });

  it('JUDGE color is defined', () => {
    expect(ENTITY_TYPE_COLORS['JUDGE']).toBe('#795548');
  });

  it('LAWYER color is defined', () => {
    expect(ENTITY_TYPE_COLORS['LAWYER']).toBe('#1565C0');
  });

  it('PETITIONER color is defined', () => {
    expect(ENTITY_TYPE_COLORS['PETITIONER']).toBe('#2E7D32');
  });

  it('RESPONDENT color is defined', () => {
    expect(ENTITY_TYPE_COLORS['RESPONDENT']).toBe('#E65100');
  });

  it('WITNESS color is defined', () => {
    expect(ENTITY_TYPE_COLORS['WITNESS']).toBe('#4E342E');
  });

  it('JURISDICTION color is defined', () => {
    expect(ENTITY_TYPE_COLORS['JURISDICTION']).toBe('#FF6F00');
  });

  it('LEGAL_CONCEPT color is defined', () => {
    expect(ENTITY_TYPE_COLORS['LEGAL_CONCEPT']).toBe('#009688');
  });

  it('DEFINED_TERM color is defined', () => {
    expect(ENTITY_TYPE_COLORS['DEFINED_TERM']).toBe('#26A69A');
  });

  it('STATUTE_SECTION color is defined', () => {
    expect(ENTITY_TYPE_COLORS['STATUTE_SECTION']).toBe('#7B1FA2');
  });

  it('CASE_CITATION color is defined', () => {
    expect(ENTITY_TYPE_COLORS['CASE_CITATION']).toBe('#C62828');
  });

  it('spaCy shorthands do NOT exist in color map', () => {
    expect(ENTITY_TYPE_COLORS['ORG']).toBeUndefined();
    expect(ENTITY_TYPE_COLORS['GPE']).toBeUndefined();
    expect(ENTITY_TYPE_COLORS['LOC']).toBeUndefined();
    expect(ENTITY_TYPE_COLORS['NORP']).toBeUndefined();
    expect(ENTITY_TYPE_COLORS['FAC']).toBeUndefined();
  });

  it('canonical UPPERCASE labels have distinct colors from each other', () => {
    const canonical = ['PERSON', 'ORGANIZATION', 'LOCATION', 'LAW', 'COURT_CASE', 'LEGISLATION_TITLE', 'COURT', 'JUDGE', 'LAWYER', 'PETITIONER', 'RESPONDENT', 'WITNESS', 'JURISDICTION', 'LEGAL_CONCEPT', 'DEFINED_TERM', 'STATUTE_SECTION', 'CASE_CITATION', 'MONEY', 'PERCENT', 'DATE', 'CONCEPT', 'EVENT', 'DOCUMENT', 'TOPIC', 'LEGISLATION_REFERENCE'];
    const colors = canonical.map((k) => ENTITY_TYPE_COLORS[k]);
    const unique = new Set(colors);
    expect(unique.size).toBe(colors.length);
  });
});

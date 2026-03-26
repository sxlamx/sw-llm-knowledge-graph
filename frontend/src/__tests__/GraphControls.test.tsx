import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import GraphControls from '../components/graph/GraphControls';

const EDGE_TYPES = ['WORKS_AT', 'RELATED_TO', 'FOUNDED_BY', 'LOCATED_IN', 'PART_OF', 'CITES'];

function setup(overrides: Partial<Parameters<typeof GraphControls>[0]> = {}) {
  const defaults: Parameters<typeof GraphControls>[0] = {
    depth: 2,
    onDepthChange: vi.fn(),
    pathFinderMode: false,
    onPathFinderToggle: vi.fn(),
    activeEdgeTypes: [],
    onEdgeTypeFiltersChange: vi.fn(),
    entityTypeFilters: [],
    onEntityTypeFiltersChange: vi.fn(),
    nerLabelFilters: [],
    onNerLabelFiltersChange: vi.fn(),
  };
  const props = { ...defaults, ...overrides } as Parameters<typeof GraphControls>[0];
  render(<GraphControls {...props} />);
  return props;
}

describe('GraphControls', () => {
  it('renders Graph Controls heading', () => {
    setup();
    expect(screen.getByText('Graph Controls')).toBeInTheDocument();
  });

  it('shows current depth value', () => {
    setup({ depth: 3 });
    expect(screen.getByText('Depth: 3')).toBeInTheDocument();
  });

  it('renders all edge type chips', () => {
    setup();
    // Expand the edge types section first (collapsed by default)
    fireEvent.click(screen.getByText('Edge types'));
    for (const type of EDGE_TYPES) {
      expect(screen.getByText(type)).toBeInTheDocument();
    }
  });

  it('calls onEdgeTypeFiltersChange when an edge type is clicked', () => {
    const onEdgeTypeFiltersChange = vi.fn();
    setup({ onEdgeTypeFiltersChange });
    // Expand the edge types section first
    fireEvent.click(screen.getByText('Edge types'));
    fireEvent.click(screen.getByText('WORKS_AT'));
    expect(onEdgeTypeFiltersChange).toHaveBeenCalled();
  });

  it('Path Finder button is not selected by default', () => {
    setup({ pathFinderMode: false });
    const btn = screen.getByRole('button', { name: /path finder/i });
    expect(btn).not.toHaveClass('Mui-selected');
  });

  it('Path Finder button is selected when pathFinderMode is true', () => {
    setup({ pathFinderMode: true });
    const btn = screen.getByRole('button', { name: /path finder/i });
    expect(btn).toHaveClass('Mui-selected');
  });

  it('calls onPathFinderToggle when Path Finder is clicked', () => {
    const onPathFinderToggle = vi.fn();
    setup({ onPathFinderToggle });
    fireEvent.click(screen.getByRole('button', { name: /path finder/i }));
    expect(onPathFinderToggle).toHaveBeenCalledOnce();
  });

  it('active edge type is checked in the list', () => {
    setup({ activeEdgeTypes: ['WORKS_AT'] });
    // Expand edge types section
    fireEvent.click(screen.getByText('Edge types'));
    // WORKS_AT checkbox should be checked
    const item = screen.getByText('WORKS_AT').closest('li');
    const checkbox = item?.querySelector('input[type="checkbox"]');
    expect(checkbox).toBeChecked();
  });
});

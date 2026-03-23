import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import GraphControls from '../components/graph/GraphControls';

const EDGE_TYPES = ['WORKS_AT', 'RELATED_TO', 'FOUNDED_BY', 'LOCATED_IN', 'PART_OF', 'CITES'];

function setup(overrides: Partial<Parameters<typeof GraphControls>[0]> = {}) {
  const props = {
    depth: 2,
    onDepthChange: vi.fn(),
    pathFinderMode: false,
    onPathFinderToggle: vi.fn(),
    activeEdgeTypes: [],
    onEdgeTypeToggle: vi.fn(),
    entityTypeFilters: [],
    onEntityTypeToggle: vi.fn(),
    nerLabelFilters: [],
    onNerLabelToggle: vi.fn(),
    ...overrides,
  };
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
    for (const type of EDGE_TYPES) {
      expect(screen.getByText(type)).toBeInTheDocument();
    }
  });

  it('calls onEdgeTypeToggle when a chip is clicked', () => {
    const onEdgeTypeToggle = vi.fn();
    setup({ onEdgeTypeToggle });
    fireEvent.click(screen.getByText('WORKS_AT'));
    expect(onEdgeTypeToggle).toHaveBeenCalledWith('WORKS_AT');
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

  it('active edge type chip has primary color', () => {
    setup({ activeEdgeTypes: ['WORKS_AT'] });
    // The active chip should have MuiChip-colorPrimary class
    const chip = screen.getByText('WORKS_AT').closest('.MuiChip-root');
    expect(chip).toHaveClass('MuiChip-colorPrimary');
  });
});

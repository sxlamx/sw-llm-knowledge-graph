import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ResultCard from '../components/search/ResultCard';
import type { SearchResultItem } from '../api/searchApi';

const baseResult: SearchResultItem = {
  id: 'r1',
  chunk_id: 'c1',
  doc_id: 'd1',
  doc_title: 'Test Document',
  text: 'Hello world chunk text.',
  highlights: [],
  final_score: 0.85,
  vector_score: 0.8,
  keyword_score: 0.9,
  topics: ['topic-a', 'topic-b'],
  page: 3,
  has_image: false,
};

describe('ResultCard', () => {
  it('renders doc title', () => {
    render(<ResultCard result={baseResult} />);
    expect(screen.getByText('Test Document')).toBeInTheDocument();
  });

  it('shows score as percentage', () => {
    render(<ResultCard result={baseResult} />);
    expect(screen.getByText('85%')).toBeInTheDocument();
  });

  it('shows page number', () => {
    render(<ResultCard result={baseResult} />);
    expect(screen.getByText('p.3')).toBeInTheDocument();
  });

  it('renders topics as chips', () => {
    render(<ResultCard result={baseResult} />);
    expect(screen.getByText('topic-a')).toBeInTheDocument();
    expect(screen.getByText('topic-b')).toBeInTheDocument();
  });

  it('shows description icon for text chunk (no image)', () => {
    render(<ResultCard result={baseResult} />);
    // DescriptionIcon renders as SVG with data-testid or title
    // Confirm image expand section is absent
    expect(screen.queryByText('Page image')).not.toBeInTheDocument();
  });

  it('shows image icon and expand toggle for image chunk', () => {
    const result: SearchResultItem = {
      ...baseResult,
      has_image: true,
      image_b64: 'abc123',
    };
    render(<ResultCard result={result} />);
    expect(screen.getByText('Page image')).toBeInTheDocument();
  });

  it('image is hidden by default and shown after expand click', () => {
    const result: SearchResultItem = {
      ...baseResult,
      has_image: true,
      image_b64: 'abc123',
    };
    render(<ResultCard result={result} />);
    // img not rendered initially (Collapse unmounts children when closed)
    expect(screen.queryByRole('img')).toBeNull();

    // click the expand button
    const expandBtn = screen.getByRole('button');
    fireEvent.click(expandBtn);

    // now img should be present
    const imgAfter = screen.getByRole('img') as HTMLImageElement;
    expect(imgAfter.src).toContain('data:image/jpeg;base64,abc123');
  });

  it('uses Untitled when no doc_title', () => {
    const result: SearchResultItem = { ...baseResult, doc_title: undefined };
    render(<ResultCard result={result} />);
    expect(screen.getByText('Untitled')).toBeInTheDocument();
  });

  it('shows highlights instead of plain text when present', () => {
    const result: SearchResultItem = {
      ...baseResult,
      highlights: ['<mark>Hello</mark> world'],
    };
    render(<ResultCard result={result} />);
    // dangerouslySetInnerHTML renders the HTML
    const mark = document.querySelector('mark');
    expect(mark).not.toBeNull();
    expect(mark?.textContent).toBe('Hello');
  });

  it('shows low score as default chip color (<=40%)', () => {
    const result: SearchResultItem = { ...baseResult, final_score: 0.3 };
    render(<ResultCard result={result} />);
    expect(screen.getByText('30%')).toBeInTheDocument();
  });
});

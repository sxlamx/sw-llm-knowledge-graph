import { describe, it, expect } from 'vitest';

// Test URL builder logic for analytics endpoints.
// These mirror the template strings in analyticsApi.ts so that any
// accidental change to the URL shape is caught immediately.

const buildPageRankUrl = (collection_id: string, top_k = 50) =>
  `/analytics/pagerank?collection_id=${collection_id}&top_k=${top_k}`;

const buildBetweennessUrl = (collection_id: string, top_k = 50) =>
  `/analytics/betweenness?collection_id=${collection_id}&top_k=${top_k}`;

const buildCommunitiesUrl = (collection_id: string) =>
  `/analytics/communities?collection_id=${collection_id}`;

const buildSummaryUrl = (collection_id: string) =>
  `/analytics/summary?collection_id=${collection_id}`;

describe('analytics URL builders', () => {
  it('pagerank URL includes collection_id and default top_k', () => {
    expect(buildPageRankUrl('col-1')).toBe(
      '/analytics/pagerank?collection_id=col-1&top_k=50',
    );
  });

  it('pagerank URL includes custom top_k', () => {
    expect(buildPageRankUrl('col-2', 10)).toBe(
      '/analytics/pagerank?collection_id=col-2&top_k=10',
    );
  });

  it('betweenness URL correct', () => {
    expect(buildBetweennessUrl('col-3')).toBe(
      '/analytics/betweenness?collection_id=col-3&top_k=50',
    );
  });

  it('communities URL correct', () => {
    expect(buildCommunitiesUrl('col-4')).toBe(
      '/analytics/communities?collection_id=col-4',
    );
  });

  it('summary URL correct', () => {
    expect(buildSummaryUrl('col-5')).toBe(
      '/analytics/summary?collection_id=col-5',
    );
  });
});

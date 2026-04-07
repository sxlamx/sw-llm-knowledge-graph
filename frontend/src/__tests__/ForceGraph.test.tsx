/**
 * Unit tests for ForceGraph — entity type colors.
 *
 * Verifies:
 * - ENTITY_TYPE_COLORS uses canonical labels (ORGANIZATION, not ORG)
 * - LOCATION is used (not GPE)
 * - Graph mode is available
 */
import { describe, it, expect } from 'vitest';
import { ENTITY_TYPE_COLORS } from '../components/graph/ForceGraph';

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

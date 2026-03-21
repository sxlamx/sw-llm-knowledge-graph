import React from 'react';
import { FixedSizeList as List } from 'react-window';
import { Box, Typography, CircularProgress } from '@mui/material';
import ResultCard from './ResultCard';
import { SearchResultItem } from '../../api/searchApi';

interface Props {
  results: SearchResultItem[];
  loading?: boolean;
  latencyMs?: number;
}

const ITEM_HEIGHT = 148;

const SearchResults: React.FC<Props> = ({ results, loading, latencyMs }) => {
  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (results.length === 0) {
    return (
      <Box sx={{ py: 4, textAlign: 'center' }}>
        <Typography color="text.secondary">No results found.</Typography>
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
        {results.length} results{latencyMs != null ? ` · ${latencyMs}ms` : ''}
      </Typography>

      {results.length > 20 ? (
        <List
          height={Math.min(results.length * ITEM_HEIGHT, 700)}
          itemCount={results.length}
          itemSize={ITEM_HEIGHT}
          width="100%"
          overscanCount={3}
        >
          {({ index, style }) => (
            <div style={style}>
              <ResultCard result={results[index]} />
            </div>
          )}
        </List>
      ) : (
        results.map((r) => <ResultCard key={r.chunk_id} result={r} />)
      )}
    </Box>
  );
};

export default SearchResults;

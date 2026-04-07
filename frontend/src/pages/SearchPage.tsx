import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import TextField from '@mui/material/TextField';
import Button from '@mui/material/Button';
import Paper from '@mui/material/Paper';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import CircularProgress from '@mui/material/CircularProgress';
import Alert from '@mui/material/Alert';
import Chip from '@mui/material/Chip';
import InputAdornment from '@mui/material/InputAdornment';
import SearchIcon from '@mui/icons-material/Search';
import { useSearchMutation } from '../api/searchApi';
import { useListCollectionsQuery } from '../api/collectionsApi';
import type { SearchResult } from '../types/api';

function ResultCard({ result }: { result: SearchResult }) {
  const score = result.score ?? result.vector_score ?? result.keyword_score ?? 0;
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Box display="flex" justifyContent="space-between" alignItems="flex-start" mb={1}>
        <Typography variant="caption" color="text.secondary">
          {result.doc_id ? `Doc: ${result.doc_id.slice(0, 8)}…` : ''}
          {result.page ? ` · Page ${result.page}` : ''}
        </Typography>
        <Chip
          label={`${(score * 100).toFixed(0)}%`}
          size="small"
          color={score > 0.7 ? 'success' : score > 0.4 ? 'primary' : 'default'}
          variant="outlined"
        />
      </Box>
      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
        {result.text}
      </Typography>
    </Paper>
  );
}

export default function SearchPage() {
  const [searchParams] = useSearchParams();
  const preselectedCollection = searchParams.get('collection_id');

  const { data: collectionsData } = useListCollectionsQuery();
  const [doSearch, { isLoading, error }] = useSearchMutation();

  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'vector' | 'hybrid' | 'keyword' | 'graph'>('vector');
  const [selectedCollections, setSelectedCollections] = useState<string[]>(
    preselectedCollection ? [preselectedCollection] : [],
  );
  const [results, setResults] = useState<SearchResult[]>([]);
  const [latency, setLatency] = useState<number | null>(null);
  const [total, setTotal] = useState(0);

  const collections = collectionsData?.collections ?? [];

  useEffect(() => {
    if (collections.length > 0 && selectedCollections.length === 0) {
      setSelectedCollections(collections.map((c) => c.id));
    }
  }, [collections]);

  const handleSearch = async () => {
    if (!query.trim() || selectedCollections.length === 0) return;
    try {
      const res = await doSearch({
        query: query.trim(),
        collection_ids: selectedCollections,
        mode,
        limit: 20,
      }).unwrap();
      setResults(res.results);
      setTotal(res.total);
      setLatency(res.latency_ms);
    } catch {
      setResults([]);
    }
  };

  const toggleCollection = (id: string) => {
    setSelectedCollections((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id],
    );
  };

  return (
    <Box maxWidth={800} mx="auto">
      <Typography variant="h5" fontWeight={700} mb={3}>Search</Typography>

      <Paper sx={{ p: 2.5, mb: 2 }}>
        <TextField
          fullWidth
          placeholder="Enter your query..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon color="action" />
              </InputAdornment>
            ),
          }}
          sx={{ mb: 2 }}
        />

        <Box display="flex" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1}>
          <ToggleButtonGroup
            value={mode}
            exclusive
            onChange={(_e, v) => v && setMode(v)}
            size="small"
          >
            <ToggleButton value="vector">Vector</ToggleButton>
            <ToggleButton value="keyword">Keyword</ToggleButton>
            <ToggleButton value="hybrid">Hybrid</ToggleButton>
            <ToggleButton value="graph">Graph</ToggleButton>
          </ToggleButtonGroup>

          <Button
            variant="contained"
            onClick={handleSearch}
            disabled={isLoading || !query.trim() || selectedCollections.length === 0}
            startIcon={isLoading ? <CircularProgress size={16} color="inherit" /> : <SearchIcon />}
          >
            Search
          </Button>
        </Box>

        {collections.length > 1 && (
          <Box mt={2}>
            <Typography variant="caption" color="text.secondary" display="block" mb={0.5}>
              Collections
            </Typography>
            <Box display="flex" flexWrap="wrap" gap={0.5}>
              {collections.map((col) => (
                <Chip
                  key={col.id}
                  label={col.name}
                  size="small"
                  onClick={() => toggleCollection(col.id)}
                  color={selectedCollections.includes(col.id) ? 'primary' : 'default'}
                  variant={selectedCollections.includes(col.id) ? 'filled' : 'outlined'}
                />
              ))}
            </Box>
          </Box>
        )}
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>Search failed</Alert>}

      {results.length > 0 && (
        <Box>
          <Box display="flex" alignItems="center" gap={1} mb={2}>
            <Typography variant="body2" color="text.secondary">
              {total} result{total !== 1 ? 's' : ''}
            </Typography>
            {latency !== null && (
              <Typography variant="caption" color="text.disabled">
                · {latency.toFixed(0)} ms
              </Typography>
            )}
          </Box>
          <Box display="flex" flexDirection="column" gap={1.5}>
            {results.map((r) => (
              <ResultCard key={r.id} result={r} />
            ))}
          </Box>
        </Box>
      )}

      {!isLoading && results.length === 0 && query && (
        <Box textAlign="center" py={4}>
          <Typography variant="body2" color="text.secondary">No results found</Typography>
        </Box>
      )}
    </Box>
  );
}

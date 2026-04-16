import React, { useEffect, useCallback, useState, useRef } from 'react';
import {
  Box,
  Grid,
  Paper,
  Typography,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
} from '@mui/material';
import { useAppSelector, useAppDispatch } from '../store';
import { useListCollectionsQuery } from '../api/collectionsApi';
import { useSearchMutation } from '../api/searchApi';
import { setSelectedCollections } from '../store/slices/searchSlice';
import SearchBar from '../components/search/SearchBar';
import SearchResults from '../components/search/SearchResults';
import TopicSidebar from '../components/search/TopicSidebar';
import { Alert, Box as MuiBox } from '@mui/material';

const PAGE_SIZE = 50;

const Search: React.FC = () => {
  const dispatch = useAppDispatch();
  const { query, mode, weights, selectedTopics, selectedCollectionIds } = useAppSelector((s) => s.search);
  const activeCollectionId = useAppSelector((s) => s.collections.activeCollectionId);

  const { data: collectionsData } = useListCollectionsQuery();
  const [doSearch, { data: searchData, isLoading }] = useSearchMutation();
  const [allResults, setAllResults] = useState<import('../types/api').SearchResult[]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const doSearchCb = useCallback(() => {
    if (!query.trim()) return;
    doSearch({
      query,
      collection_ids: selectedCollectionIds.length > 0 ? selectedCollectionIds : [],
      topics: selectedTopics.length > 0 ? selectedTopics : undefined,
      mode: mode as 'vector' | 'hybrid' | 'keyword' | 'graph' | undefined,
      weights: weights as unknown as Record<string, number>,
      limit: PAGE_SIZE,
      offset: 0,
    });
    setOffset(0);
    setAllResults([]);
  }, [query, mode, weights, selectedTopics, selectedCollectionIds, doSearch]);

  useEffect(() => {
    if (!query.trim()) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      doSearchCb();
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, mode, weights, selectedTopics, selectedCollectionIds]);

  useEffect(() => {
    if (searchData?.results) {
      if (offset === 0) {
        setAllResults(searchData.results);
      } else {
        setAllResults((prev) => [...prev, ...searchData.results]);
      }
      setHasMore(searchData.results.length >= PAGE_SIZE);
    }
  }, [searchData, offset]);

  const handleLoadMore = useCallback(() => {
    const nextOffset = offset + PAGE_SIZE;
    setOffset(nextOffset);
    doSearch({
      query,
      collection_ids: selectedCollectionIds.length > 0 ? selectedCollectionIds : [],
      topics: selectedTopics.length > 0 ? selectedTopics : undefined,
      mode: mode as 'vector' | 'hybrid' | 'keyword' | 'graph' | undefined,
      weights: weights as unknown as Record<string, number>,
      limit: PAGE_SIZE,
      offset: nextOffset,
    });
  }, [offset, query, mode, weights, selectedTopics, selectedCollectionIds, doSearch]);

  return (
    <Box>
      <Typography variant="h5" fontWeight={600} mb={2}>
        Search
      </Typography>

      <Paper sx={{ p: 2, mb: 2 }}>
        <Box mb={2}>
          <SearchBar />
        </Box>

        {mode === 'graph' && (
          <MuiBox mb={1}>
            <Alert severity="info" variant="outlined" icon={false}>
              Graph mode uses hybrid search with emphasis on graph proximity.
            </Alert>
          </MuiBox>
        )}

        <FormControl size="small" sx={{ minWidth: 240 }}>
          <InputLabel>Collection</InputLabel>
          <Select
            label="Collection"
            value={selectedCollectionIds[0] ?? activeCollectionId ?? ''}
            onChange={(e) =>
              dispatch(setSelectedCollections(e.target.value ? [e.target.value] : []))
            }
          >
            <MenuItem value="">All collections</MenuItem>
            {collectionsData?.collections.map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Paper>

      <Grid container spacing={2}>
        <Grid item xs={12} md={3}>
          <Paper sx={{ p: 2 }}>
            <TopicSidebar collectionId={selectedCollectionIds[0] ?? activeCollectionId} />
          </Paper>
        </Grid>

        <Grid item xs={12} md={9}>
          {!query.trim() ? (
            <Box sx={{ py: 6, textAlign: 'center' }}>
              <Typography color="text.secondary">
                Enter a query above to search your knowledge graph.
              </Typography>
            </Box>
          ) : (
            <SearchResults
              results={allResults}
              loading={isLoading}
              latencyMs={searchData?.latency_ms}
              hasMore={hasMore}
              onLoadMore={handleLoadMore}
            />
          )}
        </Grid>
      </Grid>
    </Box>
  );
};

export default Search;

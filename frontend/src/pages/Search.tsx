import React, { useEffect } from 'react';
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

const Search: React.FC = () => {
  const dispatch = useAppDispatch();
  const { query, mode, weights, selectedTopics, selectedCollectionIds } = useAppSelector((s) => s.search);
  const activeCollectionId = useAppSelector((s) => s.collections.activeCollectionId);

  const { data: collectionsData } = useListCollectionsQuery();
  const [doSearch, { data: searchData, isLoading }] = useSearchMutation();

  // Run search whenever query changes
  useEffect(() => {
    if (!query.trim()) return;
    doSearch({
      query,
      collection_ids: selectedCollectionIds.length > 0 ? selectedCollectionIds : undefined,
      topics: selectedTopics.length > 0 ? selectedTopics : undefined,
      mode,
      weights,
      limit: 50,
      offset: 0,
    });
  }, [query, mode, weights, selectedTopics, selectedCollectionIds]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Box>
      <Typography variant="h5" fontWeight={600} mb={2}>
        Search
      </Typography>

      <Paper sx={{ p: 2, mb: 2 }}>
        <Box mb={2}>
          <SearchBar />
        </Box>

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
            <TopicSidebar />
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
              results={searchData?.results ?? []}
              loading={isLoading}
              latencyMs={searchData?.latency_ms}
            />
          )}
        </Grid>
      </Grid>
    </Box>
  );
};

export default Search;

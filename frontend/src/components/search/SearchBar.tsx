import React, { useState } from 'react';
import {
  Box,
  TextField,
  IconButton,
  Autocomplete,
  ToggleButtonGroup,
  ToggleButton,
  Tooltip,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import { useNavigate } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../../store';
import { setSearchQuery, setSearchMode, SearchMode } from '../../store/slices/searchSlice';
import { useGetSearchSuggestionsQuery } from '../../api/searchApi';

const MODES: { value: SearchMode; label: string; tip: string }[] = [
  { value: 'hybrid', label: 'Hybrid', tip: 'Vector + BM25 + Graph' },
  { value: 'vector', label: 'Vector', tip: 'Semantic similarity' },
  { value: 'keyword', label: 'BM25', tip: 'Keyword matching' },
  { value: 'graph', label: 'Graph', tip: 'Graph proximity' },
];

const SearchBar: React.FC = () => {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const [inputValue, setInputValue] = useState('');
  const mode = useAppSelector((s) => s.search.mode);
  const activeCollectionId = useAppSelector((s) => s.collections.activeCollectionId);

  const { data: suggestions } = useGetSearchSuggestionsQuery(
    { q: inputValue, collection_id: activeCollectionId ?? undefined },
    { skip: inputValue.length < 2 }
  );

  const handleSearch = () => {
    if (!inputValue.trim()) return;
    dispatch(setSearchQuery(inputValue));
    navigate('/search');
  };

  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <Autocomplete
        freeSolo
        options={Array.isArray(suggestions) ? suggestions : []}
        inputValue={inputValue}
        onInputChange={(_, value) => setInputValue(value)}
        sx={{ flex: 1, minWidth: 260 }}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Search knowledge graph"
            size="small"
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSearch();
            }}
            InputProps={{
              ...params.InputProps,
              endAdornment: (
                <>
                  {params.InputProps.endAdornment}
                  <IconButton size="small" onClick={handleSearch}>
                    <SearchIcon fontSize="small" />
                  </IconButton>
                </>
              ),
            }}
          />
        )}
      />

      <ToggleButtonGroup
        value={mode}
        exclusive
        onChange={(_, v) => v && dispatch(setSearchMode(v as SearchMode))}
        size="small"
      >
        {MODES.map((m) => (
          <Tooltip key={m.value} title={m.tip}>
            <ToggleButton value={m.value} sx={{ px: 1.5, fontSize: '0.75rem' }}>
              {m.label}
            </ToggleButton>
          </Tooltip>
        ))}
      </ToggleButtonGroup>
    </Box>
  );
};

export default SearchBar;

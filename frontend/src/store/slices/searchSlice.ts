import { createSlice, PayloadAction } from '@reduxjs/toolkit';

export type SearchMode = 'hybrid' | 'vector' | 'keyword' | 'graph';

interface SearchWeights {
  vector: number;
  keyword: number;
  graph: number;
}

interface SearchState {
  query: string;
  mode: SearchMode;
  weights: SearchWeights;
  selectedTopics: string[];
  selectedCollectionIds: string[];
}

const initialState: SearchState = {
  query: '',
  mode: 'hybrid',
  weights: { vector: 0.6, keyword: 0.3, graph: 0.1 },
  selectedTopics: [],
  selectedCollectionIds: [],
};

const searchSlice = createSlice({
  name: 'search',
  initialState,
  reducers: {
    setSearchQuery: (state, action: PayloadAction<string>) => {
      state.query = action.payload;
    },
    setSearchMode: (state, action: PayloadAction<SearchMode>) => {
      state.mode = action.payload;
    },
    setSearchWeights: (state, action: PayloadAction<SearchWeights>) => {
      state.weights = action.payload;
    },
    setSelectedTopics: (state, action: PayloadAction<string[]>) => {
      state.selectedTopics = action.payload;
    },
    setSelectedCollections: (state, action: PayloadAction<string[]>) => {
      state.selectedCollectionIds = action.payload;
    },
    clearSearch: (state) => {
      state.query = '';
      state.selectedTopics = [];
    },
  },
});

export const {
  setSearchQuery,
  setSearchMode,
  setSearchWeights,
  setSelectedTopics,
  setSelectedCollections,
  clearSearch,
} = searchSlice.actions;
export default searchSlice.reducer;

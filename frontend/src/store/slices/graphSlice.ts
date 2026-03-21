import { createSlice, PayloadAction } from '@reduxjs/toolkit';

interface GraphState {
  selectedNodeId: string | null;
  pathFinderMode: boolean;
  pathEndpoints: [string | null, string | null];
  depth: number;
  edgeTypeFilters: string[];
  topicFilters: string[];
}

const initialState: GraphState = {
  selectedNodeId: null,
  pathFinderMode: false,
  pathEndpoints: [null, null],
  depth: 2,
  edgeTypeFilters: [],
  topicFilters: [],
};

const graphSlice = createSlice({
  name: 'graph',
  initialState,
  reducers: {
    setSelectedNode: (state, action: PayloadAction<string | null>) => {
      state.selectedNodeId = action.payload;
    },
    setPathFinderMode: (state, action: PayloadAction<boolean>) => {
      state.pathFinderMode = action.payload;
      if (!action.payload) {
        state.pathEndpoints = [null, null];
      }
    },
    setPathEndpoint: (state, action: PayloadAction<string>) => {
      if (!state.pathEndpoints[0]) {
        state.pathEndpoints = [action.payload, null];
      } else if (!state.pathEndpoints[1]) {
        state.pathEndpoints = [state.pathEndpoints[0], action.payload];
      } else {
        state.pathEndpoints = [action.payload, null];
      }
    },
    clearPath: (state) => {
      state.pathEndpoints = [null, null];
    },
    setDepth: (state, action: PayloadAction<number>) => {
      state.depth = action.payload;
    },
    setEdgeTypeFilters: (state, action: PayloadAction<string[]>) => {
      state.edgeTypeFilters = action.payload;
    },
    setTopicFilters: (state, action: PayloadAction<string[]>) => {
      state.topicFilters = action.payload;
    },
  },
});

export const {
  setSelectedNode,
  setPathFinderMode,
  setPathEndpoint,
  clearPath,
  setDepth,
  setEdgeTypeFilters,
  setTopicFilters,
} = graphSlice.actions;
export default graphSlice.reducer;

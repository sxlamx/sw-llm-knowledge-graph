import { createSlice, PayloadAction } from '@reduxjs/toolkit';

export interface PresenceEntry {
  user_id: string;
  name: string;
  node_id: string;
}

interface GraphState {
  selectedNodeId: string | null;
  pathFinderMode: boolean;
  pathEndpoints: [string | null, string | null];
  depth: number;
  edgeTypeFilters: string[];
  topicFilters: string[];
  /** Filter nodes by entity_type (e.g. ["Person", "Organization"]) — sent to backend */
  entityTypeFilters: string[];
  /** Filter nodes whose source chunks contain these NER labels (e.g. ["LEGISLATION_TITLE"]) — sent to backend */
  nerLabelFilters: string[];
  /** Other users currently viewing a node: user_id → PresenceEntry */
  presence: Record<string, PresenceEntry>;
  clusteringEnabled: boolean;
  showClusterLabels: boolean;
  selectedClusterId: number | null;
}

const initialState: GraphState = {
  selectedNodeId: null,
  pathFinderMode: false,
  pathEndpoints: [null, null],
  depth: 2,
  edgeTypeFilters: [],
  topicFilters: [],
  entityTypeFilters: [],
  nerLabelFilters: [],
  presence: {},
  clusteringEnabled: false,
  showClusterLabels: true,
  selectedClusterId: null,
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
    setEntityTypeFilters: (state, action: PayloadAction<string[]>) => {
      state.entityTypeFilters = action.payload;
    },
    setNerLabelFilters: (state, action: PayloadAction<string[]>) => {
      state.nerLabelFilters = action.payload;
    },
    setPresence: (state, action: PayloadAction<PresenceEntry>) => {
      state.presence[action.payload.user_id] = action.payload;
    },
    removePresence: (state, action: PayloadAction<string>) => {
      delete state.presence[action.payload];
    },
    clearPresence: (state) => {
      state.presence = {};
    },
    toggleClustering: (state) => {
      state.clusteringEnabled = !state.clusteringEnabled;
      if (!state.clusteringEnabled) state.selectedClusterId = null;
    },
    toggleClusterLabels: (state) => {
      state.showClusterLabels = !state.showClusterLabels;
    },
    setSelectedCluster: (state, action: PayloadAction<number | null>) => {
      state.selectedClusterId = action.payload;
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
  setEntityTypeFilters,
  setNerLabelFilters,
  setPresence,
  removePresence,
  clearPresence,
  toggleClustering,
  toggleClusterLabels,
  setSelectedCluster,
} = graphSlice.actions;
export default graphSlice.reducer;

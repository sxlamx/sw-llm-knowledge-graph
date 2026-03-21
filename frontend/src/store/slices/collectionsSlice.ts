import { createSlice, PayloadAction } from '@reduxjs/toolkit';

interface CollectionsState {
  activeCollectionId: string | null;
}

const initialState: CollectionsState = {
  activeCollectionId: null,
};

const collectionsSlice = createSlice({
  name: 'collections',
  initialState,
  reducers: {
    setActiveCollection: (state, action: PayloadAction<string | null>) => {
      state.activeCollectionId = action.payload;
    },
  },
});

export const { setActiveCollection } = collectionsSlice.actions;
export default collectionsSlice.reducer;

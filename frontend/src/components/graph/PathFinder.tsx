import React from 'react';
import { Box, Typography, Stack, Chip, Button, CircularProgress } from '@mui/material';
import AltRouteIcon from '@mui/icons-material/AltRoute';
import { useAppDispatch, useAppSelector } from '../../store';
import { clearPath, setPathFinderMode } from '../../store/slices/graphSlice';
import { useGetGraphPathQuery } from '../../api/graphApi';

interface Props {
  collectionId: string;
  onPathHighlight: (nodeIds: string[]) => void;
}

const PathFinder: React.FC<Props> = ({ collectionId, onPathHighlight }) => {
  const dispatch = useAppDispatch();
  const { pathEndpoints } = useAppSelector((s) => s.graph);
  const [startId, endId] = pathEndpoints;

  const { data: pathData, isLoading } = useGetGraphPathQuery(
    { start_id: startId!, end_id: endId!, collection_id: collectionId },
    { skip: !startId || !endId }
  );

  React.useEffect(() => {
    if (pathData?.path) {
      onPathHighlight(pathData.path);
    }
  }, [pathData, onPathHighlight]);

  return (
    <Box
      sx={{
        position: 'absolute',
        bottom: 24,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 10,
        bgcolor: 'background.paper',
        borderRadius: 2,
        px: 2,
        py: 1,
        boxShadow: 3,
        minWidth: 320,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <AltRouteIcon color="primary" fontSize="small" />
        <Typography variant="body2" fontWeight={600}>Path Finder</Typography>

        {isLoading && <CircularProgress size={16} />}

        <Stack direction="row" spacing={0.5} flex={1} justifyContent="center">
          <Chip
            label={startId ? `Node A: ${startId.slice(0, 8)}…` : 'Click node A'}
            size="small"
            color={startId ? 'primary' : 'default'}
          />
          <Chip
            label={endId ? `Node B: ${endId.slice(0, 8)}…` : 'Click node B'}
            size="small"
            color={endId ? 'secondary' : 'default'}
          />
        </Stack>

        {pathData?.path && (
          <Typography variant="caption" color="success.main">
            {pathData.path.length} hops
          </Typography>
        )}

        <Button
          size="small"
          onClick={() => {
            dispatch(clearPath());
            dispatch(setPathFinderMode(false));
            onPathHighlight([]);
          }}
        >
          Exit
        </Button>
      </Stack>
    </Box>
  );
};

export default PathFinder;

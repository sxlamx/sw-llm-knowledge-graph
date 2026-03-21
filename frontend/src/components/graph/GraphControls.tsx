import React from 'react';
import {
  Box,
  Paper,
  Typography,
  Slider,
  Stack,
  ToggleButton,
  Chip,
  Divider,
} from '@mui/material';
import AltRouteIcon from '@mui/icons-material/AltRoute';

const EDGE_TYPES = ['WORKS_AT', 'RELATED_TO', 'FOUNDED_BY', 'LOCATED_IN', 'PART_OF', 'CITES'];

interface Props {
  depth: number;
  onDepthChange: (d: number) => void;
  pathFinderMode: boolean;
  onPathFinderToggle: () => void;
  activeEdgeTypes: string[];
  onEdgeTypeToggle: (type: string) => void;
}

const GraphControls: React.FC<Props> = ({
  depth,
  onDepthChange,
  pathFinderMode,
  onPathFinderToggle,
  activeEdgeTypes,
  onEdgeTypeToggle,
}) => (
  <Paper
    elevation={3}
    sx={{
      position: 'absolute',
      top: 80,
      left: 16,
      zIndex: 10,
      p: 2,
      width: 220,
      borderRadius: 2,
    }}
  >
    <Typography variant="subtitle2" gutterBottom fontWeight={600}>
      Graph Controls
    </Typography>

    <Divider sx={{ mb: 1.5 }} />

    <Box mb={2}>
      <Typography variant="caption">Depth: {depth}</Typography>
      <Slider
        value={depth}
        onChange={(_, v) => onDepthChange(v as number)}
        min={1}
        max={4}
        step={1}
        marks
        size="small"
      />
    </Box>

    <Stack direction="row" alignItems="center" spacing={1} mb={2}>
      <ToggleButton
        value="pathfinder"
        selected={pathFinderMode}
        onChange={onPathFinderToggle}
        size="small"
        color="primary"
        sx={{ flex: 1, fontSize: '0.75rem' }}
      >
        <AltRouteIcon fontSize="small" sx={{ mr: 0.5 }} />
        Path Finder
      </ToggleButton>
    </Stack>

    <Typography variant="caption" display="block" mb={0.5}>
      Edge types
    </Typography>
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
      {EDGE_TYPES.map((t) => (
        <Chip
          key={t}
          label={t}
          size="small"
          clickable
          color={activeEdgeTypes.length === 0 || activeEdgeTypes.includes(t) ? 'primary' : 'default'}
          variant={activeEdgeTypes.includes(t) ? 'filled' : 'outlined'}
          onClick={() => onEdgeTypeToggle(t)}
          sx={{ fontSize: '0.6rem', height: 20 }}
        />
      ))}
    </Box>
  </Paper>
);

export default GraphControls;

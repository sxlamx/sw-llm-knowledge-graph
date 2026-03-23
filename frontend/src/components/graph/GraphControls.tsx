import React from 'react';
import {
  Box,
  Paper,
  Typography,
  Slider,
  Stack,
  ToggleButton,
  Checkbox,
  Collapse,
  Divider,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
} from '@mui/material';
import AltRouteIcon from '@mui/icons-material/AltRoute';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

// ---------------------------------------------------------------------------
// Entity groups with their NER sub-labels
// ---------------------------------------------------------------------------

interface NerEntry { label: string; color: string }
interface EntityGroup { entityType: string; color: string; nerLabels: NerEntry[] }

const ENTITY_GROUPS: EntityGroup[] = [
  {
    entityType: 'Person', color: '#4CAF50',
    nerLabels: [
      { label: 'PERSON',     color: '#4CAF50' },
      { label: 'JUDGE',      color: '#6A1B9A' },
      { label: 'LAWYER',     color: '#1565C0' },
      { label: 'PETITIONER', color: '#2E7D32' },
      { label: 'RESPONDENT', color: '#E65100' },
      { label: 'WITNESS',    color: '#4E342E' },
    ],
  },
  {
    entityType: 'Organization', color: '#2196F3',
    nerLabels: [
      { label: 'ORGANIZATION', color: '#2196F3' },
      { label: 'COURT',        color: '#C62828' },
    ],
  },
  {
    entityType: 'Location', color: '#FF9800',
    nerLabels: [
      { label: 'LOCATION',     color: '#FF9800' },
      { label: 'JURISDICTION', color: '#FF5722' },
    ],
  },
  {
    entityType: 'Concept', color: '#009688',
    nerLabels: [
      { label: 'LEGAL_CONCEPT',         color: '#009688' },
      { label: 'DEFINED_TERM',          color: '#00BCD4' },
      { label: 'LAW',                   color: '#E91E63' },
      { label: 'LEGISLATION_TITLE',     color: '#9C27B0' },
      { label: 'LEGISLATION_REFERENCE', color: '#673AB7' },
      { label: 'STATUTE_SECTION',       color: '#3F51B5' },
      { label: 'CASE_CITATION',         color: '#B71C1C' },
    ],
  },
  {
    entityType: 'Event', color: '#F44336',
    nerLabels: [
      { label: 'COURT_CASE', color: '#F44336' },
      { label: 'DATE',       color: '#9E9E9E' },
    ],
  },
];

// NER labels that don't map to a graph entity type
const UNGROUPED_NER: NerEntry[] = [
  { label: 'MONEY',   color: '#795548' },
  { label: 'PERCENT', color: '#607D8B' },
];

const EDGE_TYPES = ['WORKS_AT', 'RELATED_TO', 'FOUNDED_BY', 'LOCATED_IN', 'PART_OF', 'CITES'];

// ---------------------------------------------------------------------------
// Edge type filter
// ---------------------------------------------------------------------------

interface EdgeFilterProps {
  activeEdgeTypes: string[];
  onChange: (next: string[]) => void;
}

const EdgeTypeFilter: React.FC<EdgeFilterProps> = ({ activeEdgeTypes, onChange }) => {
  const [open, setOpen] = React.useState(false);
  const [explicitlyNone, setExplicitlyNone] = React.useState(false);

  const allSelected = !explicitlyNone && activeEdgeTypes.length === 0;
  const someSelected = !explicitlyNone && activeEdgeTypes.length > 0 && activeEdgeTypes.length < EDGE_TYPES.length;
  const activeCount = allSelected ? 0 : activeEdgeTypes.length;

  const handleSelectAll = () => {
    if (allSelected) { setExplicitlyNone(true); }
    else { setExplicitlyNone(false); onChange([]); }
  };

  const handleItemToggle = (item: string) => {
    setExplicitlyNone(false);
    let next: string[];
    if (allSelected || explicitlyNone) { next = [item]; }
    else if (activeEdgeTypes.includes(item)) {
      next = activeEdgeTypes.filter((i) => i !== item);
      if (next.length === EDGE_TYPES.length) next = [];
    } else {
      next = [...activeEdgeTypes, item];
      if (next.length === EDGE_TYPES.length) next = [];
    }
    onChange(next);
  };

  const isChecked = (item: string) => !explicitlyNone && (allSelected || activeEdgeTypes.includes(item));

  return (
    <Box>
      <Stack direction="row" alignItems="center" sx={{ cursor: 'pointer', py: 0.25 }} onClick={() => setOpen((v) => !v)}>
        <Typography variant="caption" fontWeight={600} sx={{ flex: 1 }}>
          Edge types
          {activeCount > 0 && (
            <Typography component="span" variant="caption" sx={{ ml: 0.75, px: 0.75, py: 0.1, borderRadius: 1, bgcolor: 'primary.main', color: '#fff', fontSize: '0.6rem' }}>
              {activeCount}
            </Typography>
          )}
        </Typography>
        {!allSelected && (
          <Typography component="span" variant="caption" color="text.secondary"
            sx={{ mr: 0.5, fontSize: '0.6rem', '&:hover': { color: 'error.main' } }}
            onClick={(e) => { e.stopPropagation(); onChange([]); }}>
            clear
          </Typography>
        )}
        <IconButton size="small" sx={{ p: 0.25 }}>
          {open ? <ExpandLessIcon sx={{ fontSize: 14 }} /> : <ExpandMoreIcon sx={{ fontSize: 14 }} />}
        </IconButton>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <List dense disablePadding sx={{ mb: 0.5 }}>
          <ListItem disablePadding>
            <ListItemButton dense onClick={(e) => { e.stopPropagation(); handleSelectAll(); }} sx={{ px: 0.5, py: 0.1, borderRadius: 1 }}>
              <ListItemIcon sx={{ minWidth: 28 }}>
                <Checkbox edge="start" checked={allSelected} indeterminate={someSelected} size="small" disableRipple sx={{ p: 0 }}
                  onClick={(e) => { e.stopPropagation(); handleSelectAll(); }} />
              </ListItemIcon>
              <ListItemText primary="Select all" primaryTypographyProps={{ variant: 'caption', fontStyle: 'italic', color: 'text.secondary' }} />
            </ListItemButton>
          </ListItem>
          <Divider sx={{ my: 0.25 }} />
          {EDGE_TYPES.map((item) => (
            <ListItem key={item} disablePadding>
              <ListItemButton dense onClick={() => handleItemToggle(item)} sx={{ px: 0.5, py: 0.1, borderRadius: 1 }}>
                <ListItemIcon sx={{ minWidth: 28 }}>
                  <Checkbox edge="start" checked={isChecked(item)} size="small" disableRipple sx={{ p: 0 }} />
                </ListItemIcon>
                <ListItemText primary={item} primaryTypographyProps={{ variant: 'caption', noWrap: true }} />
              </ListItemButton>
            </ListItem>
          ))}
        </List>
      </Collapse>
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Unified Entity / NER hierarchical filter
// ---------------------------------------------------------------------------

interface EntityNerFilterProps {
  entityTypeFilters: string[];
  nerLabelFilters: string[];
  onEntityTypeFiltersChange: (v: string[]) => void;
  onNerLabelFiltersChange: (v: string[]) => void;
}

const EntityNerFilter: React.FC<EntityNerFilterProps> = ({
  entityTypeFilters, nerLabelFilters, onEntityTypeFiltersChange, onNerLabelFiltersChange,
}) => {
  const [openGroups, setOpenGroups] = React.useState<Record<string, boolean>>({});

  const toggleGroupOpen = (et: string) =>
    setOpenGroups((prev) => ({ ...prev, [et]: !prev[et] }));

  // 'full'    = entire entity type selected via entityTypeFilters (no NER sub-filter)
  // 'partial' = specific NER sub-labels active via nerLabelFilters
  // 'none'    = nothing selected in this group
  const groupState = (group: EntityGroup): 'full' | 'partial' | 'none' => {
    if (entityTypeFilters.includes(group.entityType)) return 'full';
    if (group.nerLabels.some((n) => nerLabelFilters.includes(n.label))) return 'partial';
    return 'none';
  };

  const handleGroupClick = (group: EntityGroup) => {
    const state = groupState(group);
    const groupNerSet = new Set(group.nerLabels.map((n) => n.label));

    if (state === 'none') {
      // Select whole group via entity type filter
      onEntityTypeFiltersChange([...entityTypeFilters, group.entityType]);
    } else if (state === 'full') {
      // Deselect whole group
      onEntityTypeFiltersChange(entityTypeFilters.filter((et) => et !== group.entityType));
    } else {
      // Partial → upgrade to full: clear this group's NER labels, add entity type
      onNerLabelFiltersChange(nerLabelFilters.filter((l) => !groupNerSet.has(l)));
      onEntityTypeFiltersChange([...entityTypeFilters, group.entityType]);
    }
  };

  const handleNerClick = (group: EntityGroup, nerLabel: string) => {
    const state = groupState(group);

    if (state === 'full') {
      // Downgrade from full: remove entity type, keep all NER labels EXCEPT clicked one
      const remaining = group.nerLabels.map((n) => n.label).filter((l) => l !== nerLabel);
      onEntityTypeFiltersChange(entityTypeFilters.filter((et) => et !== group.entityType));
      onNerLabelFiltersChange([...nerLabelFilters, ...remaining]);
    } else {
      // Toggle the specific NER label
      if (nerLabelFilters.includes(nerLabel)) {
        onNerLabelFiltersChange(nerLabelFilters.filter((l) => l !== nerLabel));
      } else {
        onNerLabelFiltersChange([...nerLabelFilters, nerLabel]);
      }
    }
  };

  const handleUngroupedClick = (nerLabel: string) => {
    if (nerLabelFilters.includes(nerLabel)) {
      onNerLabelFiltersChange(nerLabelFilters.filter((l) => l !== nerLabel));
    } else {
      onNerLabelFiltersChange([...nerLabelFilters, nerLabel]);
    }
  };

  const activeCount = entityTypeFilters.length + nerLabelFilters.length;

  return (
    <Box>
      <Stack direction="row" alignItems="center" sx={{ py: 0.25 }}>
        <Typography variant="caption" fontWeight={600} sx={{ flex: 1 }}>
          Entity / NER
          {activeCount > 0 && (
            <Typography component="span" variant="caption" sx={{ ml: 0.75, px: 0.75, py: 0.1, borderRadius: 1, bgcolor: 'primary.main', color: '#fff', fontSize: '0.6rem' }}>
              {activeCount}
            </Typography>
          )}
        </Typography>
        {activeCount > 0 && (
          <Typography component="span" variant="caption" color="text.secondary"
            sx={{ mr: 0.5, fontSize: '0.6rem', cursor: 'pointer', '&:hover': { color: 'error.main' } }}
            onClick={() => { onEntityTypeFiltersChange([]); onNerLabelFiltersChange([]); }}>
            clear
          </Typography>
        )}
      </Stack>

      {ENTITY_GROUPS.map((group) => {
        const state = groupState(group);
        const isOpen = openGroups[group.entityType] ?? false;
        const activeNerCount = group.nerLabels.filter((n) => nerLabelFilters.includes(n.label)).length;

        return (
          <Box key={group.entityType}>
            {/* Group header row */}
            <Stack direction="row" alignItems="center">
              <Checkbox
                size="small"
                checked={state !== 'none'}
                indeterminate={state === 'partial'}
                sx={{
                  p: 0.25,
                  color: group.color,
                  '&.Mui-checked': { color: state === 'partial' ? group.color + '88' : group.color },
                  '&.MuiCheckbox-indeterminate': { color: group.color },
                }}
                onClick={(e) => { e.stopPropagation(); handleGroupClick(group); }}
              />
              <Typography
                variant="caption"
                fontWeight={state !== 'none' ? 700 : 400}
                sx={{ flex: 1, cursor: 'pointer', color: state !== 'none' ? group.color : 'text.primary', userSelect: 'none' }}
                onClick={() => toggleGroupOpen(group.entityType)}
              >
                {group.entityType}
                {state === 'partial' && (
                  <Typography component="span" sx={{ ml: 0.5, fontSize: '0.6rem', color: group.color + 'bb' }}>
                    {activeNerCount}/{group.nerLabels.length}
                  </Typography>
                )}
              </Typography>
              <IconButton size="small" sx={{ p: 0.25 }} onClick={() => toggleGroupOpen(group.entityType)}>
                {isOpen ? <ExpandLessIcon sx={{ fontSize: 12 }} /> : <ExpandMoreIcon sx={{ fontSize: 12 }} />}
              </IconButton>
            </Stack>

            {/* NER sub-labels */}
            <Collapse in={isOpen} unmountOnExit>
              <Box sx={{ pl: 2.5, mb: 0.25 }}>
                {group.nerLabels.map((n) => {
                  const isFull = state === 'full';
                  const checked = isFull || nerLabelFilters.includes(n.label);
                  return (
                    <Stack key={n.label} direction="row" alignItems="center" sx={{ py: 0.05 }}>
                      <Checkbox
                        size="small"
                        checked={checked}
                        sx={{
                          p: 0.25,
                          color: n.color,
                          opacity: isFull ? 0.5 : 1,
                          '&.Mui-checked': { color: n.color },
                        }}
                        onClick={() => handleNerClick(group, n.label)}
                      />
                      <Typography
                        variant="caption"
                        onClick={() => handleNerClick(group, n.label)}
                        sx={{
                          cursor: 'pointer',
                          fontSize: '0.65rem',
                          userSelect: 'none',
                          color: checked ? n.color : 'text.secondary',
                          fontWeight: checked && !isFull ? 600 : 400,
                          opacity: isFull ? 0.5 : 1,
                        }}
                      >
                        {n.label}
                      </Typography>
                    </Stack>
                  );
                })}
              </Box>
            </Collapse>
          </Box>
        );
      })}

      {/* Ungrouped NER labels (no matching entity type) */}
      <Divider sx={{ my: 0.5 }} />
      <Typography variant="caption" sx={{ fontSize: '0.6rem', color: 'text.disabled', display: 'block', mb: 0.25 }}>
        Other
      </Typography>
      {UNGROUPED_NER.map((n) => {
        const checked = nerLabelFilters.includes(n.label);
        return (
          <Stack key={n.label} direction="row" alignItems="center" sx={{ py: 0.05 }}>
            <Checkbox
              size="small"
              checked={checked}
              sx={{ p: 0.25, color: n.color, '&.Mui-checked': { color: n.color } }}
              onClick={() => handleUngroupedClick(n.label)}
            />
            <Typography
              variant="caption"
              onClick={() => handleUngroupedClick(n.label)}
              sx={{ cursor: 'pointer', fontSize: '0.65rem', userSelect: 'none', color: checked ? n.color : 'text.secondary', fontWeight: checked ? 600 : 400 }}
            >
              {n.label}
            </Typography>
          </Stack>
        );
      })}
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  depth: number;
  onDepthChange: (d: number) => void;
  pathFinderMode: boolean;
  onPathFinderToggle: () => void;
  activeEdgeTypes: string[];
  onEdgeTypeFiltersChange: (filters: string[]) => void;
  entityTypeFilters: string[];
  onEntityTypeFiltersChange: (filters: string[]) => void;
  nerLabelFilters: string[];
  onNerLabelFiltersChange: (filters: string[]) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const GraphControls: React.FC<Props> = ({
  depth, onDepthChange, pathFinderMode, onPathFinderToggle,
  activeEdgeTypes, onEdgeTypeFiltersChange,
  entityTypeFilters, onEntityTypeFiltersChange,
  nerLabelFilters, onNerLabelFiltersChange,
}) => (
  <Paper
    elevation={3}
    sx={{
      position: 'absolute', top: 80, left: 16, zIndex: 10,
      p: 1.5, width: 210, borderRadius: 2,
      maxHeight: 'calc(100vh - 100px)', overflowY: 'auto',
    }}
  >
    <Typography variant="subtitle2" gutterBottom fontWeight={600}>Graph Controls</Typography>

    <Divider sx={{ mb: 1.5 }} />

    <Box mb={1.5}>
      <Typography variant="caption">Depth: {depth}</Typography>
      <Slider value={depth} onChange={(_, v) => onDepthChange(v as number)} min={1} max={4} step={1} marks size="small" />
    </Box>

    <Stack direction="row" alignItems="center" spacing={1} mb={1.5}>
      <ToggleButton
        value="pathfinder" selected={pathFinderMode} onChange={onPathFinderToggle}
        size="small" color="primary" sx={{ flex: 1, fontSize: '0.75rem' }}
      >
        <AltRouteIcon fontSize="small" sx={{ mr: 0.5 }} />
        Path Finder
      </ToggleButton>
    </Stack>

    <Divider sx={{ mb: 1 }} />

    <EdgeTypeFilter activeEdgeTypes={activeEdgeTypes} onChange={onEdgeTypeFiltersChange} />

    <Divider sx={{ my: 1 }} />

    <EntityNerFilter
      entityTypeFilters={entityTypeFilters}
      nerLabelFilters={nerLabelFilters}
      onEntityTypeFiltersChange={onEntityTypeFiltersChange}
      onNerLabelFiltersChange={onNerLabelFiltersChange}
    />
  </Paper>
);

export default GraphControls;

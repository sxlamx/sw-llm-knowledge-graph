import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Chip,
  Stack,
  CircularProgress,
  Alert,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Tooltip,
} from '@mui/material';
import {
  useListTemplatesQuery,
  useListExtractionMethodsQuery,
} from '../../api/templatesApi';
import type { TemplateSummary } from '../../api/templatesApi';

interface Props {
  value: string | null;
  onChange: (key: string | null) => void;
  method?: string;
  onMethodChange?: (method: string) => void;
}

const TYPE_COLORS: Record<string, string> = {
  graph: '#4caf50',
  list: '#2196f3',
  set: '#ff9800',
  hypergraph: '#9c27b0',
  temporal_graph: '#00bcd4',
  spatial_graph: '#e91e63',
  spatio_temporal_graph: '#795548',
};

const TemplatePicker: React.FC<Props> = ({ value, onChange, method: methodProp, onMethodChange }) => {
  const { data: templates, isLoading, error } = useListTemplatesQuery();
  const { data: methods } = useListExtractionMethodsQuery();
  const [selectedKey, setSelectedKey] = useState<string | null>(value);
  const [selectedMethod, setSelectedMethod] = useState<string>(methodProp ?? 'standard');

  useEffect(() => { setSelectedKey(value); }, [value]);
  useEffect(() => { setSelectedMethod(methodProp ?? 'standard'); }, [methodProp]);

  const implementedMethods = (methods ?? []).filter((m) => m.implemented);

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" p={2}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error">Failed to load templates</Alert>;
  }

  if (!templates || templates.length === 0) {
    return <Alert severity="info">No templates available</Alert>;
  }

  const handleSelect = (t: TemplateSummary) => {
    const newKey = selectedKey === t.key ? null : t.key;
    setSelectedKey(newKey);
    onChange(newKey);
  };

  const handleMethodChange = (m: string) => {
    setSelectedMethod(m);
    onMethodChange?.(m);
  };

  return (
    <Box>
      <Typography variant="caption" gutterBottom display="block">
        Extraction template
      </Typography>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Chip
          key="__default__"
          label="Default"
          variant={selectedKey === null ? 'filled' : 'outlined'}
          color={selectedKey === null ? 'primary' : 'default'}
          onClick={() => { setSelectedKey(null); onChange(null); }}
          sx={{ borderColor: '#888' }}
          title="No template — use default extraction"
        />
        {templates.map((t) => (
          <Chip
            key={t.key}
            label={t.name}
            variant={selectedKey === t.key ? 'filled' : 'outlined'}
            color={selectedKey === t.key ? 'primary' : 'default'}
            onClick={() => handleSelect(t)}
            sx={{
              borderColor: TYPE_COLORS[t.type] || '#888',
              ...(selectedKey === t.key
                ? { backgroundColor: TYPE_COLORS[t.type] || '#1976d2', color: '#fff' }
                : {}),
            }}
            title={t.description}
          />
        ))}
      </Stack>
      {value && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
          Using: {value}
        </Typography>
      )}

      {implementedMethods.length > 0 && (
        <FormControl size="small" sx={{ mt: 1.5, minWidth: 180 }}>
          <InputLabel id="extraction-method-label">Method</InputLabel>
          <Select
            labelId="extraction-method-label"
            value={selectedMethod}
            label="Method"
            onChange={(e) => handleMethodChange(e.target.value)}
          >
            {implementedMethods.map((m) => (
              <MenuItem key={m.name} value={m.name}>
                <Tooltip title={m.description} arrow placement="right">
                  <span>{m.name}</span>
                </Tooltip>
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}
    </Box>
  );
};

export default TemplatePicker;
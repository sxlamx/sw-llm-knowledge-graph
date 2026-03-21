import React, { useState } from 'react';
import {
  Card,
  CardContent,
  Typography,
  Box,
  Chip,
  Stack,
  Tooltip,
  Collapse,
  IconButton,
} from '@mui/material';
import DescriptionIcon from '@mui/icons-material/Description';
import ImageIcon from '@mui/icons-material/Image';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import { SearchResultItem } from '../../api/searchApi';

interface Props {
  result: SearchResultItem;
}

const ResultCard: React.FC<Props> = ({ result }) => {
  const scorePercent = Math.round(result.final_score * 100);
  const [imgExpanded, setImgExpanded] = useState(false);

  const renderText = () => {
    if (result.highlights.length > 0) {
      return result.highlights.map((h, i) => (
        <span key={i} dangerouslySetInnerHTML={{ __html: h }} />
      ));
    }
    return result.text.slice(0, 300) + (result.text.length > 300 ? '...' : '');
  };

  return (
    <Card variant="outlined" sx={{ mb: 1 }}>
      <CardContent sx={{ pb: '12px !important' }}>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start" mb={0.5}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ minWidth: 0, flex: 1 }}>
            {result.has_image ? (
              <ImageIcon fontSize="small" color="secondary" />
            ) : (
              <DescriptionIcon fontSize="small" color="action" />
            )}
            <Tooltip title={result.doc_title ?? result.doc_id}>
              <Typography
                variant="subtitle2"
                noWrap
                sx={{ maxWidth: 280 }}
              >
                {result.doc_title ?? 'Untitled'}
              </Typography>
            </Tooltip>
            {result.page != null && (
              <Typography variant="caption" color="text.secondary">
                p.{result.page}
              </Typography>
            )}
          </Stack>
          <Chip
            label={`${scorePercent}%`}
            size="small"
            color={scorePercent > 70 ? 'success' : scorePercent > 40 ? 'warning' : 'default'}
            sx={{ flexShrink: 0, ml: 1 }}
          />
        </Stack>

        <Typography
          variant="body2"
          color="text.secondary"
          sx={{
            display: '-webkit-box',
            WebkitLineClamp: 4,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            lineHeight: 1.5,
          }}
        >
          {renderText()}
        </Typography>

        {result.topics.length > 0 && (
          <Box sx={{ mt: 1, display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
            {result.topics.slice(0, 4).map((t) => (
              <Chip key={t} label={t} size="small" variant="outlined" sx={{ height: 20, fontSize: '0.65rem' }} />
            ))}
          </Box>
        )}

        {result.has_image && result.image_b64 && (
          <Box sx={{ mt: 1 }}>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Chip
                icon={<ImageIcon sx={{ fontSize: '0.75rem !important' }} />}
                label="Page image"
                size="small"
                color="secondary"
                variant="outlined"
                sx={{ height: 20, fontSize: '0.65rem' }}
              />
              <IconButton size="small" onClick={() => setImgExpanded((v) => !v)} sx={{ p: 0.25 }}>
                {imgExpanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
              </IconButton>
            </Stack>
            <Collapse in={imgExpanded}>
              <Box
                component="img"
                src={`data:image/jpeg;base64,${result.image_b64}`}
                alt={`Page ${result.page ?? ''} from ${result.doc_title ?? 'document'}`}
                sx={{
                  mt: 1,
                  maxWidth: '100%',
                  maxHeight: 300,
                  objectFit: 'contain',
                  borderRadius: 1,
                  border: '1px solid',
                  borderColor: 'divider',
                }}
              />
            </Collapse>
          </Box>
        )}
      </CardContent>
    </Card>
  );
};

export default ResultCard;

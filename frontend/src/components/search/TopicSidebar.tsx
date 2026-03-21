import React from 'react';
import {
  Box,
  Typography,
  FormGroup,
  FormControlLabel,
  Checkbox,
  Divider,
  Skeleton,
} from '@mui/material';
import { useAppDispatch, useAppSelector } from '../../store';
import { setSelectedTopics } from '../../store/slices/searchSlice';

// Static topic list for now; Phase 2 will wire up GET /topics
const SAMPLE_TOPICS = [
  'Machine Learning',
  'Natural Language Processing',
  'Knowledge Graphs',
  'Neural Networks',
  'Data Engineering',
  'Computer Vision',
  'Reinforcement Learning',
  'Graph Databases',
];

interface Props {
  loading?: boolean;
}

const TopicSidebar: React.FC<Props> = ({ loading }) => {
  const dispatch = useAppDispatch();
  const selectedTopics = useAppSelector((s) => s.search.selectedTopics);

  const toggle = (topic: string) => {
    const next = selectedTopics.includes(topic)
      ? selectedTopics.filter((t) => t !== topic)
      : [...selectedTopics, topic];
    dispatch(setSelectedTopics(next));
  };

  return (
    <Box>
      <Typography variant="subtitle2" gutterBottom fontWeight={600}>
        Topics
      </Typography>
      <Divider sx={{ mb: 1 }} />
      {loading ? (
        Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} height={28} sx={{ mb: 0.5 }} />
        ))
      ) : (
        <FormGroup>
          {SAMPLE_TOPICS.map((topic) => (
            <FormControlLabel
              key={topic}
              control={
                <Checkbox
                  size="small"
                  checked={selectedTopics.includes(topic)}
                  onChange={() => toggle(topic)}
                />
              }
              label={<Typography variant="body2">{topic}</Typography>}
              sx={{ my: -0.25 }}
            />
          ))}
        </FormGroup>
      )}
    </Box>
  );
};

export default TopicSidebar;

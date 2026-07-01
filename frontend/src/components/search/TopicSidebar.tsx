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
import { useListTopicsQuery } from '../../api/topicsApi';

interface Props {
  collectionId?: string | null;
}

const TopicSidebar: React.FC<Props> = ({ collectionId }) => {
  const dispatch = useAppDispatch();
  const selectedTopics = useAppSelector((s) => s.search.selectedTopics);

  const { data, isLoading } = useListTopicsQuery(
    { collection_id: collectionId!, limit: 100 },
    { skip: !collectionId },
  );

  const topics = data?.topics ?? [];

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
      {!collectionId ? (
        <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
          Select a collection to see topics.
        </Typography>
      ) : isLoading ? (
        Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} height={28} sx={{ mb: 0.5 }} />
        ))
      ) : topics.length === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
          No topics found.
        </Typography>
      ) : (
        <FormGroup>
          {topics.map((topic) => (
            <FormControlLabel
              key={topic.id}
              control={
                <Checkbox
                  size="small"
                  checked={selectedTopics.includes(topic.name)}
                  onChange={() => toggle(topic.name)}
                />
              }
              label={<Typography variant="body2">{topic.name}</Typography>}
              sx={{ my: -0.25 }}
            />
          ))}
        </FormGroup>
      )}
    </Box>
  );
};

export default TopicSidebar;

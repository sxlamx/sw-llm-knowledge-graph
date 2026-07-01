"""Dangling edge pruning — remove edges that reference non-existent entities.

After two-stage (or one-stage) extraction, LLM output may include edges whose
source/target/participants are not in the entity set.  These "dangling" edges
must be pruned before graph construction.
"""

import logging
from typing import List, Optional, Set

from app.models.template import TemplateConfig

logger = logging.getLogger(__name__)


class EdgePruner:
    @staticmethod
    def prune_dangling_binary(edges: List[dict], entity_keys: Set[str]) -> List[dict]:
        valid = []
        pruned_count = 0
        for edge in edges:
            source = str(edge.get("source", ""))
            target = str(edge.get("target", ""))
            if source in entity_keys and target in entity_keys:
                valid.append(edge)
            else:
                pruned_count += 1
        if pruned_count > 0:
            logger.info(f"Pruned {pruned_count} dangling edges (binary)")
        return valid

    @staticmethod
    def prune_dangling_hyperedges(
        edges: List[dict],
        entity_keys: Set[str],
        participants_field: str = "participants",
    ) -> List[dict]:
        valid = []
        pruned_count = 0
        for edge in edges:
            participants = edge.get(participants_field, [])
            if isinstance(participants, str):
                participants = [participants]
            if participants and all(str(p) in entity_keys for p in participants):
                valid.append(edge)
            else:
                pruned_count += 1
        if pruned_count > 0:
            logger.info(f"Pruned {pruned_count} dangling hyperedges")
        return valid

    @staticmethod
    def prune(
        edges: List[dict],
        entity_keys: Set[str],
        template: TemplateConfig,
    ) -> List[dict]:
        if template.type.value == "hypergraph":
            participants_field = (
                template.relation_schema.participants_field
                if template.relation_schema and template.relation_schema.participants_field
                else "participants"
            )
            return EdgePruner.prune_dangling_hyperedges(edges, entity_keys, participants_field)
        return EdgePruner.prune_dangling_binary(edges, entity_keys)
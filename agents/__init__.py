"""
Story Generation Agents

This package contains all agents for the multiagent story generation pipeline.

Phase 2 Agents:
- ConceptAgent: Generate 1-3 event story concepts
- CastingAgent: Assign specific actors to abstract roles
"""

# Phase 2 imports
from agents.concept_agent import ConceptAgent
from agents.casting_agent import CastingAgent

__all__ = [
    "ConceptAgent",
    "CastingAgent",
]

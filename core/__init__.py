"""
Core Foundation

Foundational classes for the multiagent story system:
- Config: Configuration management with Pydantic validation
- BaseAgent: Base class for all agents with OpenAI structured outputs
- GESTBuilder: Graph-building state and methods for GEST construction
"""

from .config import Config
from .base_agent import BaseAgent
from .gest_builder import GESTBuilder

__all__ = ['Config', 'BaseAgent', 'GESTBuilder']

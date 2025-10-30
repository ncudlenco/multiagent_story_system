"""
Core Foundation

Foundational classes for the multiagent story system:
- Config: Configuration management with Pydantic validation
- BaseAgent: Base class for all agents with OpenAI structured outputs
"""

from .config import Config
from .base_agent import BaseAgent

__all__ = ['Config', 'BaseAgent']

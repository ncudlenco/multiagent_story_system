"""
Workflows Module

Exports workflow functions for story generation.
"""

from workflows.recursive_concept import run_recursive_concept
from workflows.detail_workflow import run_detail_workflow

__all__ = ['run_recursive_concept', 'run_detail_workflow']

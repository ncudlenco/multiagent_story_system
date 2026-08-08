"""Pytest fixtures shared across all test files."""

import pytest
import sys
from pathlib import Path

# Add tests directory to path so helpers.py can be imported
sys.path.insert(0, str(Path(__file__).parent))

from simple_gest_random_generator import SimpleGESTRandomGenerator
from tools.building_tools import create_building_tools
from tools.state_tools import create_state_tools


CAPABILITIES_PATH = "data/simulation_environment_capabilities.json"


@pytest.fixture
def generator():
    """Create a fresh generator instance for building/state tool tests."""
    return SimpleGESTRandomGenerator(CAPABILITIES_PATH)


@pytest.fixture
def building_tools(generator):
    """Create building tools bound to the generator with concept events enabled."""
    return {t.name: t for t in create_building_tools(generator, config={
        'enable_concept_events': True,
        'enable_logical_relations': True,
        'enable_semantic_relations': True,
    })}


@pytest.fixture
def state_tools(generator):
    """Create state tools bound to the generator."""
    return {t.name: t for t in create_state_tools(generator)}

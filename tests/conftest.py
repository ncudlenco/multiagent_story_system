"""
Shared test fixtures for validation tools tests.

Provides:
- Config loading
- Capabilities data (full and minimal)
- OpenAI client mocking
- Sample test data structures
- Cache clearing utilities
"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, MagicMock
from typing import Dict, Any

from core.config import Config


# ============================================================================
# Configuration Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def config():
    """Load test configuration (session-scoped, loaded once)."""
    return Config.load('config.yaml')


# ============================================================================
# Capabilities Data Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def full_capabilities():
    """
    Load full simulation capabilities (session-scoped).

    This is the complete 488KB capabilities file.
    Skips test if file doesn't exist.
    """
    capabilities_path = Path("data/simulation_environment_capabilities.json")

    if not capabilities_path.exists():
        pytest.skip(f"Capabilities file not found: {capabilities_path}")

    with open(capabilities_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Unwrap if needed
    if isinstance(data, list) and len(data) == 1:
        return data[0]
    return data


@pytest.fixture(scope="session")
def minimal_capabilities():
    """
    Load minimal test capabilities (session-scoped).

    This is a lightweight fixture (~200 lines) for fast unit tests.
    """
    fixtures_path = Path("tests/fixtures/minimal_capabilities.json")

    if not fixtures_path.exists():
        pytest.skip(f"Minimal capabilities fixture not found: {fixtures_path}")

    with open(fixtures_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Unwrap if needed
    if isinstance(data, list) and len(data) == 1:
        return data[0]
    return data


# ============================================================================
# OpenAI Mock Fixtures
# ============================================================================

@pytest.fixture
def mock_openai_response():
    """
    Factory fixture for creating mock OpenAI responses.

    Usage:
        def test_something(mock_openai_response):
            response = mock_openai_response({"key": "value"})
            # Use response in mocking
    """
    def _create_response(content: Dict[str, Any]) -> Mock:
        """Create a mock OpenAI response with given content."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.content = json.dumps(content)
        return mock_response

    return _create_response


@pytest.fixture
def mock_openai_client(monkeypatch, mock_openai_response):
    """
    Mock OpenAI client to avoid API calls during tests.

    Provides a configurable mock that can be set to return
    specific responses for different test cases.

    Usage:
        def test_llm_tool(mock_openai_client):
            mock_openai_client.set_response({"result": "test"})
            # Tool will use mocked response
    """
    class MockOpenAI:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self._response_queue = []

        def set_response(self, content: Dict[str, Any]):
            """Set the next response to return."""
            self._response_queue.append(content)

        def set_responses(self, contents: list):
            """Set multiple responses to return in sequence."""
            self._response_queue.extend(contents)

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            """Mock create method."""
            if not self._response_queue:
                # Default response if none set
                content = {"result": "mocked_response"}
            else:
                content = self._response_queue.pop(0)

            return mock_openai_response(content)

    # Create mock instance
    mock_client = MockOpenAI()

    # Monkey-patch OpenAI import
    monkeypatch.setattr("utils.validation_tools.OpenAI", lambda api_key: mock_client)

    # Clear LRU cache to ensure fresh client
    from utils.validation_tools import _get_openai_client
    _get_openai_client.cache_clear()

    return mock_client


# ============================================================================
# Sample Data Fixtures
# ============================================================================

@pytest.fixture
def sample_episode_data():
    """Sample episode structure for testing."""
    return {
        "name": "test_episode",
        "regions": [
            {
                "name": "test_region",
                "points_of_interest": [
                    {"id": "chair_1", "type": "chair", "actions": ["SitDown"]},
                    {"id": "desk_1", "type": "desk", "actions": []}
                ],
                "objects": [
                    {"id": "laptop_1", "type": "laptop", "pickupable": True},
                    {"id": "pen_1", "type": "pen", "pickupable": True}
                ],
                "max_actors": 3
            }
        ]
    }


@pytest.fixture
def sample_actor_timeline():
    """Sample actor timeline for temporal testing."""
    return {
        "actor": "actor1",
        "actions": [
            {"action": "Walk", "target": "poi_1"},
            {"action": "SitDown", "target": "chair_1"},
            {"action": "Talk", "target": "actor2"}
        ],
        "starting_location": "region_start"
    }


@pytest.fixture
def sample_created_objects_registry():
    """Sample created objects registry."""
    return {
        "created_laptop_1": {
            "type": "laptop",
            "chain_id": "chain_001",
            "created_by": "actor1"
        },
        "created_pen_1": {
            "type": "pen",
            "chain_id": "chain_002",
            "created_by": "actor2"
        }
    }


@pytest.fixture
def sample_temporal_structure():
    """Sample temporal structure for validation testing."""
    return {
        "events": {
            "a1": {"Action": "Walk", "Entities": ["actor1"], "Location": ["region1"]},
            "a2": {"Action": "SitDown", "Entities": ["actor1", "chair_1"], "Location": ["region1"]},
            "b1": {"Action": "Walk", "Entities": ["actor2"], "Location": ["region1"]},
            "b2": {"Action": "Talk", "Entities": ["actor2", "actor1"], "Location": ["region1"]}
        },
        "temporal": {
            "starting_actions": {"actor1": "a1", "actor2": "b1"},
            "a1": {"relations": [], "next": "a2"},
            "a2": {"relations": ["r1"], "next": None},
            "b1": {"relations": [], "next": "b2"},
            "b2": {"relations": ["r1"], "next": None},
            "r1": {"type": "starts_with", "source": None, "target": None}
        }
    }


# ============================================================================
# Random Graph Generation Fixtures
# ============================================================================

@pytest.fixture
def random_graph_output_dir(tmp_path):
    """
    Temporary directory for random graph outputs with automatic cleanup.

    Uses pytest's tmp_path fixture which automatically cleans up
    after the test session completes.

    Returns:
        Path: Temporary directory path for storing generated GEST graphs
    """
    output_dir = tmp_path / "random_graphs"
    output_dir.mkdir(exist_ok=True)
    yield output_dir
    # Cleanup handled automatically by tmp_path fixture


# ============================================================================
# Cache Clearing Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def clear_lru_caches():
    """
    Automatically clear LRU caches before each test.

    This ensures tests don't interfere with each other
    through cached data.
    """
    from utils.validation_tools import (
        _get_capabilities,
        _get_config,
        _get_openai_client
    )

    # Clear all caches before test
    _get_capabilities.cache_clear()
    _get_config.cache_clear()
    _get_openai_client.cache_clear()

    yield

    # Clear all caches after test
    _get_capabilities.cache_clear()
    _get_config.cache_clear()
    _get_openai_client.cache_clear()


# ============================================================================
# Pytest Configuration
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (integration tests, real API calls)"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (requires --integration flag)"
    )


def pytest_addoption(parser):
    """Add custom command-line options."""
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests with real API calls (expensive)"
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless --integration flag is passed."""
    if config.getoption("--integration"):
        # Integration mode: run all tests
        return

    # Normal mode: skip integration tests
    skip_integration = pytest.mark.skip(reason="need --integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)

# CLAUDE.md - AI Assistant Guide to Multiagent Story System

This document provides comprehensive guidance for AI assistants (like Claude) working on the Multiagent Story Generation System for GTA San Andreas MTA.

---

## PHASE 0 STATUS: COMPLETE

**Phase 0: Migration & Scaffolding** has been completed. The codebase has been completely rebuilt from scratch with modern architecture:

### Key Achievements:
- **Code Reduction**: 5,500 lines → 2,160 lines (60% reduction)
- **Deleted**: 3,500 lines of obsolete code (old agents, schemas, workflows)
- **Created**: 700 lines of clean, modern infrastructure
- **Unified GEST Schema**: Single schema for all refinement levels (no more Level 0-4 complexity)
- **OpenAI Structured Outputs**: Direct API validation (no manual JSON parsing)
- **Config.json Approach**: Clean MTA configuration (no ServerGlobals.lua modification)
- **MTA Client+Server Architecture**: Proper workflow with both processes required

### What's Ready:
✓ Configuration management (Pydantic-based)
✓ Base agent with structured outputs
✓ Unified GEST schema
✓ File manager utilities
✓ MTA controller with client startup
✓ Log parser
✓ Main CLI entry point

### What's NOT Implemented Yet:
✗ Specialized agents (Narrative, Scene, Region, etc.)
✗ LangGraph workflow orchestration
✗ Progressive information loading
✗ Validation loop
✗ Error correction agent

**Current Status**: Foundation complete, ready for Phase 1 (Agent Implementation)

---

## PHASE 1 STATUS: COMPLETE

**Phase 1: Preprocessing Layer** has been completed successfully.

### Key Achievements:
- **85% Token Reduction**: simulation_environment_capabilities.json (14,178 lines) → optimized caches (~1,200-2,500 lines)
- **LLM-Based Preprocessing**: SkinCategorizationAgent, EpisodeSummarizationAgent using GPT-5
- **Batched Processing**: Single API calls for 249 skins and 13 episodes
- **Adaptive Preprocessing**: Optional --skip-episodes flag for faster processing

### What's Ready:
✓ Concept cache (game_capabilities_concept.json - 92% smaller)
✓ Full indexed cache (game_capabilities_full_indexed.json - 82% smaller)
✓ Player skin categorization (by gender/age/attire)
✓ Episode summarization (concise summaries with regions/objects/actions)
✓ Comprehensive test suite (7 test classes, 20+ methods)
✓ Preprocessing CLI (--preprocess-capabilities)

**Current Status**: Preprocessing complete, ready for Phase 2 (Concept & Casting Agents)

See [PHASE_1_COMPLETE.md](PHASE_1_COMPLETE.md) for complete details.

---

## PHASE 2 STATUS: COMPLETE

**Phase 2: Concept & Casting Agents** has been completed and **significantly exceeded** original scope.

### Key Achievements:
- **Recursive Scene Expansion Architecture**: Progressive refinement with parent/leaf hierarchy (NOT in original plan)
- **Enhanced ConceptAgent**: 833 lines with recursive expansion, logical relations, bias-free generation
- **CastingAgent**: Archetype-based skin filtering, minimal narrative expansion
- **Story Diversity Fixes**: Removed GTA SA canonical bias (no more repetitive gang violence stories)
- **Generic Actor Naming**: Concept uses roles (colleague_a, courier), Casting assigns character names
- **Logical Relations**: Added to GEST schema for causal/dependency modeling (causes, enables, prevents)

### Architectural Innovation:
```
OLD: Linear 1-3 events → Outline → Breakdown → Detail
NEW: Recursive(Abstract) → Recursive(Medium) → Recursive(Detailed) → Leaf Expansion
```

### What's Ready:
✓ Recursive concept expansion workflow (workflows/recursive_concept.py)
✓ ConceptAgent with recursive expansion support
✓ CastingAgent with archetype filtering
✓ SceneDetailAgent placeholder structure
✓ Parent/leaf scene hierarchy
✓ Logical relations in GEST schema
✓ 3-phase pipeline (Concept → Casting → Detail)
✓ Artifact tracking at each recursion level
✓ Unicode encoding fixes (Windows compatibility)

### Test Results:
✓ 100% generic role names (no "CJ", "Sweet", "Denise")
✓ Diverse story themes (office drama, neighborhood scenarios - NO gang violence bias)
✓ Structural narratives (relation-focused prose, no event IDs, no descriptive details)
✓ Recursive expansion (1-2 iterations to reach target scene count)

**Current Status**: Concept & Casting complete with recursive architecture, ready for Phase 3 (Scene Detail)

See [PHASE_2_COMPLETE.md](PHASE_2_COMPLETE.md) for complete details.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture Deep Dive](#architecture-deep-dive)
3. [File Organization](#file-organization)
4. [Development Conventions](#development-conventions)
5. [Common Development Tasks](#common-development-tasks)
6. [Agent System Details](#agent-system-details)
7. [Integration Points](#integration-points)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Future Enhancement Areas](#future-enhancement-areas)

---

## Project Overview

### What This System Does

This is a **multiagent story generation system** that creates executable story graphs for GTA San Andreas using hierarchical GEST (Graph of Events in Space and Time) refinement. The system:

1. **Extracts game capabilities** from the MTA game engine (actions, locations, objects)
2. **Generates abstract narratives** using GPT-5 based on optional seed sentences
3. **Refines narratives** through 5 progressive levels from abstract to game-executable
4. **Validates stories** by running actual MTA simulations
5. **Corrects errors** autonomously through an error correction loop
6. **Produces video artifacts** of the generated stories

### Key Technologies

- **LangGraph**: Workflow orchestration and state management
- **OpenAI GPT-5**: All LLM-powered agents
- **Pydantic**: Schema validation and data models
- **Python 3.10+**: Core runtime
- **MTA San Andreas**: Game engine (Lua-based)
- **structlog**: Structured logging
- **python-dotenv**: Environment configuration

### The Progressive Refinement Strategy (PLANNED)

**Note**: This is the planned architecture. Phase 0 has created the foundation, but agents are not yet implemented.

The system will progressively refine stories using a **unified GEST schema** at all levels:

```
Concept Level (1-3 events)
  ↓ (ConceptAgent - meta-structure intent)

Casting Level (same events, actors assigned)
  ↓ (CastingAgent - specific actors)

Outline Level (5-15 events)
  ↓ (OutlineAgent - scene sequence with semantic relations)

Scene Breakdown Level (20-50 events)
  ↓ (SceneBreakdownAgent - scene-level detail)

Scene Detail Level (50-200 events per scene)
  ↓ (SceneDetailAgent - full choreography)

Aggregation Level (all scenes merged)
  ↓ (AggregationAgent - cross-scene temporal relations)

Validation Loop (max 3 attempts)
  ├─ ValidationAgent: Run MTA simulation
  ├─ ErrorCorrectorAgent: Fix issues if validation fails
  └─ Retry validation
```

**Key Innovation**: Single GEST structure throughout - only the number of events and level of detail changes, not the schema.

### Validation Loop Architecture

```
Generate Level 4 GEST
    ↓
Write to JSON file + Configure MTA
    ↓
Launch MTA Server + Client
    ↓
Run Simulation (timeout: 10 min)
    ↓
Parse Logs for Errors
    ↓
  ┌─────────────┴─────────────┐
  │                           │
SUCCESS                   FAILURE
  │                           │
  ├→ Collect Video        ├→ Extract Errors
  └→ Done!                │
                          └→ ErrorCorrectorAgent
                             │
                             └→ Retry (up to 3x total)
```

---

## Architecture Deep Dive

### Core Components (Phase 0)

#### 1. Base Agent (`core/base_agent.py` ~150 lines)

**Phase 0 Implementation** - All specialized agents will extend this:

```python
class BaseAgent(ABC):
    """Base agent using OpenAI structured outputs"""

    def call_llm(self, system_prompt: str, user_prompt: str) -> T:
        """Call OpenAI API with structured output"""
        response = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format=self.output_schema,  # Pydantic schema
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )
        # API returns validated Pydantic model directly!
        return response.choices[0].message.parsed
```

**Key Innovation**: No manual JSON parsing needed - OpenAI API validates against Pydantic schema.

**Specialized Agents (NOT YET IMPLEMENTED)**:
- ConceptAgent - Generate 1-3 event meta-structure
- CastingAgent - Assign specific actors
- OutlineAgent - 5-15 events with semantic relations
- SceneBreakdownAgent - 20-50 events per scene
- SceneDetailAgent - 50-200 events full choreography
- AggregationAgent - Merge scenes with cross-scene temporal relations
- ValidationAgent - Orchestrate MTA simulation
- ErrorCorrectorAgent - Fix validation errors

#### 2. Schemas (`schemas/` ~200 lines)

**Unified GEST Schema** (`schemas/gest.py`):

```python
class GEST(BaseModel):
    """Single schema used at ALL refinement levels"""
    events: Dict[str, GESTEvent]
    temporal: Dict[str, Any]
    spatial: Dict[str, Dict[str, List[SpatialRelation]]]
    semantic: Dict[str, SemanticRelation]
    camera: Dict[str, CameraCommand]
    title: Optional[str]
    narrative: Optional[str]

class DualOutput(BaseModel):
    """All agents return this"""
    gest: GEST
    narrative: str
```

**Key Innovation**: No more Level 0-4 schemas. Single unified structure, progressively refined.

#### 3. Utilities (`utils/`)

**FileManager** (`file_manager.py` ~335 lines):
- Generic JSON load/save operations
- Load game capabilities (as dict, no schema yet)
- Load reference graphs and documentation
- Manage output/cache directories

**MTAController** (`mta_controller.py` ~560 lines):
- **Start/stop MTA server AND client** (CRITICAL)
- Configuration via config.json (no ServerGlobals.lua modification)
- Export game capabilities workflow
- Run validation simulations with timeout
- Backup/restore config.json

**LogParser** (`log_parser.py` ~468 lines):
- Parse MTA server and client logs
- Extract errors, warnings, success indicators
- Track action execution
- Validate simulation results

#### 4. Configuration (`core/config.py` ~150 lines)

**Pydantic-based configuration**:

```python
class Config(BaseModel):
    openai: OpenAIConfig
    mta: MTAConfig
    paths: PathsConfig
    validation: ValidationConfig
    logging: LoggingConfig

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        # Load YAML + inject API key from .env
        return cls(**data)
```

#### 5. Workflow (NOT YET IMPLEMENTED)

**LangGraph Workflow** will be implemented in Phase 1+

---

## File Organization (Phase 0)

### Current Directory Structure

```
multiagent_story_system/
├── main.py                      # CLI entry point (~200 lines)
├── config.yaml                  # System configuration (~50 lines)
├── requirements.txt             # Python dependencies
├── .env.example                 # API key template
├── .env                         # Actual API key (gitignored)
├── .gitignore                   # Version control excludes
├── README.md                    # User documentation
├── MULTIAGENT_SYSTEM.md         # Original implementation spec
├── system_redesign.md           # Phase 0+ redesign spec
├── CLAUDE.md                    # This file - AI assistant guide
├── PHASE_0_COMPLETE.md          # Phase 0 completion summary
│
├── core/                        # Core infrastructure (~300 lines)
│   ├── __init__.py
│   ├── config.py                # Pydantic configuration (~150 lines)
│   └── base_agent.py            # Base agent with structured outputs (~150 lines)
│
├── schemas/                     # Pydantic schemas (~200 lines)
│   ├── __init__.py
│   └── gest.py                  # Unified GEST schema (~200 lines)
│
├── utils/                       # Utility modules (~1,360 lines)
│   ├── __init__.py
│   ├── file_manager.py          # File I/O operations (~335 lines)
│   ├── mta_controller.py        # MTA process control (~560 lines)
│   └── log_parser.py            # Log parsing (~468 lines)
│
├── data/                        # Data files (generated)
│   ├── simulation_environment_capabilities.json   # Exported from MTA
│   └── cache/                   # Cached/processed capabilities
│
├── examples/                    # Example reference graphs
│   └── reference_graphs/        # Reference GEST examples
│
├── output/                      # Generated outputs (future)
│   ├── generated_graphs/        # Final GEST files
│   └── videos/                  # Generated videos
│
└── logs/                        # Log files (generated)
    └── *.log
```

**Phase 0 Total**: ~2,160 lines (60% reduction from 5,500 lines)

**Deleted directories**:
- `agents/` (old agent implementations)
- `graphs/` (old LangGraph workflow)
- `prompts/` (empty)
- `setup/` (setup scripts)
- Old schema files

### Key Files and Responsibilities (Phase 0)

#### Configuration Files

- **config.yaml** (~50 lines): System configuration
  - OpenAI config (model, temperature, max_tokens)
  - MTA paths (server root, resource path, executables, shortcut)
  - Startup/shutdown wait times (20s server startup)
  - Validation settings (max attempts, timeout)
  - File paths (capabilities, cache, output)
  - Logging configuration

- **.env**: Secret credentials
  - `OPENAI_API_KEY=...`

#### Core Infrastructure

- **core/config.py** (~150 lines): Pydantic-based configuration
  - Type-safe configuration loading
  - Validates all settings on load
  - Injects API key from environment
  - Provides `Config.load()` class method

- **core/base_agent.py** (~150 lines): Base agent class
  - OpenAI structured outputs integration
  - Direct Pydantic validation from API
  - System/user prompt building (abstract methods)
  - Retry logic for API calls
  - Generic type support for output schemas

#### Schemas

- **schemas/gest.py** (~200 lines): Unified GEST schema
  - Single schema for all refinement levels
  - Events, temporal, spatial, semantic, camera relations
  - `DualOutput` wrapper (GEST + narrative)
  - Supports progressive refinement (1-200+ events)

#### Utilities

- **utils/file_manager.py** (~335 lines): File I/O operations
  - Generic JSON load/save
  - Load game capabilities (as dict)
  - Load reference graphs and documentation
  - Create output/cache directories

- **utils/mta_controller.py** (~560 lines): MTA process control
  - **Start/stop server AND client** (both required)
  - Configuration via config.json in sv2l resource directory
  - Backup/restore config.json
  - Export game capabilities workflow
  - Run validation simulations
  - Monitor process status with timeout

- **utils/log_parser.py** (~468 lines): Log parsing
  - Parse MTA server and client logs
  - Extract errors, warnings, success indicators
  - Track action execution
  - Validate simulation results

#### Entry Point

- **main.py** (~200 lines): CLI entry point
  - Argument parsing (--export-capabilities, --verbose)
  - Configuration loading
  - Export capabilities workflow
  - Story generation (not yet implemented)

---

## Development Conventions (Phase 0)

### Code Structure Patterns

#### 1. All Agents Extend BaseAgent (Phase 1+)

**Future agent implementation pattern** (not yet implemented in Phase 0):

```python
from core.base_agent import BaseAgent
from schemas.gest import DualOutput
from typing import Dict, Any

class ConceptAgent(BaseAgent[DualOutput]):
    """Generate 1-3 event concept-level GEST"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(
            config=config,
            agent_name="concept_agent",
            output_schema=DualOutput
        )

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """Define the agent's role and capabilities"""
        return """
        You are a concept-level story generation agent.
        Generate a 1-3 event meta-structure that captures story intent.

        [Detailed role description]
        [Output format requirements]
        [Constraints and rules]
        """

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """Provide the specific task and input data"""
        capabilities = context['capabilities']
        seeds = context.get('seeds', [])

        return f"""
        Available Actions: {capabilities['actions']}
        Narrative Seeds: {seeds}

        Task: Generate a 1-3 event concept-level GEST...
        """

    def execute(self, context: Dict[str, Any]) -> DualOutput:
        """Execute agent (inherited from BaseAgent)"""
        system_prompt = self.build_system_prompt(context)
        user_prompt = self.build_user_prompt(context)

        # call_llm returns validated DualOutput directly (no parsing needed!)
        return self.call_llm(system_prompt, user_prompt)
```

**Key Phase 0 Innovation**: No `parse_response()` needed - OpenAI API returns validated Pydantic models directly.

#### 2. Configuration via config.yaml and .env

**config.yaml structure (Phase 0):**
```yaml
openai:
  model: "gpt-4o"
  temperature: 0.7
  max_tokens: 4096

mta:
  server_root: "z:\\Path\\To\\MTA\\server"
  resource_path: "mods/deathmatch/resources/sv2l"
  server_executable: "MTA Server.exe"
  client_shortcut: "Multi Theft Auto.exe - Shortcut.lnk"
  startup_wait_seconds: 20  # Wait for server initialization
  shutdown_wait_seconds: 3

paths:
  simulation_environment_capabilities: "data/simulation_environment_capabilities.json"
  game_capabilities_source: "../sv2l/simulation_environment_capabilities.json"
  output_dir: "output"
  logs_dir: "logs"
  cache_dir: "data/cache"

validation:
  max_attempts: 3
  simulation_timeout_seconds: 3600

logging:
  level: "INFO"
  format: "json"
```

**.env structure:**
```bash
OPENAI_API_KEY=sk-proj-...
```

**Loading in code (Phase 0):**
```python
from core.config import Config

# Load configuration with Pydantic validation
config = Config.load("config.yaml")  # Injects API key from .env

# Access settings
print(config.openai.model)  # "gpt-4o"
print(config.mta.startup_wait_seconds)  # 20
```

#### 3. Logging with structlog

```python
import structlog

logger = structlog.get_logger(__name__)

# Structured logging with key-value pairs
logger.info(
    "agent_initialized",
    agent=agent_name,
    model=self.model,
    temperature=self.temperature
)

logger.error(
    "validation_failed",
    attempt=attempt,
    error=error_msg,
    exc_info=True  # Include traceback
)
```

#### 4. Error Handling Patterns

**Agent execution with retry:**
```python
try:
    result = agent.execute(context, max_retries=3)
except Exception as e:
    logger.error("agent_failed", agent=agent.agent_name, error=str(e))
    # Handle failure (add to errors list, etc.)
```

**Validation with Pydantic:**
```python
from pydantic import ValidationError

try:
    validated = MySchema(**data)
except ValidationError as e:
    logger.error("validation_failed", errors=e.errors())
    raise
```

#### 5. Schema Validation with Pydantic (Phase 0)

Unified GEST schema example:

```python
from pydantic import BaseModel, Field
from typing import Dict, Optional

class GEST(BaseModel):
    """Unified GEST schema for all refinement levels"""

    events: Dict[str, GESTEvent] = Field(default_factory=dict)
    temporal: Dict[str, Any] = Field(default_factory=dict)
    spatial: Dict[str, Dict[str, List[SpatialRelation]]] = Field(default_factory=dict)
    semantic: Dict[str, SemanticRelation] = Field(default_factory=dict)
    camera: Dict[str, CameraCommand] = Field(default_factory=dict)
    title: Optional[str] = None
    narrative: Optional[str] = None

    def model_post_init(self, __context):
        """Custom validation after Pydantic validation"""
        # Ensure event IDs are unique
        event_ids = list(self.events.keys())
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("Duplicate event IDs found")
```

---

## Common Development Tasks (Phase 0+)

### 1. Adding a New Agent (Phase 1+)

**Not yet implemented in Phase 0. Future pattern:**

**Step 1: Create agent file**

Create `agents/my_new_agent.py`:

```python
from core.base_agent import BaseAgent
from schemas.gest import DualOutput
from typing import Dict, Any

class MyNewAgent(BaseAgent[DualOutput]):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(
            config=config,
            agent_name="my_new_agent",
            output_schema=DualOutput
        )

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        return """Your system prompt here"""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        return f"""Your user prompt with {context['data']}"""
```

**Note**: No `parse_response()` needed - OpenAI API returns validated models directly.

**Step 2: Add to workflow (when implemented)**

In future `workflows/story_generation.py`:

    # Update state
    state['my_output'] = result
    state['current_stage'] = 'my_new_stage'

    return state

# Add to graph
workflow.add_node("my_new_node", my_new_node)
workflow.add_edge("previous_node", "my_new_node")
```

### 2. Modifying GEST Schema (Phase 0)

**Location:** `schemas/gest.py`

**Adding a new field to unified GEST:**

```python
class GEST(BaseModel):
    # Existing fields...
    events: Dict[str, GESTEvent]
    temporal: Dict[str, Any]
    spatial: Dict[str, Dict[str, List[SpatialRelation]]]
    semantic: Dict[str, SemanticRelation]
    camera: Dict[str, CameraCommand]
    title: Optional[str]
    narrative: Optional[str]

    # New field
    story_mood: str = Field(
        default="neutral",
        description="Overall mood of the story"
    )

    @field_validator('story_mood')
    def validate_mood(cls, v):
        """Custom validation"""
        allowed = ['happy', 'sad', 'neutral', 'tense', 'mysterious']
        if v not in allowed:
            raise ValueError(f"Mood must be one of {allowed}")
        return v
```

**Important:** After modifying schemas:
1. Update agent prompts to request the new field (when agents are implemented)
2. Test with OpenAI structured outputs

### 3. Testing MTA Integration (Phase 0)

**Current functionality:**

```bash
# Test capability export
python main.py --export-capabilities

# Verbose logging
python main.py --export-capabilities --verbose

# Check logs
cat logs/*.log
```

**Verify MTA workflow:**
1. Server console appears with visible output
2. Client window launches after 20 seconds
3. Both processes execute and auto-shutdown
4. `data/simulation_environment_capabilities.json` created

**Debug MTA issues:**

```bash
# Check MTA logs
cat "z:\More games\GTA San Andreas\MTA-SA1.6\server\mods\deathmatch\logs\server.log"

# Check config.json was written
cat "z:\More games\GTA San Andreas\MTA-SA1.6\server\mods\deathmatch\resources\sv2l\config.json"
```

### 4. Configuration Testing (Phase 0)

**Test configuration loading:**

```python
from core.config import Config

# Load and validate
config = Config.load("config.yaml")

# Access settings
print(f"Model: {config.openai.model}")
print(f"Server: {config.mta.server_root}")
print(f"Startup wait: {config.mta.startup_wait_seconds}")
```

### 5. Future Agent Testing (Phase 1+)

**When agents are implemented**, test pattern:

```python
import pytest
from agents.my_agent import MyAgent
from utils.file_manager import FileManager

@pytest.fixture
def config():
    """Load configuration"""
    return FileManager.load_config('config.yaml')

@pytest.fixture
def agent(config):
    """Create agent instance"""
    return MyAgent(config)

def test_agent_execution(agent):
    """Test agent with sample input"""
    context = {
        'input_data': 'sample input',
        'capabilities': {...}
    }

    result = agent.execute(context)

    assert result is not None
    assert hasattr(result, 'expected_field')

def test_agent_retry_logic(agent, monkeypatch):
    """Test retry on failure"""
    call_count = 0

    def mock_call_llm(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("Temporary error")
        return '{"valid": "response"}'

    monkeypatch.setattr(agent, 'call_llm', mock_call_llm)

    result = agent.execute({}, max_retries=3)
    assert call_count == 3
```

Run tests:
```bash
pytest tests/test_my_agent.py -v
```

---

## Agent System Details

### Agent Lifecycle

1. **Initialization**
   - Load config
   - Initialize OpenAI client
   - Set temperature and max_tokens
   - Configure logging

2. **Execution** (via `execute()` method)
   - Build system prompt (role, constraints, format)
   - Build user prompt (task, input data)
   - Call LLM (with retry on network errors)
   - Parse response (JSON extraction, markdown unwrapping)
   - Validate with Pydantic schema
   - Return validated result

3. **Retry Logic**
   - Up to `max_retries` attempts (default: 3)
   - Retries on: parsing errors, validation errors, network errors
   - Does NOT retry on: invalid API key, rate limits (raises immediately)

### Temperature and Token Settings

**Temperature Guide:**
- **0.2-0.3**: Very precise, deterministic (ValidationAgent, GameGESTGeneratorAgent)
- **0.4-0.5**: Balanced creativity and precision (ErrorCorrectorAgent, ActorChoreographyAgent)
- **0.6-0.7**: Creative but grounded (SceneBreakdownAgent, default)
- **0.8-0.9**: Highly creative (NarrativeAgent)

**Max Tokens by Agent:**
- NarrativeAgent: 6000 (detailed narratives)
- GameGESTGeneratorAgent: 8000 (largest, full executable GEST)
- ActorChoreographyAgent: 5000 (many actions)
- Others: 3000-4096 (standard)

### Prompt Engineering Patterns

#### Pattern 1: Role-Based System Prompts

```python
def build_system_prompt(self, context: Dict[str, Any]) -> str:
    return """
You are a [SPECIFIC ROLE] for GTA San Andreas story generation.

YOUR ROLE:
[1-2 sentence description]

YOUR TASK:
[What this agent does in the pipeline]

CONSTRAINTS:
- [Constraint 1]
- [Constraint 2]
- [Constraint 3]

OUTPUT FORMAT:
[Exact JSON structure]

GROUNDING:
[Reference to game capabilities, previous levels, etc.]
"""
```

#### Pattern 2: Few-Shot Learning

For complex formats (especially GameGESTGeneratorAgent):

```python
def build_user_prompt(self, context: Dict[str, Any]) -> str:
    # Load reference examples
    examples = context.get('reference_graphs', [])

    examples_str = ""
    for ex in examples:
        examples_str += f"\n\nEXAMPLE {ex['name']}:\n{ex['content']}"

    return f"""
{examples_str}

NOW YOUR TASK:
Generate a similar structure for:
{context['input']}
"""
```

#### Pattern 3: Incremental Grounding

Each level references previous levels and game capabilities:

```python
def build_user_prompt(self, context: Dict[str, Any]) -> str:
    prev_level = context['previous_gest']
    capabilities = context['capabilities']

    return f"""
PREVIOUS LEVEL OUTPUT:
{prev_level.model_dump_json(indent=2)}

AVAILABLE GAME CAPABILITIES:
Actions: {[a.name for a in capabilities.actions]}
Regions: {[r.name for r in capabilities.regions]}

YOUR TASK:
Refine the above into [next level description]...
"""
```

### Common Agent Issues and Solutions

#### Issue: Agent returns invalid JSON

**Solution 1:** Improve JSON parsing
```python
def parse_response(self, response: str, context: Dict[str, Any]):
    # Remove markdown
    response = response.strip()
    if response.startswith('```'):
        response = response.split('```')[1]
        if response.startswith('json'):
            response = response[4:]

    # Parse
    data = json.loads(response)
    return self.validate_with_schema(data)
```

**Solution 2:** Add JSON validation to prompt
```python
"IMPORTANT: Return ONLY valid JSON. No markdown, no explanations."
```

#### Issue: Pydantic validation fails

**Debug:**
```python
try:
    validated = self.validate_with_schema(data)
except ValidationError as e:
    logger.error("validation_errors", errors=e.errors())
    # Print each error
    for error in e.errors():
        print(f"Field: {error['loc']}")
        print(f"Error: {error['msg']}")
        print(f"Input: {error['input']}")
```

**Solution:** Update prompt to match schema exactly

#### Issue: LLM exceeds max_tokens

**Solution:** Increase in config.yaml
```yaml
agents:
  my_agent:
    max_tokens: 8000  # Increase from default
```

Or add to prompt:
```
"Be concise. Limit to maximum 50 events."
```

---

## Integration Points

### Python ↔ MTA Communication

The system uses **file-based communication** with subprocess control and **config.json** for configuration.

#### CRITICAL: MTA Client+Server Architecture

**MTA requires BOTH server AND client processes running:**

1. **Start server** - Wait 20 seconds for initialization
2. **Start client** - Client connects to localhost server
3. **Client connection triggers server execution** - Server starts processing
4. **Both auto-shutdown** - When task completes, both processes exit

**Why this matters:**
- Starting server alone does nothing - it waits for client connection
- Client must connect to localhost to trigger server execution
- Both processes are required for export AND validation workflows

#### 1. Configuration via config.json

**Phase 0 Innovation**: No more ServerGlobals.lua modification. Use config.json instead.

**Python side** (`utils/mta_controller.py`):
```python
def set_mode(self, mode: MTAMode, graph_file: Optional[str] = None) -> None:
    """Set MTA mode by writing config.json to server root"""
    config = {}

    if mode == MTAMode.EXPORT:
        config["EXPORT_MODE"] = True
        config["INPUT_GRAPHS"] = []
    elif mode == MTAMode.SIMULATION:
        config["EXPORT_MODE"] = False
        config["INPUT_GRAPHS"] = [graph_file] if graph_file else []

    # Write to server_root/config.json
    self._write_config(config)
```

**MTA Lua side** reads config.json on startup and executes accordingly.

#### 2. Exporting Game Capabilities (Lua → Python)

**Python workflow** (`utils/mta_controller.py`):
```python
def export_game_capabilities(self) -> Tuple[bool, Optional[str]]:
    """Run MTA in EXPORT_MODE and copy capabilities file"""
    # 1. Backup config.json
    backup_path = self._backup_config()

    # 2. Set export mode via config.json
    self.set_mode(MTAMode.EXPORT)

    # 3. Start server (wait 20 seconds)
    self.start_server(wait=True)

    # 4. Start client (CRITICAL - triggers export)
    self.start_client(wait=True)

    # 5. Wait for auto-shutdown (both processes exit when done)
    while self.is_running():
        time.sleep(0.5)

    # 6. Copy simulation_environment_capabilities.json from sv2l to data/
    source = Path('../sv2l/simulation_environment_capabilities.json')
    dest = Path('data/simulation_environment_capabilities.json')
    shutil.copy2(source, dest)

    # 7. Restore original config.json
    self._restore_config(backup_path)

    return True, None
```

**Load capabilities** (`utils/file_manager.py`):
```python
def load_game_capabilities(self) -> GameCapabilities:
    """Load and validate game capabilities"""
    path = self._get_path('simulation_environment_capabilities')  # data/simulation_environment_capabilities.json

    with open(path, 'r') as f:
        data = json.load(f)

    # Validate with Pydantic
    capabilities = GameCapabilities(**data)
    return capabilities
```

**Complete flow**:
```
1. Lua exports → sv2l/simulation_environment_capabilities.json
2. Python copies → data/simulation_environment_capabilities.json
3. Python loads ← data/simulation_environment_capabilities.json

    # 2. Start MTA server
    self._start_server()

    # 3. Wait for export to complete (file exists)
    # 4. Stop server
    self._stop_server()

    return True, None
```

#### 2. Running Simulations (Python → MTA → Python)

**Python orchestration** (`agents/validation_agent.py`):

```python
def validate(self, level_4_gest):
    # 1. Save GEST to input file
    file_manager.save_gest(level_4_gest, 'input_graphs/generated_graph.json')

    # 2. Configure MTA for simulation
    mta_controller.set_simulation_mode(
        graph_path='input_graphs/generated_graph.json',
        artifact_collection=False,  # No video during validation
        debug=False
    )

    # 3. Launch MTA
    mta_controller.start_server()
    mta_controller.start_client()  # Auto-connects

    # 4. Monitor for completion (timeout: 10 min)
    completed = mta_controller.wait_for_completion(timeout=3600)

    # 5. Parse logs
    log_parser = LogParser(mta_controller.get_log_paths())
    results = log_parser.parse_validation_results()

    # 6. Stop MTA
    mta_controller.stop_all()

    return results
```

**MTA side** (automatic):
- Reads `input_graphs/generated_graph.json`
- Executes GraphStory validation
- Runs simulation
- Logs errors/success to `server.log` and `clientscript.log`
- Generates video (if artifact collection enabled)

#### 3. Configuration File Modification

**Setting server modes** (`utils/mta_controller.py`):

```python
def _set_server_mode(self, mode: str, value: bool):
    """Modify ServerGlobals.lua to set mode flags"""
    globals_path = Path(self.server_root) / "mods/deathmatch/resources/sv2l/src/ServerGlobals.lua"

    with open(globals_path, 'r') as f:
        content = f.read()

    # Find and replace flag
    pattern = f"{mode} = (true|false)"
    replacement = f"{mode} = {str(value).lower()}"

    content = re.sub(pattern, replacement, content)

    with open(globals_path, 'w') as f:
        f.write(content)
```

### Video Artifact Collection

**Enable in config:**
```python
mta_controller.set_simulation_mode(
    graph_path='input_graphs/generated_graph.json',
    artifact_collection=True,  # Enable video
    debug=False
)
```

**MTA generates:**
- Video file: `data_out/video_TIMESTAMP.avi`
- Simulation logs
- GraphStory validation results

**Python retrieves:**
```python
video_path = mta_controller.get_latest_video()
# Copy to output directory
shutil.copy(video_path, f'output/videos/video_{timestamp}.avi')
```

---

## Troubleshooting Guide

### Common Issues

#### 1. "Game capabilities not found"

**Cause:** `data/simulation_environment_capabilities.json` doesn't exist

**Solution:**
```bash
python main.py --export-capabilities
```

**Check:**
- MTA server path in config.yaml is correct
- Lua exporter has write permissions in sv2l directory
- Server starts without errors
- File exists in `../sv2l/simulation_environment_capabilities.json` after export

**If automatic copy fails:**
```bash
# Manually copy from sv2l to data/
cp ../sv2l/simulation_environment_capabilities.json data/

# Or on Windows
copy ..\sv2l\simulation_environment_capabilities.json data\
```

**Verify paths in config.yaml:**
- `paths.game_capabilities_source: "../sv2l/simulation_environment_capabilities.json"` (where MTA exports)
- `paths.simulation_environment_capabilities: "data/simulation_environment_capabilities.json"` (where system loads from)

#### 2. "OpenAI API key not found"

**Cause:** `.env` file missing or invalid

**Solution:**
```bash
# Create .env from template
cp .env.example .env

# Edit and add your key
nano .env  # or notepad .env on Windows
```

**Check:**
- `.env` is in project root (same dir as main.py)
- No extra spaces: `OPENAI_API_KEY=sk-...` (not `OPENAI_API_KEY = sk-...`)
- No quotes needed around the key

#### 3. "Validation always fails"

**Debugging steps:**

1. **Check MTA logs:**
```bash
tail -f "z:\...\server\mods\deathmatch\logs\server.log"
```

2. **Run in verbose mode:**
```bash
python main.py --verbose --skip-simulation
```

3. **Examine generated GEST:**
```bash
cat output/generated_graphs/story_*_L4.json | jq .
```

4. **Common validation errors:**
   - Missing objects: Add to Level 4 GEST
   - Invalid action names: Check against simulation_environment_capabilities.json
   - Chain ID conflicts: Ensure unique chain IDs for objects
   - Missing constraints: Add temporal/spatial constraints

5. **Test manually:**
   - Copy Level 4 GEST to `input_graphs/generated_graph.json`
   - Launch MTA manually
   - Check in-game for errors

#### 4. "MTA server won't start"

**Check:**
1. Server path in config.yaml: `mta.server_root`
2. Server executable exists: `MTA Server.exe`
3. No other MTA instance running
4. Firewall permissions

**Debug:**
```python
from utils.mta_controller import MTAController
controller = MTAController(config)
controller.start_server()  # Check output
```

#### 5. "Agent returns gibberish"

**Causes:**
- Temperature too high
- Max tokens too low (response cut off)
- Prompt unclear

**Solutions:**
1. Lower temperature in config.yaml
2. Increase max_tokens
3. Simplify prompt
4. Add more examples (few-shot learning)

#### 6. "Pydantic validation errors" (Phase 0)

**Debug:**
```python
from schemas.gest import GEST
from pydantic import ValidationError

data = {...}  # Your data
try:
    gest = GEST(**data)
except ValidationError as e:
    print(e.errors())
    # Shows which fields are invalid
```

**Common issues:**
- Missing required fields
- Wrong types (string vs int)
- Invalid enum values
- Dict/List structure mismatches

### Logging and Monitoring (Phase 0)

**View logs:**
```bash
# Application logs (generated in logs/ directory)
tail -f logs/*.log

# MTA server log
tail -f "z:\More games\GTA San Andreas\MTA-SA1.6\server\mods\deathmatch\logs\server.log"

# MTA client log
tail -f "z:\More games\GTA San Andreas\MTA-SA1.6\server\mods\deathmatch\logs\clientscript.log"
```

**Adjust log level:**
```yaml
# config.yaml
logging:
  level: "DEBUG"  # Show everything
```

Or via CLI:
```bash
python main.py --export-capabilities --verbose
```

**Structured logging format:**
```
{"event": "mta_controller_initialized", "server_root": "z:\\...\\server", "level": "info"}
{"event": "starting_mta_server", "exe": "MTA Server.exe", "level": "info"}
{"event": "mta_client_started", "level": "info"}
```

---

## Future Enhancement Areas

### 1. Director Agents (Cinematographic)

**Planned but not yet implemented:**

#### SceneManager
- Place objects in 3D space with precise coordinates
- Validate spatial arrangements
- Handle object spawning vs fixed objects

#### CameraDirector
- Select camera angles (close-up, wide, over-shoulder)
- Plan camera movements (pan, zoom, dolly)
- Coordinate cuts and transitions

#### SceneDirector
- Overall cinematic direction
- Pacing and rhythm
- Emotional arc management

**Implementation approach:**
1. Create new agents extending BaseAgent
2. Add to workflow between Level 4 generation and validation
3. Update Level 4 schema to include camera metadata
4. Extend MTA Lua to support camera control commands

### 2. Multi-Game Support

**Vision:** Extend beyond MTA to other game platforms

#### GTA V (FiveM)
- Similar architecture, different API
- Abstract core engine from simulation layer
- Plugin architecture for different games

**Steps:**
1. Create `GameEngine` interface
2. Implement `MTAEngine` and `FiveMEngine`
3. Abstract capabilities format
4. Game-specific action mappings

### 3. Advanced Features

#### Multi-Threaded Agent Execution
- Parallel agent calls where possible
- Faster story generation

#### Real-Time Streaming
- Stream generation progress to web UI
- Live GEST visualization

#### Web UI
- Browser-based interface
- Visual GEST editor
- Story library browser

#### Story Variations
- Generate multiple versions from same seed
- User chooses preferred version
- Interpolate between versions

#### Character Personality Models
- Persistent actor personalities
- Dialogue generation
- Emotion tracking

#### Emotional Arc Optimization
- Analyze emotional trajectory
- Ensure satisfying narrative arc
- Validate against story structure templates

### 4. Performance Optimizations

#### Caching
- Cache LLM responses for common patterns
- Cache game capabilities parsing
- Reuse validated GEST components

#### Incremental Refinement
- Save checkpoint after each level
- Resume from any level
- Edit and re-refine specific levels

#### Batch Processing
- Generate multiple stories in parallel
- Queue system for long runs

---

## Best Practices Summary (Phase 0)

### For AI Assistants Working on This Codebase

#### DO:
✅ Use OpenAI structured outputs (no manual JSON parsing)
✅ Use Pydantic schemas for all data validation
✅ Add comprehensive logging with structlog
✅ Use config.json for MTA configuration (not ServerGlobals.lua)
✅ Start both MTA server AND client (client triggers execution)
✅ Wait 20 seconds after starting server before starting client
✅ Use unified GEST schema across all refinement levels
✅ Handle errors gracefully with retries
✅ Document all changes and reasoning
✅ Follow existing code structure patterns
✅ Follow good coding practices like DRY, SOLID, YAGNI
✅ Follow the Google Python Style Guide: https://google.github.io/styleguide/pyguide.html

#### DON'T:
❌ Hardcode API keys or credentials (use .env)
❌ Modify ServerGlobals.lua (use config.json in sv2l instead)
❌ Start server without starting client (server alone does nothing)
❌ Skip Pydantic validation "just to make it work"
❌ Manually parse JSON from OpenAI responses (use structured outputs)
❌ Create separate schemas for each refinement level (use unified GEST)
❌ Ignore validation errors (they indicate real problems)
❌ Run simulations without timeout (can hang indefinitely)
❌ Modify config.yaml structure without updating core/config.py

### When to Use Each Component (Phase 0)

#### Use `BaseAgent` when (Phase 1+):
- Creating any LLM-powered component
- Need OpenAI structured outputs
- Need Pydantic validation
- Need retry logic

#### Use `FileManager` when:
- Reading/writing JSON files
- Loading game capabilities
- Managing output/cache directories

#### Use `MTAController` when:
- Starting/stopping MTA server and client
- Exporting game capabilities
- Running validation simulations
- Managing config.json

#### Use `LogParser` when:
- Extracting validation results
- Parsing MTA logs
- Checking simulation success

#### Use `Config` when:
- Loading configuration
- Validating settings
- Accessing typed configuration values

---

## Quick Reference (Phase 0)

### Common Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Configure API key
cp .env.example .env
# Edit .env with your OpenAI API key

# Export game capabilities (Phase 0 ready)
python main.py --export-capabilities

# Verbose logging
python main.py --export-capabilities --verbose

# Help
python main.py --help

# View logs
tail -f logs/*.log

# Check MTA logs
cat "z:\More games\GTA San Andreas\MTA-SA1.6\server\mods\deathmatch\logs\server.log"

# Verify config.json location
cat "z:\More games\GTA San Andreas\MTA-SA1.6\server\mods\deathmatch\resources\sv2l\config.json"
```

### Phase 0 Available Commands

- `--export-capabilities`: Export game capabilities from MTA
- `--verbose`: Enable DEBUG logging
- `--help`: Show all available options

### Phase 1+ Commands (Not Yet Implemented)

- `--generate`: Generate story (future)
- `--seeds`: Provide narrative seeds (future)

### Key File Paths (Phase 0)

```
Config: config.yaml
Secrets: .env
Main: main.py
Core: core/*.py (config, base_agent)
Schemas: schemas/gest.py
Utils: utils/*.py (file_manager, mta_controller, log_parser)
Capabilities: data/simulation_environment_capabilities.json
MTA Config: ../sv2l/config.json (in MTA resource)
Logs: logs/*.log
```

### Important Config Keys (Phase 0)

```yaml
openai.model: "gpt-5"
openai.temperature: 0.7
openai.max_tokens: 4096
mta.server_root: "z:\\...\\server"
mta.resource_path: "mods/deathmatch/resources/sv2l"
mta.client_shortcut: "Multi Theft Auto.exe - Shortcut.lnk"
mta.startup_wait_seconds: 20
paths.simulation_environment_capabilities: "data/simulation_environment_capabilities.json"  # Where system loads from
paths.game_capabilities_source: "../sv2l/simulation_environment_capabilities.json"  # Where MTA exports to
validation.max_attempts: 3
validation.simulation_timeout_seconds: 3600
logging.level: "INFO"
```

---

## Conclusion (Phase 0)

This multiagent story generation system has completed **Phase 0: Migration & Scaffolding**. The foundation has been rebuilt from scratch with modern architecture and best practices.

**Phase 0 Achievements:**
- 60% code reduction (5,500 → 2,160 lines)
- Unified GEST schema (no more Level 0-4 complexity)
- OpenAI structured outputs (no manual JSON parsing)
- Config.json approach (cleaner MTA configuration)
- Proper MTA client+server workflow
- Pydantic-based configuration
- Clean, extensible base agent

**Current Status:**
- ✅ Foundation complete and tested
- ✅ MTA integration working (export capabilities)
- ✅ Configuration management robust
- ❌ Agents not yet implemented
- ❌ Workflow orchestration not yet implemented
- ❌ Story generation not yet functional

**Next Steps (Phase 1+):**
1. Implement specialized agents (Concept, Casting, Outline, etc.)
2. Implement LangGraph workflow orchestration
3. Implement progressive information loading
4. Test end-to-end story generation
5. Implement validation and error correction loops

**When extending this system:**
1. Use OpenAI structured outputs (no manual parsing)
2. Extend BaseAgent for all LLM-powered components
3. Use unified GEST schema across all levels
4. Start both MTA server AND client
5. Use config.json for MTA configuration
6. Add comprehensive logging
7. Document your changes

** Commits **
Do not mention authors in commits.
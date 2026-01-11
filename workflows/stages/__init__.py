"""
Reactive Story Generation Workflow Stages

5-stage pipeline for transforming abstract narratives into executable GEST:

Stage 1: Narrative Grounding - Transform abstract to simulatable
Stage 2: Spatial Segmentation - Parse into location-based segments
Stage 3: Setup Generation - Off-camera positioning and PickUp workarounds
Stage 4: Screenplay Generation - On-camera actions with validation
Stage 5: Technical Translation - Build GEST structure

Each stage is a LangGraph workflow with tool-based validation.
"""

from .narrative_grounding import ground_narrative, build_narrative_grounding_workflow
from .spatial_segmentation import segment_narrative_spatially, build_spatial_segmentation_workflow
from .setup_generation import generate_setup_actions, build_setup_generation_workflow
from .screenplay_generation import generate_screenplay, build_screenplay_generation_workflow
from .technical_translation import translate_to_gest, build_technical_translation_workflow

__all__ = [
    "ground_narrative",
    "build_narrative_grounding_workflow",
    "segment_narrative_spatially",
    "build_spatial_segmentation_workflow",
    "generate_setup_actions",
    "build_setup_generation_workflow",
    "generate_screenplay",
    "build_screenplay_generation_workflow",
    "translate_to_gest",
    "build_technical_translation_workflow"
]

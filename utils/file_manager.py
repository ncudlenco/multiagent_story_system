"""
File Manager Utility

Handles all file I/O operations for the multiagent story system.
Clean, generic infrastructure with no dependencies on GEST schemas.
"""

import json
import yaml
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import structlog

logger = structlog.get_logger(__name__)


class FileManager:
    """Manages file operations for the multiagent story system"""

    def __init__(self, config: Dict[str, Any], project_root: Optional[Path] = None):
        """
        Initialize file manager.

        Args:
            config: Configuration dictionary (from Config.to_dict())
            project_root: Optional project root path (defaults to parent of this file)
        """
        self.config = config

        # Determine project root
        if project_root is None:
            self.project_root = Path(__file__).parent.parent.resolve()
        else:
            self.project_root = Path(project_root).resolve()

        logger.info("file_manager_initialized", project_root=str(self.project_root))

        # Create necessary directories
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Ensure all required directories exist"""
        required_dirs = [
            self._get_path("output_dir"),
            self._get_path("logs_dir"),
            self._get_path("cache_dir"),
        ]

        for directory in required_dirs:
            directory.mkdir(parents=True, exist_ok=True)
            logger.debug("directory_ensured", path=str(directory))

    def _get_path(self, key: str) -> Path:
        """
        Get absolute path from config key.

        Args:
            key: Config key from paths section

        Returns:
            Absolute Path object
        """
        relative_path = self.config['paths'][key]
        return self.project_root / relative_path

    # =========================================================================
    # Generic JSON Operations
    # =========================================================================

    def load_json(self, file_path: Path) -> Dict[str, Any]:
        """
        Load JSON file.

        Args:
            file_path: Path to JSON file (absolute or relative to project root)

        Returns:
            Parsed JSON as dictionary

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        # Convert to absolute path if relative
        if not file_path.is_absolute():
            file_path = self.project_root / file_path

        logger.info("loading_json", path=str(file_path))

        if not file_path.exists():
            logger.error("json_file_not_found", path=str(file_path))
            raise FileNotFoundError(f"JSON file not found: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        logger.info("json_loaded", path=str(file_path))
        return data

    def save_json(
        self,
        data: Any,
        file_path: Path,
        pretty: bool = True,
        indent: int = 4
    ) -> Path:
        """
        Save data as JSON file.

        Args:
            data: Data to save (dict, list, or Pydantic model)
            file_path: Path to save to (absolute or relative to project root)
            pretty: Whether to pretty-print JSON
            indent: Indentation level for pretty printing

        Returns:
            Path where file was saved
        """
        # Convert to absolute path if relative
        if not file_path.is_absolute():
            file_path = self.project_root / file_path

        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert Pydantic models to dict
        if hasattr(data, 'model_dump'):
            data = data.model_dump()
        elif hasattr(data, 'dict'):
            data = data.dict()

        # Write JSON
        with open(file_path, 'w', encoding='utf-8') as f:
            if pretty:
                json.dump(data, f, indent=indent, ensure_ascii=False)
            else:
                json.dump(data, f, ensure_ascii=False)

        logger.info("json_saved", path=str(file_path))
        return file_path

    # =========================================================================
    # Game Capabilities
    # =========================================================================

    def load_concept_cache(self) -> Dict[str, Any]:
        """
        Load concept-level game capabilities cache.

        This cache contains ~1,200 lines of summary information:
        - Action chains, action catalog, object types
        - Episode catalog, player skins summary
        - Spatial/temporal relation types, camera actions

        Returns:
            Concept capabilities as dictionary

        Raises:
            FileNotFoundError: If cache file doesn't exist (run preprocessing first)
        """
        cache_path = self._get_path('cache_dir') / 'game_capabilities_concept.json'

        logger.info("loading_concept_cache", path=str(cache_path))

        if not cache_path.exists():
            logger.error("concept_cache_not_found", path=str(cache_path))
            raise FileNotFoundError(
                f"Concept cache file not found: {cache_path}\n"
                f"Run: python main.py --preprocess-capabilities"
            )

        data = self.load_json(cache_path)

        logger.info("concept_cache_loaded", keys=list(data.keys()) if isinstance(data, dict) else 'not_dict')
        return data

    def load_full_indexed_cache(self) -> Dict[str, Any]:
        """
        Load full indexed game capabilities cache.

        This cache contains ~2,500 lines:
        - Everything from concept cache
        - player_skins_categorized (full categorized list)
        - episode_summaries (optional)

        Returns:
            Full indexed capabilities as dictionary

        Raises:
            FileNotFoundError: If cache file doesn't exist (run preprocessing first)
        """
        cache_path = self._get_path('cache_dir') / 'game_capabilities_full_indexed.json'

        logger.info("loading_full_indexed_cache", path=str(cache_path))

        if not cache_path.exists():
            logger.error("full_indexed_cache_not_found", path=str(cache_path))
            raise FileNotFoundError(
                f"Full indexed cache file not found: {cache_path}\n"
                f"Run: python main.py --preprocess-capabilities"
            )

        data = self.load_json(cache_path)

        logger.info("full_indexed_cache_loaded", keys=list(data.keys()) if isinstance(data, dict) else 'not_dict')
        return data

    def load_game_capabilities(self) -> Dict[str, Any]:
        """
        Load game capabilities from JSON file.

        Returns capabilities as generic dict (no schema validation).
        For Phase 0, we don't have a schema that matches the actual JSON structure.

        Returns:
            Game capabilities as dictionary

        Raises:
            FileNotFoundError: If capabilities file doesn't exist
        """
        capabilities_path = self._get_path('game_capabilities')

        logger.info("loading_game_capabilities", path=str(capabilities_path))

        if not capabilities_path.exists():
            logger.error("game_capabilities_not_found", path=str(capabilities_path))
            raise FileNotFoundError(
                f"Game capabilities file not found: {capabilities_path}\n"
                f"Run: python main.py --export-capabilities"
            )

        data = self.load_json(capabilities_path)

        # Note: JSON is array with single object
        if isinstance(data, list) and len(data) > 0:
            data = data[0]

        logger.info("game_capabilities_loaded")
        return data

    # =========================================================================
    # Reference Graphs
    # =========================================================================

    def list_reference_graphs(self) -> List[Path]:
        """
        List all reference graph files.

        Returns:
            List of paths to reference graph JSON files
        """
        reference_dir = self._get_path('reference_graphs')

        if not reference_dir.exists():
            logger.warning("reference_graphs_dir_not_found", path=str(reference_dir))
            return []

        graphs = list(reference_dir.glob("*.json"))
        logger.info("reference_graphs_listed", count=len(graphs))

        return sorted(graphs)

    def load_reference_graph(self, graph_name: str) -> Dict[str, Any]:
        """
        Load a reference graph by name.

        Args:
            graph_name: Name of graph file (with or without .json extension)

        Returns:
            Graph data as dictionary

        Raises:
            FileNotFoundError: If reference graph not found
        """
        if not graph_name.endswith('.json'):
            graph_name += '.json'

        reference_dir = self._get_path('reference_graphs')
        graph_path = reference_dir / graph_name

        if not graph_path.exists():
            raise FileNotFoundError(f"Reference graph not found: {graph_path}")

        data = self.load_json(graph_path)
        logger.info("reference_graph_loaded", name=graph_name)

        return data

    # =========================================================================
    # Configuration
    # =========================================================================

    @staticmethod
    def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Load configuration from YAML file.

        Args:
            config_path: Optional path to config.yaml (defaults to project root)

        Returns:
            Configuration dictionary

        Raises:
            FileNotFoundError: If config file not found
        """
        if config_path is None:
            # Default to config.yaml in project root
            script_dir = Path(__file__).parent.parent
            config_path = script_dir / "config.yaml"

        logger.info("loading_config", path=str(config_path))

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        return config

    # =========================================================================
    # Documentation
    # =========================================================================

    def load_documentation(self, doc_name: str) -> str:
        """
        Load documentation markdown file.

        Args:
            doc_name: Documentation filename (with or without .md extension)

        Returns:
            Documentation content as string

        Raises:
            FileNotFoundError: If documentation file not found
        """
        if not doc_name.endswith('.md'):
            doc_name += '.md'

        doc_dir = self._get_path('documentation')
        doc_path = doc_dir / doc_name

        if not doc_path.exists():
            raise FileNotFoundError(f"Documentation file not found: {doc_path}")

        with open(doc_path, 'r', encoding='utf-8') as f:
            content = f.read()

        logger.info("documentation_loaded", name=doc_name, size=len(content))

        return content

    def list_documentation_files(self) -> List[Path]:
        """
        List all documentation files.

        Returns:
            List of paths to documentation .md files
        """
        doc_dir = self._get_path('documentation')

        if not doc_dir.exists():
            logger.warning("documentation_dir_not_found", path=str(doc_dir))
            return []

        docs = list(doc_dir.glob("*.md"))
        return sorted(docs)

    # =========================================================================
    # Cleanup and Maintenance
    # =========================================================================

    def clean_cache(self) -> int:
        """
        Clean cache directory.

        Returns:
            Number of files deleted
        """
        cache_dir = self._get_path('cache_dir')
        deleted_count = 0

        if cache_dir.exists():
            for item in cache_dir.iterdir():
                if item.is_file():
                    item.unlink()
                    deleted_count += 1
                elif item.is_dir():
                    shutil.rmtree(item)
                    deleted_count += 1

        logger.info("cache_cleaned", count=deleted_count)
        return deleted_count

    # =========================================================================
    # Story Generation Output (Phase 2)
    # =========================================================================

    def create_story_output_dir(self, story_id: Optional[str] = None) -> tuple[Path, str]:
        """
        Create output directory for a story generation run.

        Directory structure:
        output/story_{uuid}/
            ├── metadata.json
            ├── concept_gest.json
            ├── concept_narrative.txt
            ├── casting_gest.json
            ├── casting_narrative.txt
            └── ... (future stages)

        Args:
            story_id: Optional UUID for the story. If None, generates new UUID4.

        Returns:
            Tuple of (story_directory_path, story_id)
        """
        import uuid

        if story_id is None:
            story_id = str(uuid.uuid4())

        output_base = self._get_path('output_dir')
        story_dir = output_base / f"story_{story_id}"

        story_dir.mkdir(parents=True, exist_ok=True)

        # Create metadata file if it doesn't exist
        metadata_path = story_dir / "metadata.json"
        if not metadata_path.exists():
            metadata = {
                "story_id": story_id,
                "created_at": datetime.now().isoformat(),
                "stages_completed": []
            }
            self.save_json(metadata, metadata_path, pretty=True)
            logger.info("story_metadata_created", story_id=story_id, path=str(metadata_path))

        logger.info("story_output_dir_created", story_id=story_id, path=str(story_dir))
        return story_dir, story_id

    def save_stage_output(
        self,
        output_dir: Path,
        stage_name: str,
        gest: Dict[str, Any],
        narrative: str
    ) -> tuple[Path, Path]:
        """
        Save GEST and narrative for a specific stage.

        Args:
            output_dir: Story output directory (from create_story_output_dir)
            stage_name: Stage name (e.g., "concept", "casting", "outline")
            gest: GEST dict (or Pydantic model)
            narrative: Narrative text string

        Returns:
            Tuple of (gest_path, narrative_path)
        """
        # Convert Pydantic models to dict if needed
        if hasattr(gest, 'model_dump'):
            gest = gest.model_dump()
        elif hasattr(gest, 'dict'):
            gest = gest.dict()

        # Save GEST as JSON
        gest_path = output_dir / f"{stage_name}_gest.json"
        self.save_json(gest, gest_path, pretty=True, indent=2)

        # Save narrative as text
        narrative_path = output_dir / f"{stage_name}_narrative.txt"
        narrative_path.write_text(narrative, encoding='utf-8')

        logger.info(
            "stage_output_saved",
            stage=stage_name,
            gest_path=str(gest_path),
            narrative_path=str(narrative_path)
        )

        return (gest_path, narrative_path)

    def update_story_metadata(self, story_dir: Path, stage_name: str) -> None:
        """
        Mark a stage as completed in story metadata.

        Args:
            story_dir: Story output directory (from create_story_output_dir)
            stage_name: Stage name (e.g., "concept", "casting", "outline")
        """
        metadata_path = story_dir / "metadata.json"

        if not metadata_path.exists():
            logger.warning(
                "metadata_file_not_found",
                story_dir=str(story_dir),
                stage=stage_name
            )
            return

        metadata = self.load_json(metadata_path)

        # Add stage to completed list if not already there
        if stage_name not in metadata.get("stages_completed", []):
            metadata["stages_completed"].append(stage_name)
            metadata[f"{stage_name}_completed_at"] = datetime.now().isoformat()

            self.save_json(metadata, metadata_path, pretty=True)
            logger.info(
                "story_metadata_updated",
                stage=stage_name,
                story_id=metadata.get("story_id"),
                stages_completed=metadata["stages_completed"]
            )

"""
Configuration Management

Handles loading and validation of system configuration using Pydantic.
Supports YAML configuration files with environment variable injection.
"""

from typing import Dict, Any, Optional
from pathlib import Path
import yaml
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os


class OpenAIConfig(BaseModel):
    """OpenAI API configuration"""
    model: str = Field(default="gpt-4o", description="Model name to use")
    api_key: str = Field(description="API key from environment variable")
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Temperature (0-2). None = use model default (GPT-5 structured outputs requires 1)"
    )
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description="Max completion tokens (output). None = no limit (GPT-5 decides)"
    )


class MTAConfig(BaseModel):
    """MTA server configuration"""
    server_root: str = Field(description="Path to MTA server root directory")
    resource_path: str = Field(default="mods/deathmatch/resources/sv2l", description="Path to sv2l resource")
    server_executable: str = Field(default="MTA Server.exe", description="Server executable name")
    client_shortcut: str = Field(default="Multi Theft Auto.exe - Shortcut.lnk", description="Client shortcut name")
    client_executable: str = Field(default="Multi Theft Auto.exe", description="Client executable name")
    server_log: str = Field(default="mods/deathmatch/logs/server.log", description="Path to server log")
    client_log: str = Field(default="mods/deathmatch/logs/clientscript.log", description="Path to client log")
    startup_wait_seconds: int = Field(default=20, ge=1, description="Seconds to wait after starting server")
    shutdown_wait_seconds: int = Field(default=3, ge=1, description="Seconds to wait for graceful shutdown")


class PathsConfig(BaseModel):
    """File paths configuration"""
    simulation_environment_capabilities: str = Field(default="data/simulation_environment_capabilities.json", description="Game capabilities JSON file")
    game_capabilities_source: str = Field(default="../sv2l/simulation_environment_capabilities.json", description="Source file from MTA export")
    game_capabilities_concept: str = Field(default="data/cache/game_capabilities_concept.json", description="Preprocessed concept cache")
    game_capabilities_full_indexed: str = Field(default="data/cache/game_capabilities_full_indexed.json", description="Preprocessed full indexed cache")
    output_dir: str = Field(default="output", description="Output directory")
    logs_dir: str = Field(default="logs", description="Logs directory")
    cache_dir: str = Field(default="data/cache", description="Cache directory")
    reference_graphs: str = Field(default="examples/reference_graphs", description="Reference graphs directory")
    documentation: str = Field(default="data/documentation", description="Documentation directory")


class ValidationConfig(BaseModel):
    """Validation settings"""
    max_attempts: int = Field(default=3, ge=1, description="Maximum validation attempts")
    simulation_timeout_seconds: int = Field(default=600, ge=60, description="Simulation timeout in seconds")
    scene_1_max_retries: int = Field(default=2, ge=1, description="Maximum retries for Scene 1 validation")
    require_video_output: bool = Field(default=False, description="Whether video output is required for success")
    error_patterns: list = Field(default_factory=lambda: ["error", "failed", "exception"], description="Error patterns to search in logs")
    success_patterns: list = Field(default_factory=lambda: ["simulation complete", "story complete"], description="Success patterns in logs")


class LoggingConfig(BaseModel):
    """Logging configuration"""
    level: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")
    format: str = Field(default="json", description="Log format (json or text)")


class PromptLoggingConfig(BaseModel):
    """Prompt logging configuration"""
    enabled: bool = Field(default=False, description="Enable prompt logging")
    save_system_prompt: bool = Field(default=True, description="Save system prompts")
    save_user_prompt: bool = Field(default=True, description="Save user prompts")
    save_response_raw: bool = Field(default=False, description="Save raw LLM responses (verbose)")
    save_response_parsed: bool = Field(default=True, description="Save parsed responses")
    separate_files: bool = Field(default=True, description="Save separate files per agent/iteration")


class Config(BaseModel):
    """
    Root configuration for the multiagent story system.

    Loads from YAML file and injects environment variables.
    All sections use Pydantic for validation.
    """
    openai: OpenAIConfig
    mta: MTAConfig
    paths: PathsConfig
    validation: ValidationConfig
    logging: LoggingConfig
    prompt_logging: PromptLoggingConfig = Field(default_factory=PromptLoggingConfig)

    class Config:
        extra = "allow"  # Allow additional fields for extensibility

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        """
        Load configuration from YAML file and environment.

        Args:
            config_path: Path to config.yaml file

        Returns:
            Validated Config object

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If OPENAI_API_KEY not found in environment
        """
        # Load environment variables from .env
        load_dotenv()

        # Check config file exists
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Load YAML
        with open(config_file) as f:
            data = yaml.safe_load(f)

        # Inject API key from environment
        if 'openai' not in data:
            data['openai'] = {}

        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY not found in environment. "
                "Create a .env file with: OPENAI_API_KEY=your-key-here"
            )

        data['openai']['api_key'] = api_key

        # Validate and return
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert config to dictionary (for passing to agents).

        Returns:
            Dictionary representation of config
        """
        return self.model_dump()

"""Prompt logging utility for tracking LLM interactions during story generation."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import structlog

logger = structlog.get_logger(__name__)


class PromptLogger:
    """
    Logs all LLM prompts and responses during story generation.

    Tracks:
    - Individual prompts (system + user)
    - Responses (raw + parsed)
    - Token usage (from OpenAI API)
    - Execution time per call
    - Aggregate statistics across all calls
    """

    def __init__(
        self,
        story_id: str,
        output_dir: Path,
        save_system_prompt: bool = True,
        save_user_prompt: bool = True,
        save_response_raw: bool = False,
        save_response_parsed: bool = True,
        separate_files: bool = True
    ):
        """
        Initialize prompt logger.

        Args:
            story_id: Story identifier
            output_dir: Base output directory (e.g., output/)
            save_system_prompt: Whether to save system prompts
            save_user_prompt: Whether to save user prompts
            save_response_raw: Whether to save raw LLM responses (verbose)
            save_response_parsed: Whether to save parsed responses
            separate_files: Whether to save separate files per agent/iteration
        """
        self.story_id = story_id
        self.output_dir = Path(output_dir)
        self.save_system_prompt = save_system_prompt
        self.save_user_prompt = save_user_prompt
        self.save_response_raw = save_response_raw
        self.save_response_parsed = save_response_parsed
        self.separate_files = separate_files

        # Tracking
        self.start_time = time.time()
        self.api_calls: List[Dict[str, Any]] = []

        # Create prompts directory
        self.prompts_dir = self.output_dir / story_id / "prompts"
        self.prompts_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "prompt_logger_initialized",
            story_id=story_id,
            prompts_dir=str(self.prompts_dir),
            save_raw_responses=save_response_raw
        )

    def log_prompt(
        self,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
        response_parsed: Any,
        token_usage: Optional[Dict[str, int]],
        execution_time: float,
        model: str,
        temperature: float,
        max_tokens: Optional[int],
        response_raw: Optional[str] = None,
        iteration: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log a single LLM prompt and response.

        Args:
            agent_name: Name of the agent making the call
            system_prompt: System prompt sent to LLM
            user_prompt: User prompt sent to LLM
            response_parsed: Parsed response (Pydantic model)
            token_usage: Token usage from API (prompt_tokens, completion_tokens, total_tokens)
            execution_time: Time taken for API call in seconds
            model: Model name (e.g., "gpt-5")
            temperature: Temperature setting
            max_tokens: Max tokens setting
            response_raw: Raw LLM response (optional, verbose)
            iteration: Iteration number (for recursive agents)
            context: Additional context (scene_id, etc.)
        """
        timestamp = datetime.now().isoformat()

        # Build log entry
        log_entry = {
            "agent": agent_name,
            "timestamp": timestamp,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "execution_time_seconds": round(execution_time, 3)
        }

        # Add iteration if provided
        if iteration is not None:
            log_entry["iteration"] = iteration

        # Add context if provided (e.g., scene_id for SceneDetailAgent)
        # Only save simple types (strings, numbers, boolists) - skip Pydantic models
        if context:
            sanitized_context = {}
            for key, value in context.items():
                # Only include JSON-serializable values
                if isinstance(value, (str, int, float, bool, type(None))):
                    sanitized_context[key] = value
                elif isinstance(value, (list, tuple)):
                    # Include lists/tuples of simple types
                    if all(isinstance(item, (str, int, float, bool, type(None))) for item in value):
                        sanitized_context[key] = list(value)
                elif isinstance(value, dict):
                    # Include simple dicts (but not those containing Pydantic models)
                    try:
                        json.dumps(value)  # Test if it's JSON-serializable
                        sanitized_context[key] = value
                    except (TypeError, ValueError):
                        # Skip non-serializable dicts
                        pass
            if sanitized_context:
                log_entry["context"] = sanitized_context

        # Add prompts
        if self.save_system_prompt:
            log_entry["system_prompt"] = system_prompt
        if self.save_user_prompt:
            log_entry["user_prompt"] = user_prompt

        # Add responses
        if self.save_response_parsed:
            # Convert Pydantic model to dict (mode='json' for recursive serialization)
            if hasattr(response_parsed, 'model_dump'):
                log_entry["response_parsed"] = response_parsed.model_dump(mode='json')
            elif hasattr(response_parsed, 'dict'):
                log_entry["response_parsed"] = response_parsed.dict(mode='json')
            else:
                log_entry["response_parsed"] = str(response_parsed)

        if self.save_response_raw and response_raw:
            log_entry["response_raw"] = response_raw

        # Add token usage
        if token_usage:
            log_entry["token_usage"] = token_usage

        # Store for summary
        self.api_calls.append({
            "agent": agent_name,
            "timestamp": timestamp,
            "tokens": token_usage,
            "duration": execution_time,
            "iteration": iteration,
            "context": context
        })

        # Save individual file if enabled
        if self.separate_files:
            self._save_individual_file(agent_name, log_entry, iteration, context)

        logger.info(
            "prompt_logged",
            agent=agent_name,
            iteration=iteration,
            tokens=token_usage.get("total_tokens") if token_usage else None,
            duration=round(execution_time, 3)
        )

    def _save_individual_file(
        self,
        agent_name: str,
        log_entry: Dict[str, Any],
        iteration: Optional[int],
        context: Optional[Dict[str, Any]]
    ) -> None:
        """Save individual prompt log file."""
        # Create agent-specific directory
        agent_dir = self.prompts_dir / agent_name
        agent_dir.mkdir(parents=True, exist_ok=True)

        # Determine filename
        if iteration is not None:
            filename = f"iteration_{iteration}.json"
        elif context and "scene_id" in context:
            filename = f"{context['scene_id']}.json"
        else:
            # Use timestamp for unique filename
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"{timestamp_str}.json"

        filepath = agent_dir / filename

        # Save JSON
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(log_entry, f, indent=2, ensure_ascii=False)

        logger.debug("prompt_file_saved", filepath=str(filepath))

    def get_summary(self) -> Dict[str, Any]:
        """
        Calculate aggregate statistics across all API calls.

        Returns:
            Dictionary with summary statistics
        """
        end_time = time.time()
        total_duration = end_time - self.start_time

        # Initialize aggregates
        total_tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        by_agent: Dict[str, Dict[str, Any]] = {}

        # Aggregate by agent
        for call in self.api_calls:
            agent = call["agent"]

            if agent not in by_agent:
                by_agent[agent] = {
                    "api_calls": 0,
                    "total_tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "total_duration_seconds": 0.0,
                    "durations": []
                }

            # Count API call
            by_agent[agent]["api_calls"] += 1

            # Add tokens
            if call["tokens"]:
                for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
                    by_agent[agent]["total_tokens"][key] += call["tokens"].get(key, 0)
                    total_tokens[key] += call["tokens"].get(key, 0)

            # Add duration
            by_agent[agent]["total_duration_seconds"] += call["duration"]
            by_agent[agent]["durations"].append(call["duration"])

        # Calculate averages
        for agent_data in by_agent.values():
            if agent_data["durations"]:
                agent_data["average_duration_seconds"] = round(
                    sum(agent_data["durations"]) / len(agent_data["durations"]), 3
                )
                del agent_data["durations"]  # Remove raw durations from summary
            agent_data["total_duration_seconds"] = round(agent_data["total_duration_seconds"], 3)

        # Build summary
        summary = {
            "story_id": self.story_id,
            "generation_start": datetime.fromtimestamp(self.start_time).isoformat(),
            "generation_end": datetime.fromtimestamp(end_time).isoformat(),
            "total_duration_seconds": round(total_duration, 3),
            "total_api_calls": len(self.api_calls),
            "total_tokens": total_tokens,
            "by_agent": by_agent
        }

        # Add cost estimation (optional, based on hypothetical GPT-5 pricing)
        # These rates are placeholders - update with actual pricing when available
        prompt_cost_per_1k = 0.01  # $0.01 per 1K prompt tokens (placeholder)
        completion_cost_per_1k = 0.02  # $0.02 per 1K completion tokens (placeholder)

        prompt_cost = (total_tokens["prompt_tokens"] / 1000) * prompt_cost_per_1k
        completion_cost = (total_tokens["completion_tokens"] / 1000) * completion_cost_per_1k

        summary["estimated_cost_usd"] = {
            "note": "Based on placeholder pricing (update with actual GPT-5 rates)",
            "prompt_cost": round(prompt_cost, 4),
            "completion_cost": round(completion_cost, 4),
            "total_cost": round(prompt_cost + completion_cost, 4)
        }

        return summary

    def save_summary(self) -> None:
        """Save aggregate summary to prompts_summary.json."""
        summary = self.get_summary()

        summary_path = self.output_dir / self.story_id / "prompts_summary.json"

        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        logger.info(
            "prompt_summary_saved",
            path=str(summary_path),
            total_api_calls=summary["total_api_calls"],
            total_tokens=summary["total_tokens"]["total_tokens"],
            total_duration=summary["total_duration_seconds"]
        )

"""
Batch reporter for generating reports and summaries.

This module generates comprehensive markdown reports, JSON summaries, and
statistics for batch story generation and simulation runs.
"""

import json
import structlog
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from batch.schemas import BatchState, StoryStatus

logger = structlog.get_logger(__name__)


class BatchReporter:
    """Generates batch reports and summaries."""

    def __init__(self, batch_state: BatchState):
        """
        Initialize batch reporter.

        Args:
            batch_state: Batch state to report on
        """
        self.batch_state = batch_state

        logger.info(
            "batch_reporter_initialized",
            batch_id=batch_state.batch_id
        )

    def generate_markdown_report(self) -> str:
        """
        Generate comprehensive markdown report.

        Returns:
            Markdown report string
        """
        report_lines = []

        # Header
        report_lines.append("# Batch Generation Report\n")
        report_lines.append(f"**Batch ID:** {self.batch_state.batch_id}\n")
        report_lines.append(f"**Started:** {self._format_timestamp(self.batch_state.started_at)}\n")

        if self.batch_state.completed_at:
            report_lines.append(f"**Completed:** {self._format_timestamp(self.batch_state.completed_at)}\n")
            duration = self._calculate_duration(
                self.batch_state.started_at,
                self.batch_state.completed_at
            )
            report_lines.append(f"**Duration:** {duration}\n")

        report_lines.append("\n---\n")

        # Summary
        report_lines.append("\n## Summary\n\n")
        report_lines.append(f"- **Total Stories:** {len(self.batch_state.stories)}\n")
        report_lines.append(f"- **Successful:** {self.batch_state.success_count} "
                            f"({self._percentage(self.batch_state.success_count, len(self.batch_state.stories))})\n")
        report_lines.append(f"- **Failed:** {self.batch_state.failure_count} "
                            f"({self._percentage(self.batch_state.failure_count, len(self.batch_state.stories))})\n")

        if self.batch_state.failure_count > 0:
            gen_failures = sum(1 for s in self.batch_state.stories
                               if s.status == 'failed' and s.current_phase < 3)
            sim_failures = sum(1 for s in self.batch_state.stories
                               if s.status == 'failed' and s.current_phase == 3)
            report_lines.append(f"  - Generation failures: {gen_failures}\n")
            report_lines.append(f"  - Simulation failures: {sim_failures}\n")

        report_lines.append("\n")

        # Configuration
        report_lines.append("## Configuration\n\n")
        config = self.batch_state.config
        report_lines.append(f"- **Max Protagonists:** {config.max_num_protagonists}\n")
        report_lines.append(f"- **Max Extras:** {config.max_num_extras}\n")
        report_lines.append(f"- **Distinct Actions:** {config.num_distinct_actions}\n")
        report_lines.append(f"- **Scenes per Story:** {config.scene_number}\n")
        report_lines.append(f"- **Generation Variations:** {config.same_story_generation_variations}\n")
        report_lines.append(f"- **Simulation Variations:** {config.same_story_simulation_variations}\n")

        if config.narrative_seeds:
            report_lines.append(f"- **Seeds:** {config.narrative_seeds}\n")

        report_lines.append("\n")

        # Retry Statistics
        if self.batch_state.total_generation_retries > 0 or self.batch_state.total_simulation_retries > 0:
            report_lines.append("## Retry Statistics\n\n")
            report_lines.append(f"- **Total Generation Retries:** {self.batch_state.total_generation_retries}\n")

            if self.batch_state.phase_retry_counts:
                for phase, count in sorted(self.batch_state.phase_retry_counts.items()):
                    if count > 0:
                        report_lines.append(f"  - Phase {phase}: {count}\n")

            report_lines.append(f"- **Total Simulation Retries:** {self.batch_state.total_simulation_retries}\n")
            report_lines.append("\n")

        # Successful Stories
        successful_stories = [s for s in self.batch_state.stories if s.status == 'success']
        if successful_stories:
            report_lines.append("## Successful Stories\n\n")
            report_lines.append("| Story # | Story ID | Scenes | Events | Simulations | Duration |\n")
            report_lines.append("|---------|----------|--------|--------|-------------|----------|\n")

            for story in successful_stories:
                story_num = f"{story.story_number:05d}"
                story_id = story.story_id
                scenes = story.scene_count or "N/A"
                events = story.event_count or "N/A"
                sims = len(story.successful_simulations)
                duration = self._calculate_duration(story.started_at, story.completed_at) if story.completed_at else "N/A"

                report_lines.append(
                    f"| {story_num} | {story_id} | {scenes} | {events} | {sims} | {duration} |\n"
                )

            report_lines.append("\n")

        # Failed Stories
        failed_stories = [s for s in self.batch_state.stories if s.status == 'failed']
        if failed_stories:
            report_lines.append("## Failed Stories\n\n")

            for story in failed_stories:
                report_lines.append(f"### Story {story.story_number:05d} ({story.story_id})\n\n")

                # Determine failure type
                if story.current_phase < 3:
                    failure_type = "Generation Failure"
                    phase_name = ["", "Concept", "Casting", "Detail"][story.current_phase] if story.current_phase else "Unknown"
                    report_lines.append(f"- **Type:** {failure_type} (Phase {story.current_phase}: {phase_name})\n")
                else:
                    failure_type = "Simulation Failure"
                    report_lines.append(f"- **Type:** {failure_type}\n")

                # Attempts
                if story.generation_attempts:
                    report_lines.append(f"- **Generation Attempts:** {story.generation_attempts}\n")
                if story.simulation_attempts > 0:
                    report_lines.append(f"- **Simulation Attempts:** {story.simulation_attempts}\n")

                # Errors
                if story.errors:
                    report_lines.append("- **Errors:**\n")
                    for error in story.errors[-3:]:  # Show last 3 errors
                        report_lines.append(f"  - {error}\n")

                # Warnings
                if story.warnings:
                    report_lines.append("- **Warnings:**\n")
                    for warning in story.warnings[-3:]:  # Show last 3 warnings
                        report_lines.append(f"  - {warning}\n")

                report_lines.append("\n")

        # Artifacts
        report_lines.append("## Artifacts\n\n")
        report_lines.append(f"- **Output Directory:** `{self.batch_state.batch_output_dir}/`\n")

        # Calculate total size
        total_size_mb = self._calculate_total_size()
        if total_size_mb:
            report_lines.append(f"- **Total Size:** {total_size_mb:.1f} MB\n")

        if self.batch_state.drive_folder_link:
            report_lines.append(f"- **Google Drive:** [{self.batch_state.drive_folder_link}]({self.batch_state.drive_folder_link})\n")

        report_lines.append("\n")

        # Recommendations
        if failed_stories:
            report_lines.append("## Recommendations\n\n")

            # Analyze failure patterns
            gen_failures = [s for s in failed_stories if s.current_phase < 3]
            sim_failures = [s for s in failed_stories if s.current_phase == 3]

            if gen_failures:
                report_lines.append(f"- **{len(gen_failures)} generation failure(s):** Review error logs for common patterns\n")
                # Identify common error types
                common_errors = self._identify_common_errors(gen_failures)
                for error_type, count in common_errors.items():
                    report_lines.append(f"  - {error_type}: {count} occurrence(s)\n")

            if sim_failures:
                report_lines.append(f"- **{len(sim_failures)} simulation failure(s):** Consider increasing timeout or checking GEST validity\n")

                # Check for timeout patterns
                timeout_count = sum(1 for s in sim_failures
                                    if any('timeout' in str(e).lower() for e in s.errors))
                if timeout_count > 0:
                    report_lines.append(f"  - {timeout_count} timeout(s) detected - consider increasing `simulation_timeout`\n")

            report_lines.append("\n")

        # Footer
        report_lines.append("---\n\n")
        report_lines.append("*Report generated by Multiagent Story Generation System*\n")

        return "".join(report_lines)

    def generate_json_summary(self) -> Dict[str, Any]:
        """
        Generate JSON summary of batch results.

        Returns:
            Dictionary with batch summary
        """
        summary = {
            'batch_id': self.batch_state.batch_id,
            'started_at': self.batch_state.started_at,
            'completed_at': self.batch_state.completed_at,
            'duration_seconds': self._calculate_duration_seconds(
                self.batch_state.started_at,
                self.batch_state.completed_at
            ) if self.batch_state.completed_at else None,
            'configuration': self.batch_state.config.to_dict(),
            'statistics': {
                'total_stories': len(self.batch_state.stories),
                'successful': self.batch_state.success_count,
                'failed': self.batch_state.failure_count,
                'success_rate': self._percentage(
                    self.batch_state.success_count,
                    len(self.batch_state.stories)
                ),
            },
            'retry_statistics': {
                'total_generation_retries': self.batch_state.total_generation_retries,
                'total_simulation_retries': self.batch_state.total_simulation_retries,
                'phase_retries': self.batch_state.phase_retry_counts,
            },
            'stories': [
                self._story_summary(story)
                for story in self.batch_state.stories
            ],
            'output': {
                'directory': self.batch_state.batch_output_dir,
                'drive_folder_id': self.batch_state.drive_folder_id,
                'drive_folder_link': self.batch_state.drive_folder_link,
            }
        }

        return summary

    def _story_summary(self, story: StoryStatus) -> Dict[str, Any]:
        """Create summary for a single story."""
        return {
            'story_number': story.story_number,
            'story_id': story.story_id,
            'status': story.status,
            'scenes': story.scene_count,
            'events': story.event_count,
            'takes_generated': story.current_take,
            'simulations_successful': len(story.successful_simulations),
            'generation_attempts': story.generation_attempts,
            'simulation_attempts': story.simulation_attempts,
            'has_errors': len(story.errors) > 0,
            'has_warnings': len(story.warnings) > 0,
            'started_at': story.started_at,
            'completed_at': story.completed_at,
        }

    def save_reports(self, output_dir: Path) -> Dict[str, Path]:
        """
        Save all reports to disk.

        Args:
            output_dir: Directory to save reports

        Returns:
            Dictionary mapping report names to paths
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        saved_reports = {}

        try:
            # Save markdown report
            markdown_path = output_dir / "batch_report.md"
            with open(markdown_path, 'w', encoding='utf-8') as f:
                f.write(self.generate_markdown_report())
            saved_reports['markdown'] = markdown_path

            logger.info(
                "markdown_report_saved",
                batch_id=self.batch_state.batch_id,
                path=str(markdown_path)
            )

            # Save JSON summary
            json_path = output_dir / "batch_summary.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(self.generate_json_summary(), f, indent=2)
            saved_reports['json'] = json_path

            logger.info(
                "json_summary_saved",
                batch_id=self.batch_state.batch_id,
                path=str(json_path)
            )

        except Exception as e:
            logger.error(
                "report_save_failed",
                batch_id=self.batch_state.batch_id,
                error=str(e),
                exc_info=True
            )

        return saved_reports

    def _format_timestamp(self, timestamp_str: str) -> str:
        """Format ISO timestamp to human-readable string."""
        try:
            dt = datetime.fromisoformat(timestamp_str)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            return timestamp_str

    def _calculate_duration(self, start_str: str, end_str: Optional[str]) -> str:
        """Calculate and format duration between two timestamps."""
        if not end_str:
            return "In progress"

        try:
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str)
            duration = end - start

            hours, remainder = divmod(duration.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            parts = []
            if duration.days > 0:
                parts.append(f"{duration.days}d")
            if hours > 0:
                parts.append(f"{hours}h")
            if minutes > 0:
                parts.append(f"{minutes}m")
            if seconds > 0 or not parts:
                parts.append(f"{seconds}s")

            return " ".join(parts)

        except:
            return "Unknown"

    def _calculate_duration_seconds(self, start_str: str, end_str: Optional[str]) -> Optional[float]:
        """Calculate duration in seconds."""
        if not end_str:
            return None

        try:
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str)
            return (end - start).total_seconds()
        except:
            return None

    def _percentage(self, part: int, total: int) -> str:
        """Calculate and format percentage."""
        if total == 0:
            return "0%"
        return f"{(part / total * 100):.1f}%"

    def _calculate_total_size(self) -> Optional[float]:
        """Calculate total size of batch output in MB."""
        try:
            output_path = Path(self.batch_state.batch_output_dir)
            if not output_path.exists():
                return None

            total_bytes = sum(
                f.stat().st_size
                for f in output_path.rglob('*')
                if f.is_file()
            )

            return total_bytes / (1024 * 1024)

        except Exception as e:
            logger.error(
                "size_calculation_failed",
                error=str(e)
            )
            return None

    def _identify_common_errors(self, failed_stories: List[StoryStatus]) -> Dict[str, int]:
        """Identify common error patterns across failed stories."""
        error_patterns = {
            'Pydantic validation': 0,
            'Budget violation': 0,
            'Temporal validation': 0,
            'API error': 0,
            'Timeout': 0,
            'Other': 0,
        }

        for story in failed_stories:
            for error in story.errors:
                error_lower = error.lower()
                if 'pydantic' in error_lower or 'validation' in error_lower:
                    error_patterns['Pydantic validation'] += 1
                elif 'budget' in error_lower or 'exceeded' in error_lower:
                    error_patterns['Budget violation'] += 1
                elif 'temporal' in error_lower or 'orphaned' in error_lower:
                    error_patterns['Temporal validation'] += 1
                elif 'api' in error_lower or 'openai' in error_lower or 'rate' in error_lower:
                    error_patterns['API error'] += 1
                elif 'timeout' in error_lower:
                    error_patterns['Timeout'] += 1
                else:
                    error_patterns['Other'] += 1

        # Filter out zero counts
        return {k: v for k, v in error_patterns.items() if v > 0}

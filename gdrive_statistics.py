"""
Google Drive Batch Statistics Collector

Connects to Google Drive, traverses batch folder structure, and computes
distribution statistics across all generated stories.

Usage:
    python gdrive_statistics.py --output stats.json --verbose
    python gdrive_statistics.py --folder-id YOUR_FOLDER_ID --output stats.json
"""

import argparse
import json
import statistics
import io
from collections import Counter
from pathlib import Path
from typing import Dict, List, Any, Optional, Iterator, Tuple
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger(__name__)

# Import Google Drive dependencies
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseDownload
    GOOGLE_DRIVE_AVAILABLE = True
except ImportError:
    GOOGLE_DRIVE_AVAILABLE = False
    logger.warning("google_drive_dependencies_not_available",
                   message="Install with: pip install google-auth google-api-python-client google-auth-oauthlib")


# Episode to regions mapping (for inferring episode from regions in GEST)
EPISODE_REGIONS = {
    "classroom1": ["hallway", "classroom"],
    "garden": ["porch", "garden", "driveway", "street"],
    "office": ["office"],
    "gym3": ["right part of the gym room", "left part of the gym room"],
    "common": ["livingroom", "bedroom", "bathroom"],
    "house9": ["livingroom", "hallway", "kitchen", "barroom", "second floor hallway",
               "bedroom", "bathroom", "stairs", "nearDoor"],
    "gym1_a": ["gym main room"],
    "gym2_a": ["gym main room", "gym backroom"],
    "office2": ["office"],
}

# Build reverse mapping: region -> episode
REGION_TO_EPISODE = {}
for episode, regions in EPISODE_REGIONS.items():
    for region in regions:
        if region not in REGION_TO_EPISODE:
            REGION_TO_EPISODE[region] = episode


@dataclass
class StoryStats:
    """Statistics extracted from a single story"""
    actors: int = 0
    events: int = 0
    temporal_relations: int = 0
    regions: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    genders: List[int] = field(default_factory=list)
    object_types: List[str] = field(default_factory=list)
    action_categories: List[str] = field(default_factory=list)
    temporal_relation_types: List[str] = field(default_factory=list)


class GESTStatisticsExtractor:
    """Extracts statistics from a GEST JSON structure"""

    # Actions that are interactions (require 2 entities)
    INTERACTION_ACTIONS = {
        "Kiss", "Hug", "Talk", "Laugh", "Wave", "LookAt", "Argue",
        "Give", "Receive", "INV-Give", "INV-Receive", "Handshake",
        "HighFive", "Punch", "Slap", "Push"
    }

    def extract(self, gest: Dict[str, Any]) -> StoryStats:
        """Extract all statistics from a GEST structure"""
        stats = StoryStats()

        # Reserved keys that aren't events
        reserved_keys = {"temporal", "spatial", "semantic", "logical", "camera"}

        # Extract from events (all top-level keys except reserved)
        for key, value in gest.items():
            if key in reserved_keys:
                continue
            if not isinstance(value, dict) or "Action" not in value:
                continue

            action = value.get("Action", "")
            entities = value.get("Entities", [])
            location = value.get("Location", [])
            properties = value.get("Properties", {})

            # Count actors (Exists events with Gender property)
            if action == "Exists" and "Gender" in properties:
                stats.actors += 1
                stats.genders.append(properties["Gender"])

            # Count object types (Exists events with Type property)
            elif action == "Exists" and "Type" in properties:
                stats.object_types.append(properties["Type"])

            # Count action events (non-Exists)
            elif action != "Exists":
                stats.events += 1
                stats.actions.append(action)

                # Categorize action
                if len(entities) >= 2 or action in self.INTERACTION_ACTIONS:
                    stats.action_categories.append("interaction")
                else:
                    stats.action_categories.append("simple_action")

            # Collect regions
            if location:
                for loc in location:
                    if loc and loc not in stats.regions:
                        stats.regions.append(loc)

        # Extract temporal relations
        temporal = gest.get("temporal", {})
        for key, value in temporal.items():
            if isinstance(value, dict) and "type" in value:
                stats.temporal_relations += 1
                stats.temporal_relation_types.append(value["type"])

        return stats


class GDriveStatisticsCollector:
    """Collects statistics from Google Drive batch folders"""

    # Use same scope as batch uploader (drive.file allows access to app-created files)
    SCOPES = ['https://www.googleapis.com/auth/drive.file']

    def __init__(self, credentials_path: str = "credentials/google_drive_credentials.json",
                 token_path: str = "credentials/token.json"):
        if not GOOGLE_DRIVE_AVAILABLE:
            raise ImportError("Google Drive dependencies not installed")

        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Authenticate with Google Drive API"""
        creds = None

        if Path(self.token_path).exists():
            creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, self.SCOPES)
                creds = flow.run_local_server(port=0)

            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())

        self.service = build('drive', 'v3', credentials=creds)
        logger.info("gdrive_authenticated")

    def list_folders(self, parent_id: str) -> List[Dict[str, str]]:
        """List subfolders in a folder"""
        query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"

        results = self.service.files().list(
            q=query,
            fields="files(id, name)",
            pageSize=1000
        ).execute()

        return results.get('files', [])

    def find_detail_gest(self, story_folder_id: str) -> Optional[str]:
        """Find detail_gest.json in story folder structure (detail/take1/detail_gest.json)"""
        try:
            # Look for 'detail' folder
            detail_folders = self.list_folders(story_folder_id)
            detail_folder = next((f for f in detail_folders if f['name'] == 'detail'), None)
            if not detail_folder:
                return None

            # Look for 'take1' folder
            take_folders = self.list_folders(detail_folder['id'])
            take_folder = next((f for f in take_folders if f['name'] == 'take1'), None)
            if not take_folder:
                return None

            # Look for detail_gest.json file
            query = f"'{take_folder['id']}' in parents and name='detail_gest.json' and trashed=false"
            results = self.service.files().list(q=query, fields="files(id)").execute()
            files = results.get('files', [])

            if files:
                return files[0]['id']
            return None

        except Exception as e:
            logger.debug("find_detail_gest_error", error=str(e))
            return None

    def download_file_content(self, file_id: str) -> Optional[str]:
        """Download file content as string"""
        try:
            request = self.service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)

            done = False
            while not done:
                _, done = downloader.next_chunk()

            fh.seek(0)
            return fh.read().decode('utf-8')

        except Exception as e:
            logger.debug("download_error", file_id=file_id, error=str(e))
            return None

    def traverse_batches(self, root_folder_id: str, verbose: bool = False) -> Iterator[Tuple[str, str, Dict]]:
        """
        Traverse all batches and yield (batch_name, story_name, gest_dict) tuples
        """
        # Get all batch folders
        batch_folders = self.list_folders(root_folder_id)
        batch_folders = [f for f in batch_folders if f['name'].startswith('batch_')]
        batch_folders.sort(key=lambda x: x['name'])

        if verbose:
            print(f"Found {len(batch_folders)} batch folders")

        for batch_idx, batch_folder in enumerate(batch_folders):
            batch_name = batch_folder['name']
            if verbose:
                print(f"\n[{batch_idx + 1}/{len(batch_folders)}] Processing {batch_name}...")

            # Get story folders
            story_folders = self.list_folders(batch_folder['id'])
            story_folders = [f for f in story_folders if f['name'].startswith('story_')]
            story_folders.sort(key=lambda x: x['name'])

            for story_folder in story_folders:
                story_name = story_folder['name']

                # Find and download detail_gest.json
                gest_file_id = self.find_detail_gest(story_folder['id'])
                if not gest_file_id:
                    if verbose:
                        print(f"  Skipping {story_name} - no detail_gest.json found")
                    continue

                content = self.download_file_content(gest_file_id)
                if not content:
                    continue

                try:
                    gest = json.loads(content)
                    yield batch_name, story_name, gest
                except json.JSONDecodeError:
                    logger.warning("invalid_json", batch=batch_name, story=story_name)
                    continue

    def upload_file(self, file_path: Path, parent_folder_id: str, filename: Optional[str] = None) -> Optional[str]:
        """
        Upload a file to Google Drive.

        Args:
            file_path: Local path to file
            parent_folder_id: Google Drive folder ID to upload to
            filename: Optional filename override

        Returns:
            File ID if successful, None otherwise
        """
        from googleapiclient.http import MediaFileUpload

        try:
            file_metadata = {
                'name': filename or file_path.name,
                'parents': [parent_folder_id]
            }

            media = MediaFileUpload(
                str(file_path),
                mimetype='application/json',
                resumable=True
            )

            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()

            file_id = file.get('id')
            link = file.get('webViewLink')

            logger.info("file_uploaded", file_id=file_id, link=link)
            return file_id

        except Exception as e:
            logger.error("upload_failed", error=str(e), exc_info=True)
            return None

    def get_file_link(self, file_id: str) -> Optional[str]:
        """Get shareable link for a file"""
        try:
            # Make file publicly readable
            self.service.permissions().create(
                fileId=file_id,
                body={'type': 'anyone', 'role': 'reader'}
            ).execute()

            file = self.service.files().get(
                fileId=file_id,
                fields='webViewLink'
            ).execute()

            return file.get('webViewLink')
        except Exception as e:
            logger.error("get_link_failed", error=str(e))
            return None


class StatisticsAggregator:
    """Aggregates statistics across multiple stories"""

    def __init__(self):
        self.total_batches = set()
        self.total_stories = 0
        self.total_events = 0
        self.total_temporal_relations = 0

        # Per-story metrics
        self.actors_per_story: List[int] = []
        self.events_per_story: List[int] = []
        self.temporal_relations_per_story: List[int] = []

        # Counters for distributions
        self.regions = Counter()
        self.episodes = Counter()
        self.actions = Counter()
        self.action_categories = Counter()
        self.genders = Counter()
        self.object_types = Counter()
        self.temporal_relation_types = Counter()

        # Unique sets
        self.unique_actions = set()
        self.unique_object_types = set()
        self.unique_regions = set()

    def add_story(self, batch_name: str, story_stats: StoryStats):
        """Add a story's statistics to the aggregator"""
        self.total_batches.add(batch_name)
        self.total_stories += 1
        self.total_events += story_stats.events
        self.total_temporal_relations += story_stats.temporal_relations

        # Per-story metrics
        self.actors_per_story.append(story_stats.actors)
        self.events_per_story.append(story_stats.events)
        self.temporal_relations_per_story.append(story_stats.temporal_relations)

        # Distributions
        for region in story_stats.regions:
            self.regions[region] += 1
            self.unique_regions.add(region)
            # Infer episode from region
            episode = REGION_TO_EPISODE.get(region, "unknown")
            self.episodes[episode] += 1

        for action in story_stats.actions:
            self.actions[action] += 1
            self.unique_actions.add(action)

        for category in story_stats.action_categories:
            self.action_categories[category] += 1

        for gender in story_stats.genders:
            gender_name = "male" if gender == 1 else "female" if gender == 2 else f"unknown_{gender}"
            self.genders[gender_name] += 1

        for obj_type in story_stats.object_types:
            self.object_types[obj_type] += 1
            self.unique_object_types.add(obj_type)

        for rel_type in story_stats.temporal_relation_types:
            self.temporal_relation_types[rel_type] += 1

    def _compute_stats(self, values: List[int]) -> Dict[str, float]:
        """Compute min, max, mean, std for a list of values"""
        if not values:
            return {"mean": 0, "min": 0, "max": 0, "std": 0}
        return {
            "mean": round(statistics.mean(values), 2),
            "min": min(values),
            "max": max(values),
            "std": round(statistics.stdev(values) if len(values) > 1 else 0, 2)
        }

    def _counter_to_distribution(self, counter: Counter) -> Dict[str, Dict[str, Any]]:
        """Convert counter to distribution with percentages"""
        total = sum(counter.values())
        if total == 0:
            return {}

        result = {}
        for key, count in counter.most_common():
            result[key] = {
                "count": count,
                "percentage": round(100 * count / total, 2)
            }
        return result

    def to_dict(self) -> Dict[str, Any]:
        """Export aggregated statistics as dictionary"""
        return {
            "summary": {
                "total_batches": len(self.total_batches),
                "total_stories": self.total_stories,
                "total_events": self.total_events,
                "total_temporal_relations": self.total_temporal_relations,
                "unique_actions": len(self.unique_actions),
                "unique_object_types": len(self.unique_object_types),
                "unique_regions": len(self.unique_regions)
            },
            "per_story_averages": {
                "actors_per_story": self._compute_stats(self.actors_per_story),
                "events_per_story": self._compute_stats(self.events_per_story),
                "temporal_relations_per_story": self._compute_stats(self.temporal_relations_per_story)
            },
            "distributions": {
                "regions": self._counter_to_distribution(self.regions),
                "episodes": self._counter_to_distribution(self.episodes),
                "actions": self._counter_to_distribution(self.actions),
                "action_categories": self._counter_to_distribution(self.action_categories),
                "genders": self._counter_to_distribution(self.genders),
                "object_types": self._counter_to_distribution(self.object_types),
                "temporal_relation_types": self._counter_to_distribution(self.temporal_relation_types)
            },
            "unique_values": {
                "actions": sorted(self.unique_actions),
                "object_types": sorted(self.unique_object_types),
                "regions": sorted(self.unique_regions)
            }
        }


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Compute statistics from Google Drive batch folders"
    )
    parser.add_argument(
        "--folder-id",
        default="null",
        help="Google Drive root folder ID (default: configured folder)"
    )
    parser.add_argument(
        "--output",
        default="batch_statistics.json",
        help="Output file path (JSON)"
    )
    parser.add_argument(
        "--credentials",
        default="credentials/google_drive_credentials.json",
        help="Path to Google Drive credentials"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show progress"
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload statistics file to Google Drive root folder"
    )

    args = parser.parse_args()

    if not GOOGLE_DRIVE_AVAILABLE:
        print("Error: Google Drive dependencies not installed.")
        print("Install with: pip install google-auth google-api-python-client google-auth-oauthlib")
        return 1

    try:
        # Initialize collector
        print("Authenticating with Google Drive...")
        collector = GDriveStatisticsCollector(credentials_path=args.credentials)

        # Initialize extractor and aggregator
        extractor = GESTStatisticsExtractor()
        aggregator = StatisticsAggregator()

        # Traverse and collect statistics
        print(f"\nCollecting statistics from folder: {args.folder_id}")

        story_count = 0
        for batch_name, story_name, gest in collector.traverse_batches(args.folder_id, verbose=args.verbose):
            story_stats = extractor.extract(gest)
            aggregator.add_story(batch_name, story_stats)
            story_count += 1

            if args.verbose and story_count % 50 == 0:
                print(f"  Processed {story_count} stories...")

        # Generate output
        result = aggregator.to_dict()

        # Save to file
        output_path = Path(args.output)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)

        print(f"\n{'='*60}")
        print(f"Statistics saved to: {output_path}")
        print(f"{'='*60}")
        print(f"\nSummary:")
        print(f"  Total batches: {result['summary']['total_batches']}")
        print(f"  Total stories: {result['summary']['total_stories']}")
        print(f"  Total events: {result['summary']['total_events']}")
        print(f"  Total temporal relations: {result['summary']['total_temporal_relations']}")
        print(f"  Unique actions: {result['summary']['unique_actions']}")
        print(f"  Unique object types: {result['summary']['unique_object_types']}")
        print(f"  Unique regions: {result['summary']['unique_regions']}")

        print(f"\nPer-story averages:")
        for metric, stats in result['per_story_averages'].items():
            print(f"  {metric}: mean={stats['mean']}, min={stats['min']}, max={stats['max']}")

        print(f"\nTop 5 actions:")
        for action, data in list(result['distributions']['actions'].items())[:5]:
            print(f"  {action}: {data['count']} ({data['percentage']}%)")

        print(f"\nGender distribution:")
        for gender, data in result['distributions']['genders'].items():
            print(f"  {gender}: {data['count']} ({data['percentage']}%)")

        # Upload to Google Drive if requested
        if args.upload:
            print(f"\nUploading to Google Drive...")
            file_id = collector.upload_file(output_path, args.folder_id)
            if file_id:
                link = collector.get_file_link(file_id)
                print(f"  Uploaded successfully!")
                print(f"  File ID: {file_id}")
                if link:
                    print(f"  Link: {link}")
            else:
                print(f"  Upload failed!")

        return 0

    except Exception as e:
        logger.error("statistics_collection_failed", error=str(e), exc_info=True)
        print(f"\nError: {e}")
        return 1


if __name__ == "__main__":
    exit(main())

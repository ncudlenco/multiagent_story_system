"""
Google Drive Integration Manager for VMware Orchestrator

Handles Google Drive authentication, subfolder creation, and credential
distribution to worker VMs for batch story generation.
"""

import io
import os
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import structlog

logger = structlog.get_logger(__name__)

# Google API imports (optional - graceful degradation if not installed)
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload
    GOOGLE_DRIVE_AVAILABLE = True
except ImportError:
    GOOGLE_DRIVE_AVAILABLE = False
    logger.warning("google_drive_dependencies_not_available",
                   message="Install with: pip install google-auth google-api-python-client google-auth-oauthlib")


class GDriveManager:
    """Manages Google Drive integration for VMware orchestration"""

    # OAuth scopes for Drive API (limited to app-created files)
    SCOPES = ['https://www.googleapis.com/auth/drive.file']

    def __init__(self, credentials_path: str = "credentials/google_drive_credentials.json",
                 token_path: str = "credentials/token.json"):
        """
        Initialize Google Drive manager

        Args:
            credentials_path: Path to OAuth client secrets JSON
            token_path: Path to cached token JSON
        """
        if not GOOGLE_DRIVE_AVAILABLE:
            raise ImportError("Google Drive dependencies not installed. "
                            "Install with: pip install google-auth google-api-python-client google-auth-oauthlib")

        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
        self.creds = None

        logger.info("gdrive_manager_initialized",
                   credentials_path=credentials_path,
                   token_path=token_path)

    def authenticate(self, force_reauth: bool = False) -> bool:
        """
        Authenticate with Google Drive API

        Args:
            force_reauth: Force re-authentication even if token exists

        Returns:
            bool: True if authentication successful, False otherwise
        """
        try:
            # Load cached token if exists
            if not force_reauth and os.path.exists(self.token_path):
                logger.info("loading_cached_token", token_path=self.token_path)
                self.creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)

            # Refresh expired token or run OAuth flow
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    logger.info("refreshing_expired_token")
                    self.creds.refresh(Request())
                else:
                    if not os.path.exists(self.credentials_path):
                        logger.error("credentials_file_not_found",
                                   path=self.credentials_path,
                                   message="Download OAuth credentials from Google Cloud Console")
                        return False

                    logger.info("starting_oauth_flow",
                               message="Browser will open for authentication")
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.credentials_path, self.SCOPES)
                    self.creds = flow.run_local_server(port=0)

                # Save token for future use
                token_dir = os.path.dirname(self.token_path)
                if token_dir and not os.path.exists(token_dir):
                    os.makedirs(token_dir)

                with open(self.token_path, 'w') as token:
                    token.write(self.creds.to_json())
                logger.info("token_saved", token_path=self.token_path)

            # Build Drive service
            self.service = build('drive', 'v3', credentials=self.creds)
            logger.info("gdrive_authenticated")
            return True

        except Exception as e:
            logger.error("gdrive_authentication_failed", error=str(e), exc_info=True)
            return False

    def create_folder(self, name: str, parent_folder_id: Optional[str] = None,
                     make_public: bool = True) -> Optional[str]:
        """
        Create a folder in Google Drive

        Args:
            name: Folder name
            parent_folder_id: Parent folder ID (None for root)
            make_public: Make folder publicly accessible (anyone with link)

        Returns:
            str: Created folder ID, or None if failed
        """
        if not self.service:
            logger.error("gdrive_not_authenticated")
            return None

        try:
            file_metadata = {
                'name': name,
                'mimeType': 'application/vnd.google-apps.folder'
            }

            if parent_folder_id:
                file_metadata['parents'] = [parent_folder_id]

            folder = self.service.files().create(
                body=file_metadata,
                fields='id, name, webViewLink'
            ).execute()

            folder_id = folder.get('id')
            logger.info("gdrive_folder_created",
                       folder_name=name,
                       folder_id=folder_id,
                       parent_id=parent_folder_id)

            # Make public if requested
            if make_public:
                self._make_public(folder_id)

            return folder_id

        except HttpError as e:
            logger.error("gdrive_folder_creation_failed",
                        folder_name=name,
                        error=str(e),
                        exc_info=True)
            return None

    def create_worker_subfolders(self, parent_folder_id: str, num_workers: int,
                                make_public: bool = True) -> Dict[int, str]:
        """
        Create worker subfolders (worker1/, worker2/, etc.) in parent folder

        Args:
            parent_folder_id: Parent Drive folder ID
            num_workers: Number of worker folders to create
            make_public: Make folders publicly accessible

        Returns:
            Dict[int, str]: Mapping of worker index to folder ID {0: "folder_id1", 1: "folder_id2", ...}
        """
        worker_folders = {}

        for i in range(num_workers):
            worker_name = f"worker{i+1}"
            folder_id = self.create_folder(worker_name, parent_folder_id, make_public)

            if folder_id:
                worker_folders[i] = folder_id
                logger.info("worker_subfolder_created",
                           worker_index=i,
                           worker_name=worker_name,
                           folder_id=folder_id)
            else:
                logger.error("worker_subfolder_creation_failed",
                            worker_index=i,
                            worker_name=worker_name)

        logger.info("worker_subfolders_created",
                   total=len(worker_folders),
                   requested=num_workers)

        return worker_folders

    def get_folder_link(self, folder_id: str) -> Optional[str]:
        """
        Get shareable web link for folder

        Args:
            folder_id: Drive folder ID

        Returns:
            str: Web view link, or None if failed
        """
        if not self.service:
            logger.error("gdrive_not_authenticated")
            return None

        try:
            folder = self.service.files().get(
                fileId=folder_id,
                fields='webViewLink'
            ).execute()

            link = folder.get('webViewLink')
            logger.info("gdrive_folder_link_retrieved",
                       folder_id=folder_id,
                       link=link)
            return link

        except HttpError as e:
            logger.error("gdrive_folder_link_failed",
                        folder_id=folder_id,
                        error=str(e),
                        exc_info=True)
            return None

    def get_worker_folder_links(self, worker_folder_ids: Dict[int, str]) -> Dict[int, str]:
        """
        Get shareable links for all worker folders

        Args:
            worker_folder_ids: Mapping of worker index to folder ID

        Returns:
            Dict[int, str]: Mapping of worker index to shareable link
        """
        worker_links = {}

        for worker_index, folder_id in worker_folder_ids.items():
            link = self.get_folder_link(folder_id)
            if link:
                worker_links[worker_index] = link

        return worker_links

    def count_story_folders(self, folder_id: str) -> int:
        """Count story_* folders across all batch_* subfolders in a Drive folder.

        Args:
            folder_id: Google Drive folder ID to search in

        Returns:
            Number of story folders found, or 0 on error/no service
        """
        if not self.service:
            return 0

        try:
            # List batch_* subfolders
            batch_query = (
                f"'{folder_id}' in parents "
                f"and mimeType='application/vnd.google-apps.folder' "
                f"and name contains 'batch_' "
                f"and trashed=false"
            )
            batches = self.service.files().list(
                q=batch_query, fields="files(id, name)", pageSize=1000
            ).execute().get('files', [])

            count = 0
            for batch in batches:
                # Count story_* folders in each batch
                story_query = (
                    f"'{batch['id']}' in parents "
                    f"and mimeType='application/vnd.google-apps.folder' "
                    f"and name contains 'story_' "
                    f"and trashed=false"
                )
                stories = self.service.files().list(
                    q=story_query, fields="files(id)", pageSize=1000
                ).execute().get('files', [])
                count += len(stories)

            logger.info("gdrive_story_count",
                       folder_id=folder_id,
                       batch_count=len(batches),
                       story_count=count)
            return count

        except Exception as e:
            logger.error("gdrive_story_count_failed",
                        folder_id=folder_id,
                        error=str(e))
            return 0

    def list_subfolders(self, folder_id: str,
                        name_prefix: Optional[str] = None) -> List[Dict[str, str]]:
        """List subfolders in a Google Drive folder.

        Args:
            folder_id: Parent folder ID
            name_prefix: Optional name prefix filter (e.g., 'worker', 'batch_', 'story_')

        Returns:
            List of dicts with 'id' and 'name' keys, sorted by name
        """
        if not self.service:
            logger.error("gdrive_not_authenticated")
            return []

        try:
            query = (
                f"'{folder_id}' in parents "
                f"and mimeType='application/vnd.google-apps.folder' "
                f"and trashed=false"
            )
            if name_prefix:
                query += f" and name contains '{name_prefix}'"

            all_files = []
            page_token = None

            while True:
                response = self.service.files().list(
                    q=query,
                    fields="nextPageToken, files(id, name)",
                    pageSize=1000,
                    pageToken=page_token
                ).execute()

                all_files.extend(response.get('files', []))
                page_token = response.get('nextPageToken')
                if not page_token:
                    break

            all_files.sort(key=lambda f: f['name'])
            return all_files

        except Exception as e:
            logger.error("gdrive_list_subfolders_failed",
                        folder_id=folder_id,
                        error=str(e))
            return []

    def list_files_in_folder(self, folder_id: str,
                             name: Optional[str] = None) -> List[Dict[str, str]]:
        """List non-folder files in a Google Drive folder.

        Args:
            folder_id: Parent folder ID
            name: Optional exact filename filter (e.g., 'batch_summary.json')

        Returns:
            List of dicts with 'id' and 'name' keys
        """
        if not self.service:
            logger.error("gdrive_not_authenticated")
            return []

        try:
            query = (
                f"'{folder_id}' in parents "
                f"and mimeType!='application/vnd.google-apps.folder' "
                f"and trashed=false"
            )
            if name:
                query += f" and name='{name}'"

            response = self.service.files().list(
                q=query,
                fields="files(id, name)",
                pageSize=1000
            ).execute()

            return response.get('files', [])

        except Exception as e:
            logger.error("gdrive_list_files_failed",
                        folder_id=folder_id,
                        error=str(e))
            return []

    def download_json_file(self, file_id: str) -> Optional[dict]:
        """Download a JSON file from Google Drive and parse it.

        Args:
            file_id: Google Drive file ID

        Returns:
            Parsed dict, or None on failure
        """
        if not self.service:
            logger.error("gdrive_not_authenticated")
            return None

        try:
            request = self.service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)

            done = False
            while not done:
                _, done = downloader.next_chunk()

            fh.seek(0)
            content = fh.read().decode('utf-8')
            return json.loads(content)

        except Exception as e:
            logger.error("gdrive_download_json_failed",
                        file_id=file_id,
                        error=str(e))
            return None

    def upload_json_file(self, name: str, data: dict,
                         parent_folder_id: str) -> Optional[str]:
        """Upload JSON data as a file to Google Drive.

        Args:
            name: File name (e.g. "batch_summary.json")
            data: Dict to serialize as JSON
            parent_folder_id: Parent folder ID

        Returns:
            File ID, or None on failure
        """
        if not self.service:
            logger.error("gdrive_not_authenticated")
            return None

        try:
            content = json.dumps(data, indent=2).encode('utf-8')
            media = MediaInMemoryUpload(content, mimetype='application/json')

            file_metadata = {
                'name': name,
                'parents': [parent_folder_id],
                'mimeType': 'application/json'
            }

            uploaded = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()

            file_id = uploaded.get('id')
            logger.info("gdrive_json_file_uploaded",
                       name=name,
                       file_id=file_id,
                       parent_id=parent_folder_id)
            return file_id

        except Exception as e:
            logger.error("gdrive_json_upload_failed",
                        name=name,
                        error=str(e),
                        exc_info=True)
            return None

    # =========================================================================
    # Google Drive Results Merge (--merge-gdrive-results)
    # =========================================================================

    def index_worker_batch_structure(self, root_folder_id: str) -> Dict[str, Any]:
        """Index the worker/batch/story folder structure in a Google Drive folder.

        Traverses: root/ -> worker*/ -> batch_*/ -> story_*/

        Args:
            root_folder_id: Root Google Drive folder ID

        Returns:
            Dict with full index including workers, batches, stories, and totals
        """
        index: Dict[str, Any] = {
            'root_folder_id': root_folder_id,
            'workers': [],
            'totals': {
                'worker_count': 0,
                'batch_count': 0,
                'total_story_folders': 0,
                'complete_batches': 0,
                'incomplete_batches': 0,
            }
        }

        worker_folders = self.list_subfolders(root_folder_id, 'worker')
        index['totals']['worker_count'] = len(worker_folders)

        for worker_folder in worker_folders:
            worker_info: Dict[str, Any] = {
                'name': worker_folder['name'],
                'id': worker_folder['id'],
                'batches': [],
            }

            batch_folders = self.list_subfolders(worker_folder['id'], 'batch_')

            for batch_folder in batch_folders:
                story_folders = self.list_subfolders(batch_folder['id'])
                batch_files = self.list_files_in_folder(batch_folder['id'])

                batch_summary_file = next(
                    (f for f in batch_files if f['name'] == 'batch_summary.json'), None)
                has_summary = batch_summary_file is not None

                batch_info = {
                    'name': batch_folder['name'],
                    'id': batch_folder['id'],
                    'story_folders': story_folders,
                    'has_batch_summary': has_summary,
                    'batch_summary_file_id': batch_summary_file['id'] if batch_summary_file else None,
                }

                worker_info['batches'].append(batch_info)

                index['totals']['batch_count'] += 1
                index['totals']['total_story_folders'] += len(story_folders)
                if has_summary:
                    index['totals']['complete_batches'] += 1
                else:
                    index['totals']['incomplete_batches'] += 1

            story_count = sum(len(b['story_folders']) for b in worker_info['batches'])
            print(f"  {worker_folder['name']}: {len(worker_info['batches'])} batch(es), "
                  f"{story_count} simulation(s)")

            index['workers'].append(worker_info)

        return index

    def aggregate_batch_summaries(self, index: Dict[str, Any]) -> dict:
        """Aggregate batch summaries from all workers/batches.

        For complete batches (have batch_summary.json): downloads and aggregates stats.
        For incomplete batches: counts story folders as total with unknown status.

        Args:
            index: Index from index_worker_batch_structure()

        Returns:
            Merged summary dict for upload as batch_summary.json
        """
        total_stories_from_summaries = 0
        total_success = 0
        total_failed = 0
        total_from_incomplete = 0
        source_batches = []

        for worker in index['workers']:
            for batch in worker['batches']:
                story_count = len(batch['story_folders'])

                if batch['has_batch_summary'] and batch['batch_summary_file_id']:
                    summary = self.download_json_file(batch['batch_summary_file_id'])
                    if summary:
                        # Try different summary formats (batch_reporter vs orchestrator)
                        stats = summary.get('statistics', summary)
                        batch_success = stats.get('success_count',
                                                  stats.get('successful', 0))
                        batch_failed = stats.get('failure_count',
                                                 stats.get('failed', 0))
                        total_success += batch_success
                        total_failed += batch_failed
                        total_stories_from_summaries += stats.get(
                            'total_stories', story_count)

                        source_batches.append({
                            'worker': worker['name'],
                            'batch': batch['name'],
                            'stories': story_count,
                            'complete': True,
                            'success': batch_success,
                            'failed': batch_failed,
                        })
                        continue

                # Incomplete batch or failed to download summary
                total_from_incomplete += story_count
                source_batches.append({
                    'worker': worker['name'],
                    'batch': batch['name'],
                    'stories': story_count,
                    'complete': False,
                })

        total_all = total_stories_from_summaries + total_from_incomplete
        return {
            'merged_from_gdrive': True,
            'root_folder_id': index['root_folder_id'],
            'num_workers': index['totals']['worker_count'],
            'num_batches': index['totals']['batch_count'],
            'total_story_folders': index['totals']['total_story_folders'],
            'complete_batches': index['totals']['complete_batches'],
            'incomplete_batches': index['totals']['incomplete_batches'],
            'statistics': {
                'total_stories': total_all,
                'successful': total_success,
                'failed': total_failed,
                'unknown': total_from_incomplete,
            },
            'merged_at': datetime.now().isoformat(),
            'source_batches': source_batches,
        }

    def flatten_worker_batches_to_root(self, root_folder_id: str,
                                        index: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten story folders from worker/batch hierarchy into root folder.

        Moves all story_* folders from worker*/batch_*/ directly into root/.
        Trashes empty worker and batch folders after ALL their stories are moved.

        SAFETY: Uses Google Drive API move (addParents/removeParents) which is
        atomic -- if it fails, the file stays in the original location.
        Only trashes folders after verifying all children moved successfully.

        Args:
            root_folder_id: Root folder ID (destination)
            index: Index from index_worker_batch_structure()

        Returns:
            Dict with move results
        """
        result: Dict[str, Any] = {
            'moved_count': 0,
            'failed_count': 0,
            'trashed_folders': 0,
            'errors': [],
        }

        # Check for name conflicts in root
        existing_in_root = self.list_subfolders(root_folder_id)
        existing_names = {f['name'] for f in existing_in_root}

        for worker in index['workers']:
            worker_all_moved = True

            for batch in worker['batches']:
                batch_all_moved = True

                for story in batch['story_folders']:
                    story_name = story['name']

                    # Handle name conflicts
                    if story_name in existing_names:
                        story_name = f"{story_name}_{worker['name']}"
                        logger.info("gdrive_rename_conflict",
                                   original=story['name'],
                                   renamed=story_name)

                    try:
                        update_body = {}
                        if story_name != story['name']:
                            update_body['name'] = story_name

                        self.service.files().update(
                            fileId=story['id'],
                            body=update_body if update_body else None,
                            addParents=root_folder_id,
                            removeParents=batch['id'],
                            fields='id, parents'
                        ).execute()

                        existing_names.add(story_name)
                        result['moved_count'] += 1

                    except Exception as e:
                        result['failed_count'] += 1
                        error_msg = f"Failed to move {story['name']}: {e}"
                        result['errors'].append(error_msg)
                        logger.error("gdrive_move_failed",
                                    story=story['name'],
                                    error=str(e))
                        batch_all_moved = False
                        worker_all_moved = False

                # Trash batch folder only if all stories moved
                if batch_all_moved:
                    try:
                        self.service.files().update(
                            fileId=batch['id'],
                            body={'trashed': True}
                        ).execute()
                        result['trashed_folders'] += 1
                    except Exception as e:
                        logger.warning("gdrive_trash_batch_failed",
                                      batch=batch['name'],
                                      error=str(e))

            # Trash worker folder only if all batches moved
            if worker_all_moved:
                try:
                    self.service.files().update(
                        fileId=worker['id'],
                        body={'trashed': True}
                    ).execute()
                    result['trashed_folders'] += 1
                except Exception as e:
                    logger.warning("gdrive_trash_worker_failed",
                                  worker=worker['name'],
                                  error=str(e))

            status = "OK" if worker_all_moved else "PARTIAL"
            worker_stories = sum(len(b['story_folders']) for b in worker['batches'])
            print(f"  [{worker['name']}] [{status}] Moved stories from "
                  f"{len(worker['batches'])} batch(es)")

        return result

    def merge_flat_folders(self, dest_folder_id: str,
                            source_folder_ids: List[str]) -> Dict[str, Any]:
        """Merge simulation folders from multiple flat folders into a destination.

        Moves all subfolders from each source folder into the destination folder.
        Trashes source folders after all their contents are moved successfully.

        SAFETY: Atomic moves, trash-only-after-success, idempotent.

        Args:
            dest_folder_id: Destination folder ID (keeps existing simulations)
            source_folder_ids: List of source folder IDs to move FROM

        Returns:
            Dict with merge results
        """
        result: Dict[str, Any] = {
            'moved_count': 0,
            'failed_count': 0,
            'trashed_folders': 0,
            'errors': [],
            'dest_existing': 0,
            'source_counts': {},
        }

        if not self.service:
            logger.error("gdrive_not_authenticated")
            return result

        # Index destination for conflict detection
        dest_folders = self.list_subfolders(dest_folder_id)
        existing_names = {f['name'] for f in dest_folders}
        result['dest_existing'] = len(dest_folders)

        for src_idx, src_folder_id in enumerate(source_folder_ids):
            src_folders = self.list_subfolders(src_folder_id)
            # Skip non-simulation items (metadata files are files, not folders)
            sim_folders = [f for f in src_folders
                           if not f['name'].startswith(('worker', 'batch_'))]
            result['source_counts'][src_folder_id] = len(sim_folders)

            all_moved = True

            for sim in sim_folders:
                sim_name = sim['name']

                # Handle name conflicts
                if sim_name in existing_names:
                    sim_name = f"{sim_name}_src{src_idx + 1}"
                    logger.info("gdrive_rename_conflict",
                               original=sim['name'],
                               renamed=sim_name)

                try:
                    update_body = {}
                    if sim_name != sim['name']:
                        update_body['name'] = sim_name

                    self.service.files().update(
                        fileId=sim['id'],
                        body=update_body if update_body else None,
                        addParents=dest_folder_id,
                        removeParents=src_folder_id,
                        fields='id, parents'
                    ).execute()

                    existing_names.add(sim_name)
                    result['moved_count'] += 1

                except Exception as e:
                    result['failed_count'] += 1
                    error_msg = f"Failed to move {sim['name']}: {e}"
                    result['errors'].append(error_msg)
                    logger.error("gdrive_flat_move_failed",
                                sim=sim['name'], error=str(e))
                    all_moved = False

            status = "OK" if all_moved else "PARTIAL"
            print(f"  [Source {src_idx + 1}] [{status}] Moved {len(sim_folders)} simulations")

            # Trash source folder only if ALL simulations moved
            if all_moved:
                try:
                    self.service.files().update(
                        fileId=src_folder_id,
                        body={'trashed': True}
                    ).execute()
                    result['trashed_folders'] += 1
                except Exception as e:
                    logger.warning("gdrive_trash_source_failed",
                                  folder_id=src_folder_id, error=str(e))

        return result

    def generate_merged_report(self, merged_summary: dict,
                                index: Dict[str, Any]) -> str:
        """Generate a human-readable markdown report from merged results.

        Args:
            merged_summary: Aggregated summary from aggregate_batch_summaries()
            index: Index from index_worker_batch_structure()

        Returns:
            Markdown report string
        """
        stats = merged_summary['statistics']
        totals = index['totals']
        lines = []

        lines.append("# Merged Batch Report\n")
        lines.append(f"**Merged at:** {merged_summary['merged_at']}\n")
        lines.append(f"**Source folder:** `{merged_summary['root_folder_id']}`\n")
        lines.append("\n---\n")

        # Summary
        lines.append("\n## Summary\n\n")
        lines.append(f"- **Total simulations:** {totals['total_story_folders']}\n")
        lines.append(f"- **Workers:** {totals['worker_count']}\n")
        lines.append(f"- **Batches:** {totals['batch_count']}\n")

        if stats['successful'] or stats['failed']:
            total_known = stats['successful'] + stats['failed']
            success_rate = (
                f"{100 * stats['successful'] / total_known:.1f}%"
                if total_known > 0 else "N/A"
            )
            lines.append(f"- **Successful:** {stats['successful']} ({success_rate})\n")
            lines.append(f"- **Failed:** {stats['failed']}\n")

        if stats['unknown'] > 0:
            lines.append(f"- **Unknown (incomplete batches):** {stats['unknown']}\n")

        lines.append(f"- **Complete batches:** {totals['complete_batches']}\n")
        lines.append(f"- **Incomplete batches:** {totals['incomplete_batches']}\n")
        lines.append("\n")

        # Per-worker breakdown
        lines.append("## Per-Worker Breakdown\n\n")
        lines.append("| Worker | Batches | Simulations |\n")
        lines.append("|--------|---------|-------------|\n")

        for worker in index['workers']:
            sim_count = sum(len(b['story_folders']) for b in worker['batches'])
            lines.append(
                f"| {worker['name']} | {len(worker['batches'])} | {sim_count} |\n")

        lines.append("\n")

        # Source batches
        lines.append("## Source Batches\n\n")
        lines.append("| Worker | Batch | Simulations | Complete | Success | Failed |\n")
        lines.append("|--------|-------|-------------|----------|---------|--------|\n")

        for batch_info in merged_summary.get('source_batches', []):
            complete = "Yes" if batch_info['complete'] else "No"
            success = batch_info.get('success', '-')
            failed = batch_info.get('failed', '-')
            lines.append(
                f"| {batch_info['worker']} | {batch_info['batch']} "
                f"| {batch_info['stories']} | {complete} "
                f"| {success} | {failed} |\n")

        lines.append("\n---\n\n")
        lines.append("*Report generated by Multiagent Story Generation System "
                      "(--merge-gdrive-results)*\n")

        return "".join(lines)

    def upload_text_file(self, name: str, content: str,
                          parent_folder_id: str,
                          mimetype: str = 'text/markdown') -> Optional[str]:
        """Upload text content as a file to Google Drive.

        Args:
            name: File name (e.g. "batch_report.md")
            content: Text content to upload
            parent_folder_id: Parent folder ID
            mimetype: MIME type (default: text/markdown)

        Returns:
            File ID, or None on failure
        """
        if not self.service:
            logger.error("gdrive_not_authenticated")
            return None

        try:
            encoded = content.encode('utf-8')
            media = MediaInMemoryUpload(encoded, mimetype=mimetype)

            file_metadata = {
                'name': name,
                'parents': [parent_folder_id],
            }

            uploaded = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()

            file_id = uploaded.get('id')
            logger.info("gdrive_text_file_uploaded",
                       name=name,
                       file_id=file_id,
                       parent_id=parent_folder_id)
            return file_id

        except Exception as e:
            logger.error("gdrive_text_upload_failed",
                        name=name,
                        error=str(e),
                        exc_info=True)
            return None

    def merge_worker_folders(self, parent_folder_id: str,
                             worker_folder_ids: Dict[int, str],
                             merged_folder_name: str,
                             merged_summary: Optional[dict] = None) -> Optional[str]:
        """Merge all worker folder contents into a single folder.

        Moves all items from worker batch subfolders into one merged folder,
        then trashes the empty worker folders.

        Args:
            parent_folder_id: Parent Drive folder containing worker subfolders
            worker_folder_ids: Mapping of worker index to folder ID
            merged_folder_name: Name for the merged folder
            merged_summary: Optional summary dict to upload as batch_summary.json

        Returns:
            Merged folder ID, or None on failure
        """
        if not self.service:
            logger.error("gdrive_not_authenticated")
            return None

        try:
            # Create merged folder
            merged_folder_id = self.create_folder(
                merged_folder_name, parent_folder_id, make_public=True)
            if not merged_folder_id:
                logger.error("gdrive_merge_folder_creation_failed")
                return None

            logger.info("gdrive_merge_folder_created",
                       name=merged_folder_name,
                       folder_id=merged_folder_id)

            total_moved = 0

            for worker_index, worker_folder_id in sorted(worker_folder_ids.items()):
                # List batch_* subfolders in this worker folder
                batch_query = (
                    f"'{worker_folder_id}' in parents "
                    f"and mimeType='application/vnd.google-apps.folder' "
                    f"and name contains 'batch_' "
                    f"and trashed=false"
                )
                batches = self.service.files().list(
                    q=batch_query, fields="files(id, name)", pageSize=1000
                ).execute().get('files', [])

                worker_moved = 0

                for batch in batches:
                    # List ALL items in each batch subfolder (stories, reports, etc.)
                    items_query = (
                        f"'{batch['id']}' in parents "
                        f"and trashed=false"
                    )
                    items = self.service.files().list(
                        q=items_query, fields="files(id, name)", pageSize=1000
                    ).execute().get('files', [])

                    # Move each item to merged folder
                    for item in items:
                        self.service.files().update(
                            fileId=item['id'],
                            addParents=merged_folder_id,
                            removeParents=batch['id'],
                            fields='id, parents'
                        ).execute()
                        worker_moved += 1

                    # Trash the now-empty batch subfolder
                    self.service.files().update(
                        fileId=batch['id'],
                        body={'trashed': True}
                    ).execute()

                total_moved += worker_moved
                print(f"  [Worker {worker_index + 1}] Moved {worker_moved} items")

                logger.info("gdrive_worker_items_moved",
                           worker_index=worker_index,
                           items_moved=worker_moved)

                # Trash the now-empty worker folder
                self.service.files().update(
                    fileId=worker_folder_id,
                    body={'trashed': True}
                ).execute()

            # Upload merged summary if provided
            if merged_summary:
                self.upload_json_file(
                    "batch_summary.json", merged_summary, merged_folder_id)

            logger.info("gdrive_merge_complete",
                       merged_folder_id=merged_folder_id,
                       total_items_moved=total_moved,
                       workers_merged=len(worker_folder_ids))

            print(f"  Total: {total_moved} items merged from "
                  f"{len(worker_folder_ids)} workers")

            return merged_folder_id

        except Exception as e:
            logger.error("gdrive_merge_failed",
                        error=str(e),
                        exc_info=True)
            return None

    def _make_public(self, folder_id: str) -> bool:
        """
        Make folder publicly accessible (anyone with link can view)

        Args:
            folder_id: Drive folder ID

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            permission = {
                'type': 'anyone',
                'role': 'reader'
            }

            self.service.permissions().create(
                fileId=folder_id,
                body=permission
            ).execute()

            logger.info("gdrive_folder_made_public", folder_id=folder_id)
            return True

        except HttpError as e:
            logger.error("gdrive_make_public_failed",
                        folder_id=folder_id,
                        error=str(e),
                        exc_info=True)
            return False

    @staticmethod
    def copy_credentials_to_vm(vm_path: str, vmrun_exe: str,
                              guest_username: str, guest_password: str,
                              credentials_dir: str = "credentials",
                              guest_work_dir: str = "C:\\multiagent_story_system") -> bool:
        """
        Copy Google Drive credentials to guest VM via vmrun

        Args:
            vm_path: Path to worker VM .vmx file
            vmrun_exe: Path to vmrun.exe
            guest_username: Guest OS username
            guest_password: Guest OS password
            credentials_dir: Host credentials directory
            guest_work_dir: Guest working directory

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            creds_path = Path(credentials_dir) / "google_drive_credentials.json"
            token_path = Path(credentials_dir) / "token.json"

            guest_creds_dir = f"{guest_work_dir}\\credentials"

            # Create credentials directory in guest
            mkdir_cmd = [
                vmrun_exe, "-T", "ws",
                "-gu", guest_username, "-gp", guest_password,
                "runProgramInGuest", vm_path,
                "cmd.exe", "/c", f"mkdir {guest_creds_dir}"
            ]

            subprocess.run(mkdir_cmd, check=False, capture_output=True)  # Ignore if exists

            # Copy credentials file
            if creds_path.exists():
                copy_creds_cmd = [
                    vmrun_exe, "-T", "ws",
                    "-gu", guest_username, "-gp", guest_password,
                    "copyFileFromHostToGuest", vm_path,
                    str(creds_path),
                    f"{guest_creds_dir}\\google_drive_credentials.json"
                ]

                result = subprocess.run(copy_creds_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error("failed_to_copy_credentials",
                               vm_path=vm_path,
                               error=result.stderr)
                    return False

                logger.info("credentials_copied_to_vm",
                           vm_path=vm_path,
                           file="google_drive_credentials.json")

            # Copy token file (if exists)
            if token_path.exists():
                copy_token_cmd = [
                    vmrun_exe, "-T", "ws",
                    "-gu", guest_username, "-gp", guest_password,
                    "copyFileFromHostToGuest", vm_path,
                    str(token_path),
                    f"{guest_creds_dir}\\token.json"
                ]

                result = subprocess.run(copy_token_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error("failed_to_copy_token",
                               vm_path=vm_path,
                               error=result.stderr)
                    return False

                logger.info("token_copied_to_vm",
                           vm_path=vm_path,
                           file="token.json")

            return True

        except Exception as e:
            logger.error("copy_credentials_to_vm_failed",
                        vm_path=vm_path,
                        error=str(e),
                        exc_info=True)
            return False


def test_authentication():
    """Test Google Drive authentication (for manual testing)"""
    print("Testing Google Drive authentication...")

    try:
        manager = GDriveManager()

        print("Authenticating with Google Drive...")
        if manager.authenticate():
            print("✓ Authentication successful!")

            # Test folder creation
            print("\nTesting folder creation...")
            test_folder_id = manager.create_folder(
                name=f"sa_video_story_engine",
                make_public=True
            )

            if test_folder_id:
                print(f"✓ Test folder created: {test_folder_id}")

                link = manager.get_folder_link(test_folder_id)
                if link:
                    print(f"✓ Folder link: {link}")

                print("\nTest complete! You can delete the test folder from Google Drive.")
            else:
                print("✗ Failed to create test folder")

        else:
            print("✗ Authentication failed")

    except ImportError as e:
        print(f"✗ Google Drive dependencies not installed: {e}")
        print("  Install with: pip install google-auth google-api-python-client google-auth-oauthlib")


if __name__ == "__main__":
    # Run authentication test if executed directly
    test_authentication()

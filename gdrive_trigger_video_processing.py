"""
Trigger Google Drive video processing for unprocessed videos.

Videos uploaded programmatically may not be processed by Google Drive for
streaming playback if the MIME type was not set correctly during upload.
This script diagnoses and fixes the issue by correcting MIME types and
optionally using files.copy() to force reprocessing.

Usage:
    python gdrive_trigger_video_processing.py FOLDER_ID --check-only --verbose
    python gdrive_trigger_video_processing.py FOLDER_ID1 FOLDER_ID2 --verbose
    python gdrive_trigger_video_processing.py FOLDER_ID --copy --verbose
"""

import argparse
import time
from collections import Counter
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_DRIVE_AVAILABLE = True
except ImportError:
    GOOGLE_DRIVE_AVAILABLE = False
    logger.warning("google_drive_dependencies_not_available",
                   message="Install with: pip install google-auth google-api-python-client google-auth-oauthlib")


def retry_on_network_error(max_retries: int = 3, base_delay: float = 1.0):
    """Retry on transient network errors with exponential backoff."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (TimeoutError, ConnectionResetError, OSError) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("network_error_retrying",
                                       func=func.__name__,
                                       attempt=attempt + 1,
                                       delay=delay,
                                       error=str(e))
                        time.sleep(delay)
                    else:
                        logger.error("network_error_max_retries",
                                     func=func.__name__,
                                     error=str(e))
            raise last_exception
        return wrapper
    return decorator


class VideoProcessingTrigger:
    """Finds and triggers processing for unprocessed Google Drive videos."""

    SCOPES = ['https://www.googleapis.com/auth/drive']

    def __init__(self, credentials_path: str = "credentials/google_drive_credentials.json",
                 token_path: str = "credentials/token.json",
                 delay: float = 0.5):
        if not GOOGLE_DRIVE_AVAILABLE:
            raise ImportError("Google Drive dependencies not installed")

        self.credentials_path = credentials_path
        self.token_path = token_path
        self.delay = delay
        self.service = None
        self.creds = None
        self._authenticate()

    def _authenticate(self):
        """Authenticate with Google Drive API."""
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

            token_dir = str(Path(self.token_path).parent)
            if token_dir:
                Path(token_dir).mkdir(parents=True, exist_ok=True)
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())

        self.creds = creds
        self.service = build('drive', 'v3', credentials=creds)
        logger.info("gdrive_authenticated")

    def _list_paginated(self, query: str,
                        fields: str) -> List[Dict[str, Any]]:
        """List files/folders with pagination support."""
        all_items = []
        page_token = None

        while True:
            response = self.service.files().list(
                q=query,
                fields=f"nextPageToken, {fields}",
                pageSize=1000,
                pageToken=page_token
            ).execute()

            all_items.extend(response.get('files', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                break

        return all_items

    def find_all_videos(self, folder_ids: List[str],
                        verbose: bool = False) -> Iterator[Dict[str, Any]]:
        """Recursively find all video files in folder trees.

        Yields dicts with keys: id, name, mimeType, parents, videoMediaMetadata,
        thumbnailLink, hasThumbnail.
        """
        visited = set()

        def traverse(folder_id: str, path: str = "", depth: int = 0):
            if folder_id in visited:
                return
            visited.add(folder_id)

            # Find .mp4 files in this folder
            try:
                video_query = (
                    f"'{folder_id}' in parents "
                    f"and name contains '.mp4' "
                    f"and trashed=false"
                )
                videos = self._list_paginated(
                    video_query,
                    "files(id, name, mimeType, parents, videoMediaMetadata, "
                    "thumbnailLink, hasThumbnail)"
                )
                for video in videos:
                    video['_path'] = path
                    yield video
            except HttpError as e:
                logger.error("list_videos_failed",
                             folder_id=folder_id, error=str(e))

            # Recurse into subfolders
            try:
                folder_query = (
                    f"'{folder_id}' in parents "
                    f"and mimeType='application/vnd.google-apps.folder' "
                    f"and trashed=false"
                )
                subfolders = self._list_paginated(
                    folder_query,
                    "files(id, name)"
                )
                for subfolder in subfolders:
                    sub_path = f"{path}/{subfolder['name']}" if path else subfolder['name']
                    if verbose:
                        print(f"  {'  ' * depth}Scanning: {sub_path}/")
                    yield from traverse(subfolder['id'], sub_path, depth + 1)
            except HttpError as e:
                logger.error("list_folders_failed",
                             folder_id=folder_id, error=str(e))

        for folder_id in folder_ids:
            if verbose:
                print(f"Scanning root folder: {folder_id}")
            yield from traverse(folder_id)

    @staticmethod
    def is_processed(video: Dict[str, Any]) -> bool:
        """Check if a video has been processed by Google Drive."""
        meta = video.get('videoMediaMetadata')
        if not meta:
            return False
        return meta.get('durationMillis') is not None

    @retry_on_network_error(max_retries=3, base_delay=1.0)
    def fix_mime_type(self, file_id: str, file_name: str,
                      current_mime: str) -> Tuple[bool, str]:
        """Fix MIME type to video/mp4 if incorrect.

        Returns:
            (success, description)
        """
        try:
            self.service.files().update(
                fileId=file_id,
                body={'mimeType': 'video/mp4'}
            ).execute()
            return True, f"mime_fixed:{current_mime}->video/mp4"
        except Exception as e:
            error_msg = str(e)
            logger.error("mime_fix_failed",
                         file_id=file_id, name=file_name, error=error_msg)
            return False, error_msg

    @retry_on_network_error(max_retries=3, base_delay=1.0)
    def copy_and_delete(self, file_id: str, file_name: str,
                        parents: List[str]) -> Tuple[bool, str]:
        """Copy file (triggers reprocessing) then delete original.

        Returns:
            (success, description)
        """
        try:
            # Copy with correct MIME type and same parent
            copy_body = {
                'name': file_name,
                'mimeType': 'video/mp4',
            }
            if parents:
                copy_body['parents'] = parents

            copied = self.service.files().copy(
                fileId=file_id,
                body=copy_body
            ).execute()
            new_id = copied['id']

            # Delete original
            try:
                self.service.files().delete(fileId=file_id).execute()
            except Exception as del_err:
                logger.warning("delete_after_copy_failed",
                               original_id=file_id,
                               copy_id=new_id,
                               error=str(del_err))
                return True, f"copied:{new_id}(original_not_deleted)"

            return True, f"copied:{new_id}"
        except Exception as e:
            error_msg = str(e)
            logger.error("copy_and_delete_failed",
                         file_id=file_id, name=file_name, error=error_msg)
            return False, error_msg

    def run(self, folder_ids: List[str], dry_run: bool = False,
            check_only: bool = False, use_copy: bool = False,
            verbose: bool = False) -> Dict[str, Any]:
        """Scan folder trees and fix unprocessed videos.

        Modes:
            check_only: Just report MIME types and processing status
            default: Fix incorrect MIME types (safe, no deletions)
            use_copy: Also copy+delete videos that have correct MIME but are
                      still unprocessed
        """
        stats = {
            'total': 0,
            'already_processed': 0,
            'unprocessed': 0,
            'mime_fixed': 0,
            'copied': 0,
            'skipped': 0,
            'failed': 0,
            'errors': [],
            'mime_types': Counter(),
        }

        mode_label = "CHECK ONLY" if check_only else ("DRY RUN" if dry_run else "FIX")
        print(f"[{mode_label}] Scanning {len(folder_ids)} root folder(s)...\n")

        for video in self.find_all_videos(folder_ids, verbose):
            stats['total'] += 1
            name = video['name']
            path = video.get('_path', '')
            display = f"{path}/{name}" if path else name
            mime = video.get('mimeType', 'unknown')
            parents = video.get('parents', [])

            if self.is_processed(video):
                stats['already_processed'] += 1
                stats['mime_types'][mime] += 1
                if verbose:
                    meta = video['videoMediaMetadata']
                    dur = int(meta.get('durationMillis', 0)) / 1000
                    print(f"  [OK] {display} ({dur:.1f}s, {mime})")
            else:
                stats['unprocessed'] += 1
                stats['mime_types'][mime] += 1
                wrong_mime = (mime != 'video/mp4')

                if check_only:
                    status = "WRONG_MIME" if wrong_mime else "CORRECT_MIME"
                    print(f"  [{status}] {display} (mime: {mime}, id: {video['id']})")
                elif dry_run:
                    if wrong_mime:
                        print(f"  [WOULD_FIX_MIME] {display} ({mime} -> video/mp4)")
                    elif use_copy:
                        print(f"  [WOULD_COPY] {display} (mime OK, force reprocess)")
                    else:
                        print(f"  [SKIP] {display} (mime OK, use --copy to force)")
                else:
                    # Actually fix
                    if wrong_mime:
                        success, detail = self.fix_mime_type(
                            video['id'], name, mime)
                        if success:
                            stats['mime_fixed'] += 1
                            print(f"  [MIME_FIXED] {display} ({detail})")
                        else:
                            stats['failed'] += 1
                            stats['errors'].append(f"{display}: {detail}")
                            print(f"  [FAILED] {display}: {detail}")
                    elif use_copy:
                        success, detail = self.copy_and_delete(
                            video['id'], name, parents)
                        if success:
                            stats['copied'] += 1
                            print(f"  [COPIED] {display} ({detail})")
                        else:
                            stats['failed'] += 1
                            stats['errors'].append(f"{display}: {detail}")
                            print(f"  [FAILED] {display}: {detail}")
                    else:
                        stats['skipped'] += 1
                        if verbose:
                            print(f"  [SKIP] {display} (mime OK, use --copy to force)")

                    time.sleep(self.delay)

            if stats['total'] % 100 == 0:
                print(f"\n  ... {stats['total']} videos scanned ...\n")

        # Print summary
        print(f"\n{'=' * 60}")
        print(f"Total videos found:    {stats['total']}")
        print(f"Already processed:     {stats['already_processed']}")
        print(f"Unprocessed:           {stats['unprocessed']}")

        if stats['mime_types']:
            print(f"\nMIME type distribution (all videos):")
            for mime, count in stats['mime_types'].most_common():
                print(f"  {mime}: {count}")

        if not check_only and not dry_run:
            print(f"\nMIME types fixed:      {stats['mime_fixed']}")
            if use_copy:
                print(f"Copied (reprocess):    {stats['copied']}")
            if stats['skipped']:
                print(f"Skipped (mime OK):     {stats['skipped']}")
            if stats['failed']:
                print(f"Failed:                {stats['failed']}")
                print(f"\nFailed files:")
                for err in stats['errors']:
                    print(f"  - {err}")
        elif dry_run:
            wrong = sum(1 for v in ['WOULD_FIX_MIME'] if v)
            print(f"\n[DRY RUN] Would fix {stats['unprocessed']} unprocessed videos")
        print(f"{'=' * 60}")

        return stats


def main():
    parser = argparse.ArgumentParser(
        description="Fix Google Drive video processing for unprocessed videos"
    )
    parser.add_argument(
        "folder_ids",
        nargs="+",
        help="Google Drive folder ID(s) to scan recursively"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only report MIME types and processing status (read-only)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Use files.copy()+delete for videos with correct MIME type "
             "that are still unprocessed (destructive)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show file-by-file progress including already-processed files"
    )
    parser.add_argument(
        "--credentials",
        default="credentials/google_drive_credentials.json",
        help="Path to Google Drive credentials"
    )
    parser.add_argument(
        "--token",
        default="credentials/token.json",
        help="Path to cached OAuth token"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between API calls in seconds (default: 0.5)"
    )

    args = parser.parse_args()

    if not GOOGLE_DRIVE_AVAILABLE:
        print("Error: Google Drive dependencies not installed.")
        print("Install with: pip install google-auth google-api-python-client google-auth-oauthlib")
        return 1

    try:
        trigger = VideoProcessingTrigger(
            credentials_path=args.credentials,
            token_path=args.token,
            delay=args.delay,
        )
        stats = trigger.run(
            folder_ids=args.folder_ids,
            dry_run=args.dry_run,
            check_only=args.check_only,
            use_copy=args.copy,
            verbose=args.verbose,
        )
        return 0 if stats['failed'] == 0 else 1

    except Exception as e:
        logger.error("fatal_error", error=str(e), exc_info=True)
        print(f"\nError: {e}")
        return 1


if __name__ == "__main__":
    exit(main())

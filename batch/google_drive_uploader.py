"""
Google Drive uploader for batch artifacts.

This module provides functionality to upload batch generation results to
Google Drive with progress tracking and shareable link generation.

Note: Requires google-auth and google-api-python-client packages.
Install with: pip install google-auth google-api-python-client google-auth-oauthlib
"""

import structlog
from pathlib import Path
from typing import Optional, Dict, Any, List
import json
import time
from functools import wraps

logger = structlog.get_logger(__name__)


def retry_on_network_error(max_retries: int = 3, base_delay: float = 1.0):
    """
    Decorator to retry API calls on transient network errors.
    Uses exponential backoff: delay doubles with each retry.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (doubles with each attempt)
    """
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
                        logger.warning(
                            "network_error_retrying",
                            func=func.__name__,
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay=delay,
                            error=str(e)
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "network_error_max_retries",
                            func=func.__name__,
                            max_retries=max_retries,
                            error=str(e)
                        )
            raise last_exception
        return wrapper
    return decorator


class GoogleDriveUploader:
    """Uploads batch artifacts to Google Drive."""

    def __init__(self, credentials_path: str):
        """
        Initialize Google Drive uploader.

        Args:
            credentials_path: Path to Google Drive credentials JSON file

        Raises:
            ImportError: If Google Drive libraries not installed
            FileNotFoundError: If credentials file not found
        """
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError as e:
            logger.error("google_drive_dependencies_missing", error=str(e))
            raise ImportError(
                "Google Drive dependencies not installed. "
                "Install with: pip install google-auth google-api-python-client google-auth-oauthlib"
            )

        self.credentials_path = Path(credentials_path)
        if not self.credentials_path.exists():
            logger.error("credentials_not_found", path=str(self.credentials_path))
            raise FileNotFoundError(f"Credentials not found: {self.credentials_path}")

        # Scopes for Google Drive API
        self.SCOPES = ['https://www.googleapis.com/auth/drive.file']

        # Authenticate and build service
        self.service = self._authenticate()

        logger.info("google_drive_uploader_initialized")

    def _authenticate(self):
        """
        Authenticate with Google Drive API.

        Returns:
            Google Drive API service object
        """
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        token_path = self.credentials_path.parent / "token.json"

        # Load existing token
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), self.SCOPES)

        # Refresh or get new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("refreshing_google_drive_token")
                creds.refresh(Request())
            else:
                logger.info("starting_google_drive_oauth_flow")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), self.SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save credentials for next time
            with open(token_path, 'w') as token:
                token.write(creds.to_json())

            logger.info("google_drive_credentials_saved", path=str(token_path))

        # Build service
        service = build('drive', 'v3', credentials=creds)
        logger.info("google_drive_service_built")

        return service

    @retry_on_network_error(max_retries=3, base_delay=1.0)
    def create_folder(self, name: str, parent_folder_id: Optional[str] = None) -> str:
        """
        Create a folder in Google Drive.

        Args:
            name: Folder name
            parent_folder_id: Optional parent folder ID

        Returns:
            Created folder ID
        """
        file_metadata = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder'
        }

        if parent_folder_id:
            file_metadata['parents'] = [parent_folder_id]

        try:
            folder = self.service.files().create(
                body=file_metadata,
                fields='id'
            ).execute()

            folder_id = folder.get('id')

            logger.info(
                "folder_created",
                name=name,
                folder_id=folder_id,
                parent=parent_folder_id
            )

            return folder_id

        except Exception as e:
            logger.error(
                "folder_creation_failed",
                name=name,
                error=str(e),
                exc_info=True
            )
            raise

    @retry_on_network_error(max_retries=3, base_delay=1.0)
    def upload_file(
        self,
        file_path: Path,
        parent_folder_id: str,
        mime_type: Optional[str] = None
    ) -> str:
        """
        Upload a file to Google Drive.

        Args:
            file_path: Path to file to upload
            parent_folder_id: Parent folder ID
            mime_type: Optional MIME type (auto-detected if None)

        Returns:
            Uploaded file ID
        """
        from googleapiclient.http import MediaFileUpload

        file_path = Path(file_path)

        # Only use resumable uploads for files larger than 5MB
        # Small files cause ResumableUploadError with HTTP 200 OK (known bug)
        file_size = file_path.stat().st_size
        use_resumable = file_size > 5 * 1024 * 1024  # 5MB threshold

        file_metadata = {
            'name': file_path.name,
            'parents': [parent_folder_id]
        }

        media = MediaFileUpload(
            str(file_path),
            mimetype=mime_type,
            resumable=use_resumable
        )

        try:
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()

            file_id = file.get('id')

            logger.debug(
                "file_uploaded",
                file=file_path.name,
                file_id=file_id,
                size_bytes=file_path.stat().st_size
            )

            return file_id

        except Exception as e:
            logger.error(
                "file_upload_failed",
                file=str(file_path),
                error=str(e),
                exc_info=True
            )
            raise

    @retry_on_network_error(max_retries=3, base_delay=1.0)
    def upload_or_update_file(
        self,
        file_path: Path,
        parent_folder_id: str,
        existing_file_id: Optional[str] = None,
        mime_type: Optional[str] = None
    ) -> str:
        """
        Upload a new file or update an existing file on Google Drive.

        Args:
            file_path: Path to file to upload
            parent_folder_id: Parent folder ID (used for new uploads)
            existing_file_id: Optional existing file ID (if updating)
            mime_type: Optional MIME type (auto-detected if None)

        Returns:
            File ID (existing or newly created)
        """
        from googleapiclient.http import MediaFileUpload

        file_path = Path(file_path)

        # Only use resumable uploads for files larger than 5MB
        # Small files cause ResumableUploadError with HTTP 200 OK (known bug)
        # Also causes file size mismatch errors when file changes between attempts
        file_size = file_path.stat().st_size
        use_resumable = file_size > 5 * 1024 * 1024  # 5MB threshold

        media = MediaFileUpload(
            str(file_path),
            mimetype=mime_type,
            resumable=use_resumable
        )

        try:
            if existing_file_id:
                # Update existing file
                file = self.service.files().update(
                    fileId=existing_file_id,
                    media_body=media,
                    fields='id'
                ).execute()

                logger.debug(
                    "file_updated",
                    file=file_path.name,
                    file_id=existing_file_id,
                    size_bytes=file_path.stat().st_size
                )

                return existing_file_id
            else:
                # Create new file
                file_metadata = {
                    'name': file_path.name,
                    'parents': [parent_folder_id]
                }

                file = self.service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id'
                ).execute()

                file_id = file.get('id')

                logger.debug(
                    "file_uploaded",
                    file=file_path.name,
                    file_id=file_id,
                    size_bytes=file_path.stat().st_size
                )

                return file_id

        except Exception as e:
            logger.error(
                "file_upload_or_update_failed",
                file=str(file_path),
                existing_file_id=existing_file_id,
                error=str(e),
                exc_info=True
            )
            raise

    def upload_directory(
        self,
        local_dir: Path,
        drive_folder_id: str,
        exclude_folders: Optional[List[Path]] = None
    ) -> Dict[str, Any]:
        """
        Upload entire directory to Google Drive, optionally excluding specific folders.

        Args:
            local_dir: Local directory to upload
            drive_folder_id: Target Google Drive folder ID
            exclude_folders: List of folder paths to exclude from upload

        Returns:
            Dictionary with upload results and statistics
        """
        local_dir = Path(local_dir)
        exclude_folders = exclude_folders or []

        # Convert to set of absolute paths for faster lookups
        exclude_paths = {Path(f).resolve() for f in exclude_folders}

        logger.info(
            "uploading_directory",
            local_dir=str(local_dir),
            drive_folder_id=drive_folder_id,
            excluded_count=len(exclude_paths)
        )

        # Statistics
        stats = {
            'files_uploaded': 0,
            'folders_created': 0,
            'total_bytes': 0,
            'errors': []
        }

        # Create root folder
        try:
            root_folder_id = self.create_folder(
                name=local_dir.name,
                parent_folder_id=drive_folder_id
            )
            stats['folders_created'] += 1
        except Exception as e:
            logger.error("root_folder_creation_failed", error=str(e))
            raise

        # Track folder mappings (local path -> drive folder ID)
        folder_map = {local_dir: root_folder_id}

        # Walk directory tree
        for item in sorted(local_dir.rglob('*')):
            # Skip excluded folders and their contents
            if item.resolve() in exclude_paths:
                logger.debug("skipping_excluded_folder", folder=item.name)
                continue

            # Skip items inside excluded folders
            if any(item.resolve().is_relative_to(ex) for ex in exclude_paths):
                logger.debug("skipping_item_in_excluded_folder", item=str(item))
                continue

            try:
                # Get parent folder ID
                parent_local = item.parent
                if parent_local not in folder_map:
                    # Create missing parent folder
                    grandparent_id = folder_map.get(parent_local.parent, root_folder_id)
                    parent_id = self.create_folder(
                        name=parent_local.name,
                        parent_folder_id=grandparent_id
                    )
                    folder_map[parent_local] = parent_id
                    stats['folders_created'] += 1
                else:
                    parent_id = folder_map[parent_local]

                if item.is_dir():
                    # Create folder
                    folder_id = self.create_folder(
                        name=item.name,
                        parent_folder_id=parent_id
                    )
                    folder_map[item] = folder_id
                    stats['folders_created'] += 1

                elif item.is_file():
                    # Upload file
                    self.upload_file(
                        file_path=item,
                        parent_folder_id=parent_id
                    )
                    stats['files_uploaded'] += 1
                    stats['total_bytes'] += item.stat().st_size

                    # Progress logging
                    if stats['files_uploaded'] % 10 == 0:
                        logger.info(
                            "upload_progress",
                            files=stats['files_uploaded'],
                            folders=stats['folders_created']
                        )

            except Exception as e:
                error_msg = f"{item.name}: {str(e)}"
                stats['errors'].append(error_msg)
                logger.error(
                    "item_upload_failed",
                    item=str(item),
                    error=str(e)
                )
                # Continue with other files

        # Store root folder ID in stats
        stats['folder_id'] = root_folder_id

        # Get shareable link
        try:
            link = self.get_shareable_link(root_folder_id)
            stats['link'] = link
        except Exception as e:
            logger.error("link_generation_failed", error=str(e))
            stats['link'] = None

        logger.info(
            "directory_upload_complete",
            files=stats['files_uploaded'],
            folders=stats['folders_created'],
            bytes=stats['total_bytes'],
            errors=len(stats['errors']),
            excluded=len(exclude_paths)
        )

        return stats

    @retry_on_network_error(max_retries=3, base_delay=1.0)
    def get_shareable_link(self, file_id: str) -> str:
        """
        Get shareable link for a file or folder.

        Args:
            file_id: File or folder ID

        Returns:
            Shareable link URL
        """
        try:
            # Make file/folder publicly readable
            self.service.permissions().create(
                fileId=file_id,
                body={
                    'type': 'anyone',
                    'role': 'reader'
                }
            ).execute()

            # Get link
            file = self.service.files().get(
                fileId=file_id,
                fields='webViewLink'
            ).execute()

            link = file.get('webViewLink')

            logger.info("shareable_link_created", file_id=file_id, link=link)

            return link

        except Exception as e:
            logger.error(
                "shareable_link_failed",
                file_id=file_id,
                error=str(e),
                exc_info=True
            )
            raise


# Example usage
if __name__ == "__main__":
    # This is just an example - normally called from batch_generate.py
    print("Google Drive Uploader Module")
    print("Import this module and use GoogleDriveUploader class")
    print("\nExample:")
    print("  from batch.google_drive_uploader import GoogleDriveUploader")
    print("  uploader = GoogleDriveUploader('credentials/google_drive_credentials.json')")
    print("  result = uploader.upload_directory(Path('batch_output/batch_123'), 'drive_folder_id')")

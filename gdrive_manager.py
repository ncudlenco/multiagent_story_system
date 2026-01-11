"""
Google Drive Integration Manager for VMware Orchestrator

Handles Google Drive authentication, subfolder creation, and credential
distribution to worker VMs for batch story generation.
"""

import os
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import structlog

logger = structlog.get_logger(__name__)

# Google API imports (optional - graceful degradation if not installed)
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

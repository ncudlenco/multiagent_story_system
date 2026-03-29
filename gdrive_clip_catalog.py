"""
Google Drive Clip Catalog Generator

Traverses Google Drive folder trees, finds all .mp4 videos, classifies them
by model type (WAN, Veo, GEST), and writes the catalog to a Google Spreadsheet.

Usage:
    python gdrive_clip_catalog.py FOLDER_ID1 FOLDER_ID2 --verbose
    python gdrive_clip_catalog.py FOLDER_ID --sheet-name "My Clips"
"""

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass
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
    from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
    GOOGLE_DRIVE_AVAILABLE = True
except ImportError:
    GOOGLE_DRIVE_AVAILABLE = False
    logger.warning("google_drive_dependencies_not_available",
                   message="Install with: pip install google-auth google-api-python-client google-auth-oauthlib")

# Subfolders that never contain mp4 files or texts.json — skip to save API calls
SKIP_FOLDER_NAMES = frozenset({
    'detailed_graph', 'detail', 'textual_description', 'logs',
})

FOLDER_MIME = 'application/vnd.google-apps.folder'


@dataclass
class ClipEntry:
    """A single row in the clip catalog."""
    clip_id: str
    clip_name: str
    clip_source: str
    description: str


class GDriveClipCatalog:
    """Traverses Google Drive folders and catalogs .mp4 clips."""

    # Use full drive scope to see ALL files (not just app-created ones)
    SCOPES = ['https://www.googleapis.com/auth/drive']

    def __init__(self, credentials_path: str = "credentials/google_drive_credentials.json",
                 token_path: str = "credentials/token.json"):
        if not GOOGLE_DRIVE_AVAILABLE:
            raise ImportError("Google Drive dependencies not installed")

        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
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

        self.service = build('drive', 'v3', credentials=creds)
        logger.info("gdrive_authenticated")

    def _list_paginated(self, query: str,
                        fields: str) -> List[Dict[str, Any]]:
        """List files/folders with pagination and shared drive support."""
        all_items = []
        page_token = None

        while True:
            response = self.service.files().list(
                q=query,
                fields=f"nextPageToken, {fields}",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()

            all_items.extend(response.get('files', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                break

        return all_items

    def _list_folder_contents(self, folder_id: str
                              ) -> Tuple[List[Dict], List[Dict]]:
        """List all items in a folder in a single API call.

        Returns (subfolders, files).
        """
        query = f"'{folder_id}' in parents and trashed=false"
        items = self._list_paginated(query, "files(id, name, mimeType)")
        folders = [i for i in items if i.get('mimeType') == FOLDER_MIME]
        files = [i for i in items if i.get('mimeType') != FOLDER_MIME]
        return folders, files

    def _download_json(self, file_id: str) -> Optional[dict]:
        """Download and parse a JSON file from Drive."""
        try:
            request = self.service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)

            done = False
            while not done:
                _, done = downloader.next_chunk()

            fh.seek(0)
            content = fh.read().decode('utf-8')
            fh.close()
            return json.loads(content)
        except Exception as e:
            logger.debug("download_json_error", file_id=file_id, error=str(e))
            return None

    @staticmethod
    def _classify_clip_source(filename: str) -> str:
        """Classify clip source from filename.

        wan*.mp4 -> WAN, veo*.mp4 -> Veo, raw.mp4 -> GEST.
        """
        lower = filename.lower()
        if lower.startswith("wan"):
            return "WAN"
        if lower.startswith("veo"):
            return "Veo"
        if lower == "raw.mp4":
            return "GEST"
        return "UNKNOWN"

    def _find_mp4s_recursive(self, folder_id: str) -> List[Dict[str, Any]]:
        """Find all .mp4 files at any depth under a folder."""
        results = []
        subfolders, files = self._list_folder_contents(folder_id)

        for f in files:
            if f['name'].lower().endswith('.mp4'):
                results.append(f)

        for subfolder in subfolders:
            results.extend(self._find_mp4s_recursive(subfolder['id']))

        return results

    def diagnose(self, folder_ids: List[str]) -> None:
        """Print detailed contents of first few folders for debugging."""
        print("=== DIAGNOSE MODE ===\n")
        for folder_id in folder_ids:
            try:
                meta = self.service.files().get(
                    fileId=folder_id, fields="name, driveId, mimeType",
                    supportsAllDrives=True).execute()
                drive_id = meta.get('driveId', 'My Drive')
                print(f"Root: {meta.get('name', folder_id)} "
                      f"(drive: {drive_id})")
            except HttpError as e:
                print(f"Root: {folder_id} (error getting metadata: {e})")

            subfolders, files = self._list_folder_contents(folder_id)
            print(f"  Direct children: {len(subfolders)} folders, "
                  f"{len(files)} files")
            for f in files[:10]:
                print(f"    FILE: {f['name']} ({f.get('mimeType', '?')})")
            for sf in subfolders[:5]:
                print(f"    FOLDER: {sf['name']}/")
                # One level deeper
                sub2_folders, sub2_files = self._list_folder_contents(
                    sf['id'])
                print(f"      Children: {len(sub2_folders)} folders, "
                      f"{len(sub2_files)} files")
                for f in sub2_files[:10]:
                    print(f"        FILE: {f['name']} "
                          f"({f.get('mimeType', '?')})")
                for sf2 in sub2_folders[:5]:
                    print(f"        FOLDER: {sf2['name']}/")
                    # Two levels deeper
                    sub3_folders, sub3_files = self._list_folder_contents(
                        sf2['id'])
                    print(f"          Children: {len(sub3_folders)} folders, "
                          f"{len(sub3_files)} files")
                    for f in sub3_files[:10]:
                        print(f"            FILE: {f['name']} "
                              f"({f.get('mimeType', '?')})")
                    for sf3 in sub3_folders[:3]:
                        print(f"            FOLDER: {sf3['name']}/")
            print()
        print("=== END DIAGNOSE ===\n")

    def run(self, folder_ids: List[str],
            description_key: str = "gpt-4o_withGEST_t-1.0",
            verbose: bool = False) -> Iterator[ClipEntry]:
        """Traverse folders and yield clip catalog entries.

        At each folder:
        1. List all contents in a single API call
        2. If texts.json found: download description, collect all mp4s
           recursively under this folder, yield entries, stop recursing
        3. If mp4 files found (no texts.json): yield them with empty description
        4. Recurse into subfolders (skipping known irrelevant names)
        """
        visited = set()
        self._api_calls = 0

        def _get_folder_name(folder_id: str) -> str:
            try:
                result = self.service.files().get(
                    fileId=folder_id, fields="name",
                    supportsAllDrives=True).execute()
                return result.get('name', folder_id)
            except HttpError:
                return folder_id

        def traverse(folder_id: str, path: str = "",
                     depth: int = 0, sim_name: str = ""):
            if folder_id in visited:
                return
            visited.add(folder_id)

            subfolders, files = self._list_folder_contents(folder_id)
            self._api_calls += 1

            current_name = path.rsplit("/", 1)[-1] if "/" in path else path

            # Check for texts.json
            texts_file = next(
                (f for f in files if f['name'] == 'texts.json'), None)

            # Check for mp4 files at this level
            mp4_files = [f for f in files
                         if f['name'].lower().endswith('.mp4')]

            if texts_file:
                # This is a simulation folder — download description
                folder_name = current_name or _get_folder_name(folder_id)
                texts_data = self._download_json(texts_file['id'])
                description = ""
                if texts_data and description_key in texts_data:
                    desc_value = texts_data[description_key]
                    description = (desc_value if isinstance(desc_value, str)
                                   else str(desc_value))
                elif texts_data:
                    logger.warning("description_key_missing",
                                   folder=folder_name,
                                   key=description_key,
                                   available_keys=list(texts_data.keys())[:10])

                # Yield mp4s at this level
                for mp4 in mp4_files:
                    yield ClipEntry(
                        clip_id=mp4['id'],
                        clip_name=folder_name,
                        clip_source=self._classify_clip_source(mp4['name']),
                        description=description,
                    )

                # Find mp4s in subfolders (e.g. simulations/camera1/raw.mp4)
                for subfolder in subfolders:
                    deep_mp4s = self._find_mp4s_recursive(subfolder['id'])
                    for mp4 in deep_mp4s:
                        yield ClipEntry(
                            clip_id=mp4['id'],
                            clip_name=folder_name,
                            clip_source=self._classify_clip_source(
                                mp4['name']),
                            description=description,
                        )

                if verbose:
                    print(f"  {'  ' * depth}{folder_name}/ "
                          f"(texts.json found, mp4s collected)")
                return

            # No texts.json — yield any mp4s here with empty description
            if mp4_files:
                folder_name = sim_name or current_name or _get_folder_name(
                    folder_id)
                for mp4 in mp4_files:
                    yield ClipEntry(
                        clip_id=mp4['id'],
                        clip_name=folder_name,
                        clip_source=self._classify_clip_source(mp4['name']),
                        description="",
                    )
                if verbose:
                    print(f"  {'  ' * depth}{current_name}/ "
                          f"({len(mp4_files)} mp4s, no texts.json)")

            # Recurse into subfolders, skipping irrelevant ones
            for subfolder in subfolders:
                if subfolder['name'] in SKIP_FOLDER_NAMES:
                    continue
                sub_path = (f"{path}/{subfolder['name']}"
                            if path else subfolder['name'])
                child_sim = sim_name or current_name
                if verbose and depth < 2:
                    print(f"  {'  ' * depth}Scanning: {sub_path}/")
                yield from traverse(subfolder['id'], sub_path,
                                    depth + 1, child_sim)

        for folder_id in folder_ids:
            if verbose:
                print(f"Scanning root: {folder_id}")
            yield from traverse(folder_id)

        if verbose:
            print(f"\nTotal API calls: {self._api_calls}")

    def write_to_sheet(self, entries: List[ClipEntry],
                       title: str = "Clip Catalog",
                       output_folder_id: Optional[str] = None) -> str:
        """Create a Google Spreadsheet from clip entries via CSV upload.

        Uses Drive API mimeType conversion (CSV -> Google Sheets) so no
        Sheets API scope is needed.

        Returns the spreadsheet URL.
        """
        # Build CSV in memory
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["clipId", "clipName", "clipSource", "Description"])
        for entry in entries:
            clean_desc = entry.description.replace('\n', ' ').replace('\r', '')
            writer.writerow([entry.clip_id, entry.clip_name,
                             entry.clip_source, clean_desc])

        csv_bytes = buf.getvalue().encode('utf-8')
        buf.close()

        # Upload CSV as Google Spreadsheet (Drive API converts automatically)
        file_metadata = {
            'name': title,
            'mimeType': 'application/vnd.google-apps.spreadsheet',
        }
        if output_folder_id:
            file_metadata['parents'] = [output_folder_id]

        media = MediaIoBaseUpload(
            io.BytesIO(csv_bytes),
            mimetype='text/csv',
            resumable=True,
        )
        spreadsheet = self.service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink',
            supportsAllDrives=True,
        ).execute()

        spreadsheet_url = spreadsheet.get('webViewLink', '')
        logger.info("spreadsheet_created",
                     id=spreadsheet['id'], url=spreadsheet_url)
        return spreadsheet_url


def main():
    parser = argparse.ArgumentParser(
        description="Generate clip catalog in a Google Spreadsheet from Drive simulation folders"
    )
    parser.add_argument(
        "folder_ids",
        nargs="+",
        help="Google Drive folder ID(s) to scan recursively"
    )
    parser.add_argument(
        "--sheet-name",
        default="Clip Catalog",
        help="Name for the created Google Spreadsheet (default: Clip Catalog)"
    )
    parser.add_argument(
        "--description-key",
        default="gpt-4o_withGEST_t-1.0",
        help="Key in texts.json for Description column (default: gpt-4o_withGEST_t-1.0)"
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
        "--output-folder-id",
        default=None,
        help="Google Drive folder ID to place the spreadsheet in (default: My Drive root)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show traversal progress"
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Print folder contents for debugging (no catalog generated)"
    )

    args = parser.parse_args()

    if not GOOGLE_DRIVE_AVAILABLE:
        print("Error: Google Drive dependencies not installed.")
        print("Install with: pip install google-auth "
              "google-api-python-client google-auth-oauthlib")
        return 1

    try:
        catalog = GDriveClipCatalog(
            credentials_path=args.credentials,
            token_path=args.token,
        )

        if args.diagnose:
            catalog.diagnose(args.folder_ids)
            return 0

        entries = list(catalog.run(
            folder_ids=args.folder_ids,
            description_key=args.description_key,
            verbose=args.verbose,
        ))

        print(f"\nFound {len(entries)} clips. Writing to Google Spreadsheet...")

        url = catalog.write_to_sheet(entries, title=args.sheet_name,
                                     output_folder_id=args.output_folder_id)

        print(f"Done! {len(entries)} clips written.")
        print(f"Spreadsheet: {url}")

        return 0

    except Exception as e:
        logger.error("clip_catalog_failed", error=str(e), exc_info=True)
        print(f"\nError: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    exit(main())

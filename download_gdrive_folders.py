"""
Download Google Drive folders to local disk, skipping segmentation files.

Usage:
    python download_gdrive_folders.py --folders ID1 ID2 ID3 --dest /home/hpc/captioning/data/

Skips: segmentation_frames.zip, segmentation_mapping.json
Downloads: raw.mp4, spatial_relations.zip, and everything else.

Install deps:
    pip install google-auth google-api-python-client google-auth-oauthlib tqdm
"""

import argparse
import sys
from pathlib import Path

from tqdm import tqdm

SKIP_NAMES = {"segmentation_frames.zip", "segmentation_mapping.json"}

SCRIPT_DIR = Path(__file__).parent
CREDENTIALS_PATH = SCRIPT_DIR / "credentials" / "google_drive_credentials.json"
TOKEN_PATH = SCRIPT_DIR / "credentials" / "token.json"

SCOPES = ["https://www.googleapis.com/auth/drive"]


def authenticate():
    """Authenticate with Google Drive. Uses cached token if available,
    falls back to console-based OAuth for headless servers."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired token...")
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                print(f"ERROR: Credentials file not found: {CREDENTIALS_PATH}")
                sys.exit(1)

            print("No valid token found. Starting OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), SCOPES
            )
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
            auth_url, _ = flow.authorization_url(access_type="offline")
            print(f"\nOpen this URL in your browser:\n\n  {auth_url}\n")
            code = input("Paste the authorization code here: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials

        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        print(f"Token saved to {TOKEN_PATH}")

    service = build("drive", "v3", credentials=creds)
    return service


def get_folder_name(service, folder_id):
    try:
        meta = service.files().get(fileId=folder_id, fields="name").execute()
        return meta.get("name", folder_id)
    except Exception:
        return folder_id


def list_items(service, folder_id):
    """List all files and subfolders in a Drive folder (paginated)."""
    items = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed=false"
    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, size)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        items.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return items


def collect_files(service, folder_id, local_path):
    """Recursively collect all (file_meta, dest_path) pairs to download, skipping SKIP_NAMES."""
    local_path = Path(local_path)
    items = list_items(service, folder_id)
    folders = [i for i in items if i["mimeType"] == "application/vnd.google-apps.folder"]
    files = [i for i in items if i["mimeType"] != "application/vnd.google-apps.folder"]

    result = []
    for f in files:
        if f["name"] not in SKIP_NAMES:
            result.append((f, local_path / f["name"]))

    for folder in folders:
        result.extend(collect_files(service, folder["id"], local_path / folder["name"]))

    return result


def download_file(service, file_id, dest_path, pbar):
    """Stream a file from Drive to dest_path, updating the overall progress bar."""
    from googleapiclient.http import MediaIoBaseDownload

    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=32 * 1024 * 1024)
        done = False
        last_bytes = 0
        while not done:
            status, done = downloader.next_chunk()
            if status:
                current_bytes = int(status.resumable_progress)
                pbar.update(current_bytes - last_bytes)
                last_bytes = current_bytes


def main():
    parser = argparse.ArgumentParser(description="Download GDrive folders, skip segmentation files.")
    parser.add_argument(
        "--folders",
        nargs="+",
        required=True,
        metavar="FOLDER_ID",
        help="One or more Google Drive folder IDs to download",
    )
    parser.add_argument(
        "--dest",
        default="/home/hpc/captioning/data/",
        help="Local destination directory (default: /home/hpc/captioning/data/)",
    )
    args = parser.parse_args()

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    print("Authenticating with Google Drive...")
    service = authenticate()
    print("Authenticated.\n")

    # Phase 1: collect all files across all folders
    print("Scanning folders...")
    all_tasks = []  # list of (file_meta, dest_path, folder_name)
    for folder_id in args.folders:
        folder_name = get_folder_name(service, folder_id)
        print(f"  {folder_name} ({folder_id})")
        tasks = collect_files(service, folder_id, dest / folder_name)
        all_tasks.extend((f, p, folder_name) for f, p in tasks)

    # Separate already-downloaded files
    pending = []
    skipped = 0
    total_bytes = 0
    for f, dest_path, folder_name in all_tasks:
        size = int(f.get("size", 0))
        if dest_path.exists() and dest_path.stat().st_size == size:
            skipped += 1
        else:
            pending.append((f, dest_path, folder_name))
            total_bytes += size

    print(f"\nFound {len(all_tasks)} files across {len(args.folders)} folder(s).")
    if skipped:
        print(f"  {skipped} already downloaded — skipping.")
    print(f"  {len(pending)} to download  ({total_bytes / 1e9:.2f} GB)\n")

    if not pending:
        print("Nothing to do.")
        return

    # Phase 2: download with a single overall progress bar
    with tqdm(total=total_bytes, unit="B", unit_scale=True, unit_divisor=1024,
              desc="Total progress") as pbar:
        current_folder = None
        for f, dest_path, folder_name in pending:
            if folder_name != current_folder:
                current_folder = folder_name
                tqdm.write(f"\n=== {folder_name} ===")
            tqdm.write(f"  {dest_path.relative_to(dest)}")
            download_file(service, f["id"], dest_path, pbar)

    print("\nAll done.")


if __name__ == "__main__":
    main()

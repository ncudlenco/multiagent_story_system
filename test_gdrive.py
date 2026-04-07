"""Quick test: verify Google Drive batch uploader can authenticate and create a folder."""
import sys

PARENT_FOLDER = "null"

if __name__ == "__main__":
    print("Testing batch uploader (used by VMs)...")
    try:
        from batch.google_drive_uploader import GoogleDriveUploader
        u = GoogleDriveUploader('credentials/google_drive_credentials.json')
        print("[OK] GoogleDriveUploader initialized and authenticated")

        folder_id = u.create_folder("_test_delete_me", PARENT_FOLDER)
        print(f"[OK] Created test folder: {folder_id}")

        u.service.files().delete(fileId=folder_id).execute()
        print("[OK] Deleted test folder")
        print("\nBatch uploader works. VMs will upload successfully.")

    except Exception as e:
        print(f"[FAILED] {e}")
        sys.exit(1)

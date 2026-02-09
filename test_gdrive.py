from gdrive_manager import GDriveManager, GOOGLE_DRIVE_AVAILABLE

if __name__ == "__main__":
    if not GOOGLE_DRIVE_AVAILABLE:
        print("Google Drive API is not available. Please install the required dependencies.")
        exit()

    gdrive_manager = GDriveManager()
    gdrive_manager.authenticate()

    # Example usage: List folders in a folder
    folder_id = "16mLTWy4osogLSoig2sdOHUPIHm8QuOBm"  # Replace with your folder ID
    folders = gdrive_manager.list_subfolders(folder_id)
    print("Subfolders:")
    for folder in folders:
        print(f"Name: {folder['name']}, ID: {folder['id']}")

    # Example usage: List files in a folder
    files = gdrive_manager.list_files_in_folder(folder_id)
    print("\nFiles:")
    for file in files:
        print(f"Name: {file['name']}, ID: {file['id']}")
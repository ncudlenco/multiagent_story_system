#!/usr/bin/env python3
"""
Git Pull Script for sv2l and multiagent_story_system repositories.

Pulls the latest changes from both repositories and reports status.
"""

import argparse
import subprocess
import sys
from pathlib import Path


# ANSI color codes for terminal output
class Colors:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def run_git_command(repo_path: Path, args: list[str]) -> tuple[bool, str]:
    """Run a git command in the specified repository.

    Args:
        repo_path: Path to the repository
        args: Git command arguments (e.g., ['pull', '--ff-only'])

    Returns:
        Tuple of (success, output_or_error_message)
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            return False, result.stderr.strip() or result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out after 60 seconds"
    except FileNotFoundError:
        return False, "Git is not installed or not in PATH"
    except Exception as e:
        return False, str(e)


def get_current_branch(repo_path: Path) -> str:
    """Get the current branch name."""
    success, output = run_git_command(repo_path, ["branch", "--show-current"])
    return output if success else "unknown"


def has_uncommitted_changes(repo_path: Path) -> bool:
    """Check if there are uncommitted changes."""
    success, output = run_git_command(repo_path, ["status", "--porcelain"])
    return bool(output) if success else False


def stash_changes(repo_path: Path) -> tuple[bool, str]:
    """Stash uncommitted changes."""
    return run_git_command(repo_path, ["stash", "push", "-m", "Auto-stash by git_pull_all.py"])


def stash_pop(repo_path: Path) -> tuple[bool, str]:
    """Pop stashed changes."""
    return run_git_command(repo_path, ["stash", "pop"])


def fetch_repo(repo_path: Path) -> tuple[bool, str]:
    """Fetch from remote."""
    return run_git_command(repo_path, ["fetch"])


def pull_repo(repo_path: Path) -> tuple[bool, str]:
    """Pull latest changes."""
    return run_git_command(repo_path, ["pull", "--ff-only"])


def get_commits_behind(repo_path: Path) -> int:
    """Get number of commits behind remote."""
    success, output = run_git_command(
        repo_path,
        ["rev-list", "--count", "HEAD..@{upstream}"]
    )
    try:
        return int(output) if success else 0
    except ValueError:
        return 0


def print_header(text: str) -> None:
    """Print a colored header."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{text}{Colors.RESET}")


def print_success(text: str) -> None:
    """Print success message."""
    print(f"  {Colors.GREEN}{text}{Colors.RESET}")


def print_warning(text: str) -> None:
    """Print warning message."""
    print(f"  {Colors.YELLOW}{text}{Colors.RESET}")


def print_error(text: str) -> None:
    """Print error message."""
    print(f"  {Colors.RED}{text}{Colors.RESET}")


def print_info(text: str) -> None:
    """Print info message."""
    print(f"  {text}")


def process_repo(repo_path: Path, repo_name: str, stash: bool = False) -> bool:
    """Process a single repository.

    Args:
        repo_path: Path to the repository
        repo_name: Display name for the repository
        stash: Whether to stash uncommitted changes before pulling

    Returns:
        True if pull was successful, False otherwise
    """
    print_header(f"[{repo_name}]")

    # Check if path exists
    if not repo_path.exists():
        print_error(f"Repository not found at: {repo_path}")
        return False

    # Check if it's a git repo
    if not (repo_path / ".git").exists():
        print_error(f"Not a git repository: {repo_path}")
        return False

    # Show current branch
    branch = get_current_branch(repo_path)
    print_info(f"Branch: {branch}")

    # Check for uncommitted changes
    has_changes = has_uncommitted_changes(repo_path)
    stashed = False

    if has_changes:
        if stash:
            print_warning("Uncommitted changes detected, stashing...")
            success, msg = stash_changes(repo_path)
            if success:
                stashed = True
                print_success("Changes stashed")
            else:
                print_error(f"Failed to stash: {msg}")
                return False
        else:
            print_warning("Uncommitted changes detected (use --stash to auto-stash)")

    # Fetch first to check for updates
    print_info("Fetching...")
    success, msg = fetch_repo(repo_path)
    if not success:
        print_error(f"Fetch failed: {msg}")
        return False

    # Check how many commits behind
    commits_behind = get_commits_behind(repo_path)

    if commits_behind == 0:
        print_success("Already up to date")
    else:
        print_info(f"Pulling ({commits_behind} commit{'s' if commits_behind != 1 else ''} behind)...")
        success, msg = pull_repo(repo_path)

        if success:
            print_success(f"Updated successfully")
        else:
            print_error(f"Pull failed: {msg}")
            # Restore stash if we stashed earlier
            if stashed:
                print_info("Restoring stashed changes...")
                stash_pop(repo_path)
            return False

    # Restore stashed changes
    if stashed:
        print_info("Restoring stashed changes...")
        success, msg = stash_pop(repo_path)
        if success:
            print_success("Stash restored")
        else:
            print_warning(f"Failed to restore stash: {msg}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Pull latest changes from sv2l and multiagent_story_system repositories"
    )
    parser.add_argument(
        "--stash",
        action="store_true",
        help="Automatically stash uncommitted changes before pulling"
    )
    args = parser.parse_args()

    # Get script directory (multiagent_story_system)
    script_dir = Path(__file__).parent.resolve()

    # Define repository paths
    repos = {
        "sv2l": script_dir.parent / "sv2l",
        "multiagent_story_system": script_dir,
    }

    print(f"{Colors.BOLD}=== Git Pull All Repositories ==={Colors.RESET}")

    results = {}
    for name, path in repos.items():
        results[name] = process_repo(path, name, stash=args.stash)

    # Summary
    print(f"\n{Colors.BOLD}=== Summary ==={Colors.RESET}")
    all_success = True
    for name, success in results.items():
        if success:
            print_success(f"{name}: OK")
        else:
            print_error(f"{name}: FAILED")
            all_success = False

    print()
    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())

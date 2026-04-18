"""Local file skills — read and list files from the shared /ai-files mount."""
import logging
from pathlib import Path

log = logging.getLogger("openclaw.file_skills")

AI_FILES_DIR = Path("/ai-files")
MAX_FILE_SIZE = 512 * 1024  # 512 KB cap


def _is_safe_path(path: Path) -> bool:
    """Ensure path is inside AI_FILES_DIR (prevent traversal)."""
    try:
        path.resolve().relative_to(AI_FILES_DIR.resolve())
        return True
    except ValueError:
        return False


async def list_local_files(directory: str = "/ai-files") -> str:
    """List files in the shared AI files directory (or a subdirectory of it).

    Args:
        directory: Path to list. Must be /ai-files or a subdirectory.

    Returns:
        Formatted list of files with sizes and types.
    """
    import asyncio

    target = Path(directory)
    if not _is_safe_path(target):
        return f"Error: directory must be within /ai-files"

    def _list():
        if not target.exists():
            return f"Directory not found: {directory}"
        if not target.is_dir():
            return f"Not a directory: {directory}"

        entries = []
        for item in sorted(target.iterdir()):
            size = item.stat().st_size if item.is_file() else 0
            kind = "dir" if item.is_dir() else item.suffix.lstrip(".") or "file"
            size_str = f"{size:,} bytes" if item.is_file() else ""
            entries.append(f"{'/' if item.is_dir() else ' '} {item.name}  {kind}  {size_str}".rstrip())

        if not entries:
            return f"Directory is empty: {directory}"
        return f"Contents of {directory} ({len(entries)} items):\n" + "\n".join(entries)

    return await asyncio.to_thread(_list)


async def read_local_file(path: str) -> str:
    """Read the contents of a file from the shared /ai-files directory.

    Supports text files, markdown, JSON, CSV, code files.
    Binary files (images, Office docs) return a summary instead.

    Args:
        path: File path, e.g. /ai-files/report.md or just report.md (relative to /ai-files)

    Returns:
        File contents as text, or an error message.
    """
    import asyncio

    target = Path(path)
    if not target.is_absolute():
        target = AI_FILES_DIR / path

    if not _is_safe_path(target):
        return f"Error: path must be within /ai-files"

    TEXT_EXTENSIONS = {
        ".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".jsonl",
        ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log",
        ".py", ".js", ".ts", ".sh", ".bash", ".html", ".xml", ".css",
        ".sql", ".r", ".R"
    }

    def _read():
        if not target.exists():
            return f"File not found: {path}"
        if not target.is_file():
            return f"Not a file: {path}"

        size = target.stat().st_size
        if size > MAX_FILE_SIZE:
            return f"File too large to read ({size:,} bytes > {MAX_FILE_SIZE:,} byte limit): {path}"

        suffix = target.suffix.lower()
        if suffix not in TEXT_EXTENSIONS:
            return f"Binary or unsupported file type ({suffix}): {path}. Use a text, markdown, CSV, JSON, or code file."

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            return f"Contents of {path} ({size:,} bytes):\n\n{content}"
        except Exception as e:
            return f"Error reading {path}: {e}"

    return await asyncio.to_thread(_read)


FILE_SKILLS = {
    "list_local_files": list_local_files,
    "read_local_file": read_local_file,
}

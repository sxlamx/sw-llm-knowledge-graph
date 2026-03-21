"""File path sanitization — canonicalize, allowed-root check, blocked extension check."""

from pathlib import Path
from fastapi import HTTPException

from app.config import get_settings

settings = get_settings()

BLOCKED_EXTENSIONS = frozenset(
    [
        # Executables / scripts
        "exe", "sh", "bat", "cmd", "ps1", "py", "rb", "pl", "php",
        # Private keys / certs
        "key", "pem", "p12", "pfx", "crt", "cer",
        # Environment / config
        "env",
        # Databases (ingest only documents)
        "sqlite", "db", "sqlite3",
    ]
)


def validate_folder_path(folder_path: str) -> Path:
    """
    Validate that a folder path is:
    1. An absolute path.
    2. Resolvable on the filesystem (must exist).
    3. Within one of ALLOWED_FOLDER_ROOTS.

    Raises HTTPException(400) on any violation.
    Returns the resolved canonical Path on success.
    """
    try:
        requested = Path(folder_path)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid path: {folder_path!r}")

    if not requested.is_absolute():
        raise HTTPException(status_code=400, detail="folder_path must be an absolute path")

    try:
        canonical = requested.resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"Path does not exist: {folder_path!r}")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied accessing path: {folder_path!r}")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot resolve path: {exc}")

    if not canonical.is_dir():
        raise HTTPException(status_code=400, detail="folder_path must point to a directory")

    allowed_roots = [Path(r).resolve() for r in settings.allowed_folder_roots]
    if not any(
        _path_is_within(canonical, root) for root in allowed_roots
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Path {canonical!r} is not within any allowed root. "
                f"Allowed roots: {[str(r) for r in allowed_roots]}"
            ),
        )

    return canonical


def validate_file_extension(file_path: str) -> None:
    """
    Raise HTTPException(400) if the file extension is in the blocked list.
    Used before reading individual files during ingestion.
    """
    ext = Path(file_path).suffix.lstrip(".").lower()
    if ext in BLOCKED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '.{ext}' is not allowed for ingestion",
        )


def _path_is_within(child: Path, parent: Path) -> bool:
    """Return True if child is within (or equal to) parent after canonicalization."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False

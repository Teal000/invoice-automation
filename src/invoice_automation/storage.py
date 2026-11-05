"""Where original documents are kept.

Content-addressed: a file is stored under its own SHA-256, so identical bytes
occupy one path however many times they arrive. Two arrivals of the same
invoice produce two submissions and two document rows that reference the same
stored blob, which is exactly the relationship the duplicate handling
describes.

The database holds the path; the file is the authority on its own bytes. A
value extracted from a document can therefore always be traced back to the
document it came from, which is the requirement the data contract states.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

#: Extensions this project accepts, mapped to their media type.
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".json": "application/json",
}


class UnsupportedMediaType(ValueError):
    """Raised for a file this version does not accept at all."""


@dataclass(frozen=True)
class StoredFile:
    #: Path relative to the storage root, with forward slashes.
    storage_path: str
    sha256: str
    byte_size: int
    media_type: str
    #: True when these bytes were already on disk from an earlier arrival.
    already_present: bool


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def media_type_for(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    try:
        return MEDIA_TYPES[suffix]
    except KeyError:
        raise UnsupportedMediaType(
            f"{suffix or file_name!r} is not accepted; expected one of "
            + ", ".join(sorted(MEDIA_TYPES))
        ) from None


def store(content: bytes, *, file_name: str, root: Path) -> StoredFile:
    """Write ``content`` under its hash and return where it went.

    Writing is skipped when the path already holds these bytes, because the
    name *is* the hash: re-storing identical content cannot change it. The
    size is verified rather than assumed, so a truncated earlier write is
    repaired instead of being trusted.
    """
    media_type = media_type_for(file_name)
    digest = sha256_of(content)
    suffix = Path(file_name).suffix.lower()

    # Two-character prefix directory: keeps any one directory small.
    relative = f"{digest[:2]}/{digest}{suffix}"
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)

    already_present = destination.exists() and destination.stat().st_size == len(content)
    if not already_present:
        destination.write_bytes(content)

    return StoredFile(
        storage_path=relative,
        sha256=digest,
        byte_size=len(content),
        media_type=media_type,
        already_present=already_present,
    )


def resolve(storage_path: str, root: Path) -> Path:
    """Absolute path of a stored file, refusing to escape the root."""
    candidate = (root / storage_path).resolve()
    root_resolved = root.resolve()
    if root_resolved not in candidate.parents and candidate != root_resolved:
        raise ValueError(f"storage path {storage_path!r} escapes the storage root")
    return candidate

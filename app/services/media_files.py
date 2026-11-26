"""Local upload files and the DB rows that own them (1.35, security S-36).

Every uploaded file lives at `UPLOAD_DIR/<file_id>/<name>` and is served at
`/media/local/<file_id>/<name>`. Exactly two columns may reference such a URL:
`media.storage_url` and `users.avatar_url`. Before 1.35 deleting a row left the
file on disk and the serve route answered from disk without asking the DB, so
"deleted" media stayed public forever.

The rules now:
  * a file is served only while a row references its exact URL
    (`is_referenced`) — so a file orphaned for any reason (failed unlink, a
    crash between save and commit, old residue) is 404, not public;
  * every path that drops a reference calls `unlink_unreferenced` AFTER its
    DB commit, so a rolled-back delete never loses the file. A file whose URL
    is still referenced by any row is never touched;
  * a failed unlink is retried, then logged at ERROR with the path; the file
    is already unservable, and the owner-run orphan sweep
    (`boots/tools/sweep-upload-orphans-1.35.sh`) is the backstop.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media import Media
from app.models.user import User

logger = logging.getLogger(__name__)

# Local filesystem storage fallback (used when S3/MinIO is not configured).
# Read at call time (not bound into callers) so tests can point it at tmp.
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/opt/tetapi/uploads"))

LOCAL_PREFIX = "/media/local/"
UNLINK_ATTEMPTS = 3
_RETRY_DELAY_S = 0.2


def local_url(file_id: str, name: str) -> str:
    return f"{LOCAL_PREFIX}{file_id}/{name}"


def save_local(content: bytes, filename: str) -> str:
    """Save file to local disk, return storage URL."""
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        file_id = uuid.uuid4().hex
        safe_name = Path(filename).name or "file"
        dest = UPLOAD_DIR / file_id / safe_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return local_url(file_id, safe_name)
    except Exception as e:
        logger.warning("Local storage failed: %s", e)
        return f"local://tetapi-media/{uuid.uuid4()}/{filename}"


def resolve(file_id: str, name: str) -> Path | None:
    """`UPLOAD_DIR/<file_id>/<name>`, or None if it escapes UPLOAD_DIR or is
    not exactly two levels below it (S-1 containment)."""
    base = UPLOAD_DIR.resolve()
    path = (base / file_id / name).resolve()
    if not path.is_relative_to(base) or path.parent.parent != base:
        return None
    return path


def path_for_url(storage_url: str | None) -> Path | None:
    """The on-disk path behind a `/media/local/<file_id>/<name>` URL, or None
    for anything else (S3, `local://` fallbacks, external URLs, garbage)."""
    if not storage_url or not storage_url.startswith(LOCAL_PREFIX):
        return None
    file_id, sep, name = storage_url[len(LOCAL_PREFIX):].partition("/")
    if not sep or not file_id or not name or "/" in name:
        return None
    return resolve(file_id, name)


async def is_referenced(db: AsyncSession, storage_url: str) -> bool:
    """Does any row still own this exact URL? One indexed lookup per table
    (`ix_media_storage_url`, `ix_users_avatar_url`, migration 018)."""
    media_hit = (
        await db.execute(select(Media.id).where(Media.storage_url == storage_url).limit(1))
    ).scalar_one_or_none()
    if media_hit is not None:
        return True
    user_hit = (
        await db.execute(select(User.id).where(User.avatar_url == storage_url).limit(1))
    ).scalar_one_or_none()
    return user_hit is not None


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()  # the <file_id>/ dir; only ever holds this file
    except FileNotFoundError:
        pass


async def unlink_unreferenced(db: AsyncSession, storage_urls: Iterable[str | None]) -> list[str]:
    """Remove the files behind these URLs. Call only AFTER the commit that
    dropped their rows. Returns the URLs whose file could not be removed."""
    failed: list[str] = []
    for url in dict.fromkeys(u for u in storage_urls if u):
        path = path_for_url(url)
        if path is None:
            continue
        if await is_referenced(db, url):
            logger.warning("S-36: %s is still referenced by a row; file kept", url)
            continue
        for attempt in range(1, UNLINK_ATTEMPTS + 1):
            try:
                _remove(path)
                break
            except OSError as e:
                if attempt == UNLINK_ATTEMPTS:
                    logger.error(
                        "S-36: could not remove %s after %d attempts (%s). It is no "
                        "longer served (no row references it); the orphan sweep "
                        "will remove it.",
                        path, attempt, e,
                    )
                    failed.append(url)
                else:
                    await asyncio.sleep(_RETRY_DELAY_S * attempt)
    return failed

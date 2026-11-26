"""1.35 — deleted media must stop being served (security S-36).

Before 1.35 `DELETE /media/{id}` removed only the row, and
`GET /media/local/{file_id}/{name}` answered straight from disk, so "deleted"
bytes stayed public forever. Unit-level like the other suites: route functions
are called directly with a stub session over a tmp `UPLOAD_DIR`, so they run
on the CI runner with no Postgres. The live proof (create a throwaway media,
delete it, its URL 404s, demo media still 200) is done on prod after deploy;
the standing net is infra `probe.py` `s27_file_cleanup`.

Covered:
  * delete -> file and its <file_id>/ dir gone -> GET 404;
  * the unlink happens only after the commit (a failed delete keeps the file);
  * a file with no row -> 404 even though it is on disk;
  * a referenced media file and a referenced avatar stay 200;
  * a file still referenced by another row is never removed;
  * block delete, avatar replace and account deletion remove their files;
  * a failing unlink is retried and then reported, never raised.
"""

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.api.routes import auth as auth_route
from app.api.routes import blocks as blocks_route
from app.api.routes import media as media_route
from app.models.media import Media
from app.services import media_files


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value or []


class _Store:
    """Stub AsyncSession. Lookups by `media.storage_url` / `users.avatar_url`
    are answered from the URL sets (that is the S-36 question); every other
    execute() pops the next queued value. Commit snapshots which files
    exist, so a test can prove the unlink came after it."""

    def __init__(self, media_urls=(), avatar_urls=(), queue=()) -> None:
        self.media_urls = list(media_urls)
        self.avatar_urls = list(avatar_urls)
        self.queue = list(queue)
        self.deleted: list = []
        self.commits = 0
        self.watch: list[Path] = []
        self.existed_at_commit: list[bool] = []

    async def execute(self, stmt):
        where = getattr(stmt, "whereclause", None)
        col = getattr(getattr(where, "left", None), "key", None)
        table = getattr(getattr(getattr(where, "left", None), "table", None), "name", None)
        if (table, col) == ("media", "storage_url"):
            return _Result(uuid.uuid4() if where.right.value in self.media_urls else None)
        if (table, col) == ("users", "avatar_url"):
            return _Result(uuid.uuid4() if where.right.value in self.avatar_urls else None)
        return _Result(self.queue.pop(0) if self.queue else None)

    async def delete(self, obj) -> None:
        self.deleted.append(obj)
        if isinstance(obj, Media) and obj.storage_url in self.media_urls:
            self.media_urls.remove(obj.storage_url)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1
        self.existed_at_commit = [p.exists() for p in self.watch]


@pytest.fixture
def upload_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(media_files, "UPLOAD_DIR", tmp_path)
    return tmp_path


def _file(name: str = "photo.jpg", content: bytes = b"\xff\xd8 private bytes") -> tuple[str, Path]:
    url = media_files.save_local(content, name)
    return url, media_files.path_for_url(url)


def _parts(url: str) -> tuple[str, str]:
    file_id, _, name = url[len(media_files.LOCAL_PREFIX):].partition("/")
    return file_id, name


async def _get(url: str, db: _Store):
    file_id, name = _parts(url)
    return await media_route.serve_local_media(file_id, name, db=db)


class _User:
    def __init__(self, role: str = "user", avatar_url: str | None = None) -> None:
        self.id = uuid.uuid4()
        self.email = f"{self.id.hex[:8]}@example.test"
        self.role = role
        self.full_name = "Someone"
        self.avatar_url = avatar_url
        self.hashed_password = "x"
        self.api_key = None
        self.is_active = True
        self.token_version = 0


# ── serve side ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_referenced_media_file_is_served(upload_dir) -> None:
    url, path = _file()
    resp = await _get(url, _Store(media_urls=[url]))
    assert isinstance(resp, FileResponse)
    assert Path(resp.path) == path


@pytest.mark.asyncio
async def test_referenced_avatar_is_served(upload_dir) -> None:
    url, _ = _file("avatar.png")
    assert isinstance(await _get(url, _Store(avatar_urls=[url])), FileResponse)


@pytest.mark.asyncio
async def test_rowless_file_on_disk_is_404(upload_dir) -> None:
    url, path = _file()
    assert path.is_file()  # the bytes are there …
    with pytest.raises(HTTPException) as exc:
        await _get(url, _Store())  # … but nothing owns them
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_traversal_still_404(upload_dir) -> None:
    for file_id, name in [("..", "etc"), ("..", "../etc/passwd"), ("x", "../../etc/passwd")]:
        with pytest.raises(HTTPException) as exc:
            await media_route.serve_local_media(file_id, name, db=_Store())
        assert exc.value.status_code == 404


# ── DELETE /media/{id} ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_media_removes_file_after_commit_then_404(upload_dir) -> None:
    url, path = _file()
    owner = _User()
    media = Media(id=uuid.uuid4(), block_id=uuid.uuid4(), type="photo", storage_url=url)
    db = _Store(media_urls=[url], queue=[media, owner.id])
    db.watch = [path]

    await media_route.delete_media(media.id, db=db, current_user=owner)

    assert db.deleted == [media]
    assert db.commits == 1 and db.existed_at_commit == [True]  # unlink AFTER commit
    assert not path.exists() and not path.parent.exists()
    with pytest.raises(HTTPException) as exc:
        await _get(url, db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_media_by_non_owner_keeps_file(upload_dir) -> None:
    url, path = _file()
    media = Media(id=uuid.uuid4(), block_id=uuid.uuid4(), type="photo", storage_url=url)
    db = _Store(media_urls=[url], queue=[media, uuid.uuid4()])
    with pytest.raises(HTTPException) as exc:
        await media_route.delete_media(media.id, db=db, current_user=_User())
    assert exc.value.status_code == 403
    assert path.is_file() and db.commits == 0


@pytest.mark.asyncio
async def test_file_still_referenced_by_another_row_is_kept(upload_dir) -> None:
    url, path = _file()
    db = _Store(media_urls=[url])  # some other row still owns this URL
    assert await media_files.unlink_unreferenced(db, [url]) == []
    assert path.is_file()


@pytest.mark.asyncio
async def test_non_local_urls_are_ignored(upload_dir) -> None:
    db = _Store()
    assert await media_files.unlink_unreferenced(
        db, [None, "local://tetapi-media/x/y", "https://example.test/a.jpg", "/media/local/../x"]
    ) == []


@pytest.mark.asyncio
async def test_failed_unlink_is_retried_then_reported(upload_dir, monkeypatch, caplog) -> None:
    url, path = _file()
    calls = []

    def boom(p):
        calls.append(p)
        raise PermissionError("read-only")

    monkeypatch.setattr(media_files, "_remove", boom)
    monkeypatch.setattr(media_files, "_RETRY_DELAY_S", 0)
    failed = await media_files.unlink_unreferenced(_Store(), [url])
    assert failed == [url]
    assert len(calls) == media_files.UNLINK_ATTEMPTS
    assert any("S-36" in r.message and r.levelname == "ERROR" for r in caplog.records)


# ── sibling delete paths ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_block_removes_its_media_files(upload_dir, monkeypatch) -> None:
    url1, p1 = _file("a.jpg")
    url2, p2 = _file("b.jpg")
    keep_url, keep = _file("demo.jpg")  # media of another block — must stay
    block = type("B", (), {"business_id": uuid.uuid4()})()

    async def owned(*a, **k):
        return None

    monkeypatch.setattr(blocks_route, "_get_owned_business", owned)

    class _BlockStore(_Store):
        async def delete(self, obj) -> None:  # ORM cascade drops the block's media rows
            self.deleted.append(obj)
            self.media_urls = [u for u in self.media_urls if u not in (url1, url2)]

    db = _BlockStore(media_urls=[url1, url2, keep_url], queue=[block, [url1, url2]])
    db.watch = [p1, p2]
    await blocks_route.delete_block(uuid.uuid4(), db=db, current_user=_User())

    assert db.existed_at_commit == [True, True]
    assert not p1.exists() and not p2.exists()
    assert keep.is_file()
    assert isinstance(await _get(keep_url, db), FileResponse)


@pytest.mark.asyncio
async def test_avatar_replace_removes_old_file(upload_dir) -> None:
    old_url, old = _file("avatar.png")
    user = _User(avatar_url=old_url)

    class _Upload:
        content_type = "image/png"

        async def read(self):
            return b"\x89PNG new face"

    db = _Store()
    out = await auth_route.upload_avatar(_Upload(), db=db, user=user)
    db.avatar_urls = [out["avatar_url"]]

    assert not old.exists()
    assert media_files.path_for_url(out["avatar_url"]).is_file()
    assert isinstance(await _get(out["avatar_url"], db), FileResponse)


@pytest.mark.asyncio
async def test_account_deletion_removes_avatar(upload_dir) -> None:
    url, path = _file("avatar.jpg")
    user = _User(avatar_url=url)
    db = _Store(queue=[[], []])  # claims, entities
    await auth_route.delete_own_account(db=db, user=user)
    assert user.avatar_url is None
    assert not path.exists()
    with pytest.raises(HTTPException):
        await _get(url, db)

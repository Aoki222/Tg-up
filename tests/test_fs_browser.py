import asyncio
import time
from pathlib import Path
import pytest

from src.api.fs_browser import FsBrowserService, fs_browser


@pytest.mark.asyncio
async def test_fs_browser_list_roots_and_dir(tmp_path: Path) -> None:
    download = tmp_path / "download"
    movies = download / "movies"
    movies.mkdir(parents=True)
    video = movies / "clip.mp4"
    video.write_bytes(b"12345")

    service = FsBrowserService(ttl_seconds=5.0)

    # 1. 扫描根目录
    roots = await service.list_roots([download])
    assert len(roots) == 1
    assert roots[0].is_root is True
    assert roots[0].has_children is True
    assert roots[0].name == "download"

    # 2. 扫描子目录
    items = await service.list_dir(download, [download])
    assert len(items) == 1
    assert items[0].name == "movies"
    assert items[0].is_dir is True
    assert items[0].has_children is True

    # 3. 扫描文件
    movie_items = await service.list_dir(movies, [download])
    assert len(movie_items) == 1
    assert movie_items[0].name == "clip.mp4"
    assert movie_items[0].is_dir is False
    assert movie_items[0].size == 5
    assert movie_items[0].ext == "mp4"


@pytest.mark.asyncio
async def test_fs_browser_caching_and_invalidation(tmp_path: Path) -> None:
    download = tmp_path / "download"
    download.mkdir()
    file1 = download / "f1.txt"
    file1.write_text("hello", encoding="utf-8")

    service = FsBrowserService(ttl_seconds=10.0)

    # 第一次查询
    res1 = await service.list_dir(download, [download])
    assert len(res1) == 1

    # 新建文件（此时在缓存有效期内）
    file2 = download / "f2.txt"
    file2.write_text("world", encoding="utf-8")

    # 第二次查询，应该命中缓存，依然是 1 个
    res2 = await service.list_dir(download, [download])
    assert len(res2) == 1

    # 主动失效缓存
    service.invalidate(str(download.resolve()))

    # 第三次查询，重新读取磁盘，应为 2 个
    res3 = await service.list_dir(download, [download])
    assert len(res3) == 2


@pytest.mark.asyncio
async def test_fs_browser_singleflight_deduplication(tmp_path: Path) -> None:
    download = tmp_path / "download"
    download.mkdir()
    (download / "test.txt").write_text("abc", encoding="utf-8")

    service = FsBrowserService(ttl_seconds=10.0)

    # 同时发起 5 个并发查询
    results = await asyncio.gather(
        service.list_dir(download, [download]),
        service.list_dir(download, [download]),
        service.list_dir(download, [download]),
        service.list_dir(download, [download]),
        service.list_dir(download, [download]),
    )

    assert len(results) == 5
    for r in results:
        assert len(r) == 1
        assert r[0].name == "test.txt"

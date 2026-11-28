"""OCR の結果の置き場（DD-0056）: 同じ画素・同じ呼び方なら OCR を呼ばず、前と同じ値を返す。"""

import os

import numpy as np
import pytest

from analysis import ocr_cache


class FakeOcr:
    def __init__(self):
        self.calls = 0

    def __call__(self, pixels, **kwargs):
        self.calls += 1
        value = float(pixels.mean()) / 3.0
        return [[[[0.1, 0.2], [value, 0.2], [value, 1.7], [0.1, 1.7]], f"T{pixels.shape[0]}", value]], [0.5, 0.1]


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_cache, "_directory", tmp_path)
    return tmp_path


def _pixels(value=200, size=(40, 60)):
    return np.full((*size, 3), value, dtype=np.uint8)


def test_same_pixels_are_read_once_and_give_the_same_value(cache_dir):
    fake = FakeOcr()
    reader = ocr_cache.wrap(fake, "t")
    first = reader(_pixels(), use_cls=False)
    second = reader(_pixels(), use_cls=False)
    assert fake.calls == 1
    assert second == first  # float も JSON で同じ値に戻る


def test_other_pixels_arguments_or_model_are_read_again(cache_dir):
    fake = FakeOcr()
    reader = ocr_cache.wrap(fake, "t")
    reader(_pixels(), use_cls=False)
    reader(_pixels(201), use_cls=False)
    reader(_pixels(size=(60, 40)), use_cls=False)
    reader(_pixels(), use_cls=True)
    ocr_cache.wrap(fake, "other-model")(_pixels(), use_cls=False)
    assert fake.calls == 5


def test_without_a_directory_ocr_always_runs(monkeypatch):
    monkeypatch.setattr(ocr_cache, "_directory", None)
    fake = FakeOcr()
    reader = ocr_cache.wrap(fake, "t")
    reader(_pixels())
    reader(_pixels())
    assert fake.calls == 2


def test_disabled_by_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SEQANNO_OCR_CACHE", "0")
    ocr_cache.configure(tmp_path)
    assert ocr_cache.directory() is None
    monkeypatch.setattr(ocr_cache, "_directory", None)


def test_broken_entry_is_read_again(cache_dir):
    fake = FakeOcr()
    reader = ocr_cache.wrap(fake, "t")
    expected = reader(_pixels())
    for path in cache_dir.glob("*/*.json"):
        path.write_text("{broken", encoding="utf-8")
    assert reader(_pixels()) == expected and fake.calls == 2


def test_nothing_found_is_cached_too(cache_dir):
    calls = []

    def empty(pixels, **kwargs):
        calls.append(1)
        return None, [0.1, 0.0, 0.0]

    reader = ocr_cache.wrap(empty, "t")
    assert reader(_pixels())[0] is None and reader(_pixels())[0] is None
    assert len(calls) == 1


def test_prune_removes_least_recently_used_first(tmp_path):
    folder = tmp_path / "ab"
    folder.mkdir()
    for index in range(10):
        path = folder / f"{index}.json"
        path.write_text("x" * 100, encoding="utf-8")
        os.utime(path, (1000 + index, 1000 + index))
    removed = ocr_cache.prune(tmp_path, 500)
    left = sorted(int(p.stem) for p in folder.glob("*.json"))
    assert removed == 6 and left == [6, 7, 8, 9]
    assert ocr_cache.prune(tmp_path, 500) == 0

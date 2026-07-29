import json

import pytest

from deck_builder.errors import UserContextError
from deck_builder.user_context import load_user_context


def test_none_yields_empty_context():
    ctx = load_user_context(None)
    assert ctx.empty


def test_missing_dir_raises(tmp_path):
    with pytest.raises(UserContextError):
        load_user_context(tmp_path / "nope")


def test_full_context(tmp_path):
    (tmp_path / "notes.md").write_text("  the notes  ")
    (tmp_path / "b.png").write_bytes(b"fake")
    (tmp_path / "a.jpg").write_bytes(b"fake")
    (tmp_path / "readme.txt").write_text("ignored")
    (tmp_path / "captions.json").write_text(json.dumps({"a.jpg": "part photo"}))

    ctx = load_user_context(tmp_path)
    assert ctx.notes == "the notes"
    assert [img.path.name for img in ctx.images] == ["a.jpg", "b.png"]
    assert ctx.images[0].caption == "part photo"
    assert ctx.images[1].caption == ""


def test_bad_captions_degrade_silently(tmp_path):
    (tmp_path / "captions.json").write_text("{not json")
    (tmp_path / "x.png").write_bytes(b"fake")
    ctx = load_user_context(tmp_path)
    assert ctx.images[0].caption == ""

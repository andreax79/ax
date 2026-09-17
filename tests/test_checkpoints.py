"""Undo checkpoints for edit_file / write_file."""

from __future__ import annotations

from corecoder import checkpoints
from corecoder.tools import get_tool


def setup_function():
    checkpoints.clear()


def test_edit_then_undo_restores_previous_bytes(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("v1\n", encoding="utf-8")
    edit_file_tool = get_tool("edit_file")
    assert edit_file_tool is not None
    assert edit_file_tool.execute(str(f), "v1", "v2").startswith("Edited")
    assert f.read_text() == "v2\n"

    assert checkpoints.undo() == f"Restored {f}."
    assert f.read_text() == "v1\n"


def test_undo_removes_file_created_by_write(tmp_path):
    f = tmp_path / "new.py"
    assert not f.exists()
    write_file_tool = get_tool("write_file")
    assert write_file_tool is not None
    write_file_tool.execute(str(f), "print(1)\n")
    assert f.exists()

    assert checkpoints.undo() == f"Removed {f} (created this session)."
    assert not f.exists()


def test_undo_pops_one_mutation_at_a_time(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("v1\n", encoding="utf-8")
    edit_file_tool = get_tool("edit_file")
    edit_file_tool.execute(str(f), "v1", "v2")
    edit_file_tool.execute(str(f), "v2", "v3")

    assert checkpoints.pending() == 2
    checkpoints.undo()
    assert f.read_text() == "v2\n"
    checkpoints.undo()
    assert f.read_text() == "v1\n"


def test_failed_edit_leaves_no_checkpoint(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("v1\n", encoding="utf-8")
    # old_string absent -> error before any write
    edit_file_tool = get_tool("edit_file")
    result = edit_file_tool.execute(str(f), "missing", "v2")
    assert result.startswith("Error:")
    assert checkpoints.pending() == 0
    assert checkpoints.undo() == "Nothing to undo."

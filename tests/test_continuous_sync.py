"""Phase 6 — git hooks installer + file watcher regression tests."""

import subprocess
import time

import pytest

from jidra import git_hooks
from jidra.watcher import JidraWatcher


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


class TestGitHooks:
    def test_install_creates_all_hooks(self, git_repo):
        written = git_hooks.install_hooks(git_repo, git_repo / "graph.db")
        assert set(written) == set(git_hooks.HOOK_NAMES)
        for name in git_hooks.HOOK_NAMES:
            hook = git_repo / ".git" / "hooks" / name
            assert hook.exists()
            text = hook.read_text()
            assert git_hooks.BEGIN in text and git_hooks.END in text
            assert "jidra.cli reindex" in text

    def test_hooks_are_executable(self, git_repo):
        git_hooks.install_hooks(git_repo, git_repo / "graph.db")
        hook = git_repo / ".git" / "hooks" / "post-commit"
        assert hook.stat().st_mode & 0o100  # owner execute bit

    def test_install_preserves_existing_content(self, git_repo):
        hook = git_repo / ".git" / "hooks" / "post-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho husky\n")
        git_hooks.install_hooks(git_repo, git_repo / "graph.db")
        text = hook.read_text()
        assert "echo husky" in text  # other tooling preserved
        assert git_hooks.BEGIN in text

    def test_install_is_idempotent(self, git_repo):
        git_hooks.install_hooks(git_repo, git_repo / "graph.db")
        git_hooks.install_hooks(git_repo, git_repo / "graph.db")
        text = (git_repo / ".git" / "hooks" / "post-commit").read_text()
        assert text.count(git_hooks.BEGIN) == 1  # not duplicated

    def test_uninstall_removes_block_keeps_rest(self, git_repo):
        hook = git_repo / ".git" / "hooks" / "post-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho husky\n")
        git_hooks.install_hooks(git_repo, git_repo / "graph.db")
        removed = git_hooks.uninstall_hooks(git_repo)
        assert "post-commit" in removed
        text = hook.read_text()
        assert "echo husky" in text
        assert git_hooks.BEGIN not in text

    def test_uninstall_deletes_jidra_only_hook(self, git_repo):
        git_hooks.install_hooks(git_repo, git_repo / "graph.db")
        git_hooks.uninstall_hooks(git_repo)
        # post-merge had only our block + shebang -> file removed entirely.
        assert not (git_repo / ".git" / "hooks" / "post-merge").exists()


class TestWatcher:
    def test_relevance_filter(self, tmp_path):
        w = JidraWatcher(tmp_path, tmp_path / "graph.db")
        assert w._is_relevant("src/Foo.java")
        assert w._is_relevant("a/b/c.tsx")
        assert not w._is_relevant("README.md")
        assert not w._is_relevant("node_modules/x/Foo.ts")
        assert not w._is_relevant("build/Gen.java")

    def test_debounced_flush_calls_reindex(self, tmp_path, monkeypatch):
        calls = {}

        def fake_reindex(root, graph, *, hint_changed_files=None):
            calls["files"] = sorted(hint_changed_files or [])
            return {"change_type": "structural"}

        import jidra.reindexer as reindexer

        monkeypatch.setattr(reindexer, "incremental_reindex", fake_reindex)

        seen = []
        w = JidraWatcher(
            tmp_path, tmp_path / "graph.db", on_indexed=lambda s: seen.append(s)
        )
        w.DEBOUNCE_MS = 50
        # Rapid bursts should coalesce into one reindex call.
        w._on_change(str(tmp_path / "A.java"))
        w._on_change(str(tmp_path / "B.java"))
        time.sleep(0.2)
        assert calls["files"] == sorted(
            [str(tmp_path / "A.java"), str(tmp_path / "B.java")]
        )
        assert len(seen) == 1
        w.stop()

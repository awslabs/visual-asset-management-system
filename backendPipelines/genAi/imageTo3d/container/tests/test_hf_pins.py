#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the HuggingFace commit pins and the guard that holds downloads to them.

The guard is what makes a pin hold for the downloads this pipeline does not write itself -- the ones the
upstream libraries make, with no revision, for repositories the adapters never name. So it is tested
where it acts: on huggingface_hub's own entry points, in this process and in a child process started the
way ``run_upstream`` starts one.
"""

import os
import re
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

import generate
import preprocess
from hfguard import hf_pins
from models import base

huggingface_hub = pytest.importorskip("huggingface_hub")


@pytest.mark.unit
class TestThePinTable:
    def test_every_pin_is_a_full_commit(self):
        bad = {repo: rev for repo, rev in hf_pins.HF_REVISIONS.items() if not re.fullmatch(r"[0-9a-f]{40}", rev)}
        assert bad == {}

    def test_every_repo_a_model_or_backend_declares_is_pinned(self):
        """A declared repo with no pin would be refused at its first fetch, on a GPU instance."""
        declared = set(preprocess.SAM_MODEL_REPOS)
        for repos in preprocess.BACKGROUND_REMOVAL_REPOS.values():
            declared.update(repos)
        for adapter in generate.MODEL_ADAPTERS.values():
            declared.update(getattr(adapter, "HF_REPOS", ()))
        assert sorted(r for r in declared if not hf_pins.is_pinned(r)) == []

    def test_the_lookup_ignores_case(self):
        """StableNormal names BiRefNet 'zhengpeng7/BiRefNet'; the hub's spelling is 'ZhengPeng7/BiRefNet'."""
        assert hf_pins.revision_for("zhengpeng7/BiRefNet") == hf_pins.HF_REVISIONS["ZhengPeng7/BiRefNet"]


@pytest.mark.unit
class TestResolvingARevision:
    REPO = "stabilityai/TripoSR"
    PIN = hf_pins.HF_REVISIONS["stabilityai/TripoSR"]

    @pytest.mark.parametrize("asked", [None, "", "main"])
    def test_no_revision_or_main_becomes_the_pin(self, asked):
        assert hf_pins.resolve_revision(self.REPO, asked) == self.PIN

    def test_the_pin_itself_passes(self):
        assert hf_pins.resolve_revision(self.REPO, self.PIN) == self.PIN

    @pytest.mark.parametrize("asked", ["dev", "v1.0", "0" * 40])
    def test_any_other_revision_is_refused(self, asked):
        with pytest.raises(hf_pins.UnpinnedRepositoryError, match="pinned to"):
            hf_pins.resolve_revision(self.REPO, asked)

    def test_a_repo_with_no_pin_is_refused(self):
        with pytest.raises(hf_pins.UnpinnedRepositoryError, match="no pinned commit"):
            hf_pins.resolve_revision("someone/else", None)

    @pytest.mark.parametrize("repo_type", ["dataset", "space"])
    def test_a_repo_that_is_not_a_model_is_refused(self, repo_type):
        with pytest.raises(hf_pins.UnpinnedRepositoryError, match=repo_type):
            hf_pins.resolve_revision(self.REPO, None, repo_type)


@pytest.mark.unit
class TestTheGuardInThisProcess:
    @pytest.fixture
    def recorded(self, monkeypatch):
        """Fake download functions under the guard, recording the revision each call reaches them with."""
        from huggingface_hub import _snapshot_download, file_download

        calls = []

        def fake_file(repo_id, filename, *, revision=None, repo_type=None, **kwargs):
            calls.append(("file", repo_id, revision))
            return "/fake/file"

        def fake_snapshot(repo_id, *, revision=None, repo_type=None, **kwargs):
            calls.append(("snapshot", repo_id, revision))
            return "/fake/snapshot"

        for module, name, fake in ((file_download, "hf_hub_download", fake_file),
                                   (huggingface_hub, "hf_hub_download", fake_file),
                                   (_snapshot_download, "snapshot_download", fake_snapshot),
                                   (huggingface_hub, "snapshot_download", fake_snapshot)):
            monkeypatch.setattr(module, name, fake)
        assert hf_pins.install()
        return calls

    def test_a_download_with_no_revision_reaches_the_hub_at_the_pin(self, recorded):
        huggingface_hub.hf_hub_download("facebook/dino-vitb16", "config.json")
        huggingface_hub.snapshot_download(repo_id="stabilityai/stable-diffusion-xl-base-1.0")

        assert recorded == [
            ("file", "facebook/dino-vitb16", hf_pins.HF_REVISIONS["facebook/dino-vitb16"]),
            ("snapshot", "stabilityai/stable-diffusion-xl-base-1.0",
             hf_pins.HF_REVISIONS["stabilityai/stable-diffusion-xl-base-1.0"]),
        ]

    def test_the_module_level_and_reexported_names_are_both_guarded(self, recorded):
        """transformers and diffusers import the functions by name from the package, and
        huggingface_hub's own code calls them from their modules."""
        from huggingface_hub import _snapshot_download, file_download

        file_download.hf_hub_download("stabilityai/TripoSR", "config.yaml", revision="main")
        _snapshot_download.snapshot_download("stabilityai/TripoSR")

        assert [revision for _, _, revision in recorded] == [hf_pins.HF_REVISIONS["stabilityai/TripoSR"]] * 2

    def test_a_download_of_an_unpinned_repo_never_reaches_the_hub(self, recorded):
        with pytest.raises(hf_pins.UnpinnedRepositoryError):
            huggingface_hub.hf_hub_download("someone/else", "config.json")
        assert recorded == []

    def test_installing_twice_wraps_once(self, recorded):
        assert hf_pins.install()
        huggingface_hub.hf_hub_download("stabilityai/TripoSR", "config.yaml")

        assert len(recorded) == 1


@pytest.mark.unit
class TestTheGuardInAnUpstreamProcess:
    """``run_upstream`` puts the guard's directory first on PYTHONPATH; its sitecustomize installs the
    guard before the upstream code runs. Checked in a real child interpreter."""

    def _run(self, code, tmp_path):
        env = dict(os.environ, PYTHONPATH=base.HF_GUARD_DIR, HF_HOME=str(tmp_path), HF_HUB_OFFLINE="1")
        return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,  # nosec B603
                              timeout=120)

    def test_an_unpinned_download_is_refused_in_the_child(self, tmp_path):
        done = self._run("import huggingface_hub; huggingface_hub.hf_hub_download('someone/else', 'x.json')",
                         tmp_path)

        assert done.returncode != 0
        assert "UnpinnedRepositoryError" in done.stderr and "someone/else" in done.stderr

    def test_a_download_with_no_revision_reads_the_pinned_snapshot_in_the_child(self, tmp_path):
        """With the cache holding an OLDER commit as 'main' and the pinned commit beside it, a call with no
        revision -- the way upstream code calls -- reads the pinned commit's file."""
        repo = "facebook/dino-vitb16"
        repo_dir = Path(tmp_path) / "hub" / "models--facebook--dino-vitb16"
        for revision, body in (("0" * 40, "old"), (hf_pins.HF_REVISIONS[repo], "pinned")):
            snapshot = repo_dir / "snapshots" / revision
            snapshot.mkdir(parents=True)
            (snapshot / "config.json").write_text(body)
        (repo_dir / "refs").mkdir()
        (repo_dir / "refs" / "main").write_text("0" * 40)

        done = self._run(
            "import huggingface_hub; "
            f"print(open(huggingface_hub.hf_hub_download('{repo}', 'config.json')).read())", tmp_path)

        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == "pinned"

    def test_run_upstream_puts_the_guard_first_on_the_path(self, monkeypatch, tmp_path):
        seen = {}

        def fake_run(cmd, cwd=None, env=None, **kwargs):
            seen.update(env)

        monkeypatch.setattr(base.subprocess, "run", fake_run)
        monkeypatch.setenv("PYTHONPATH", "/opt/upstream")
        base.run_upstream(["python", "x.py"], cwd=str(tmp_path), hf_home=str(tmp_path))

        assert seen["PYTHONPATH"].split(os.pathsep) == [base.HF_GUARD_DIR, "/opt/upstream"]
        assert (Path(base.HF_GUARD_DIR) / "sitecustomize.py").is_file()
        # Only the guard lives there, so nothing on the pipeline side can shadow an upstream module.
        assert sorted(p.name for p in Path(base.HF_GUARD_DIR).glob("*.py")) == [
            "__init__.py", "hf_pins.py", "sitecustomize.py"]

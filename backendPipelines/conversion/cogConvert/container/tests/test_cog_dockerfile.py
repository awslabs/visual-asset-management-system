# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The COG conversion image: security updates in every package layer, pinned inputs, non-root runtime."""

import pathlib
import re

import pytest

CONTAINER = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = CONTAINER / "Dockerfile"


def _instructions():
    joined, current = [], ""
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not current and (not line or line.startswith("#")):
            continue
        current = f"{current} {line}" if current else line
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        joined.append(current)
        current = ""
    return joined


@pytest.mark.unit
class TestDockerfile:
    def test_each_package_layer_upgrades_before_it_installs_and_clears_the_lists(self):
        layers = [i for i in _instructions() if i.startswith("RUN ") and "apt-get install" in i]
        assert len(layers) == 2  # a builder stage and the runtime stage
        for layer in layers:
            update, upgrade, install = (layer.find(s) for s in
                                        ("apt-get update", "apt-get upgrade -y", "apt-get install"))
            assert 0 <= update < upgrade < install, layer
            assert layer.endswith("rm -rf /var/lib/apt/lists/*")

    def test_each_stage_is_pinned_by_digest_inline(self):
        # An inline FROM, not `FROM ${ARG}`: a scanner reads the reference as written (CKV_DOCKER_7).
        froms = [i for i in _instructions() if i.startswith("FROM ")]
        assert len(froms) == 2
        for line in froms:
            assert re.search(r"@sha256:[0-9a-f]{64}( AS \w+)?$", line), line
            assert "${" not in line

    def test_the_runtime_stage_is_a_slim_base_that_receives_only_the_gdal_environment(self):
        # The first review found 7 Critical findings under /opt/conda: the runtime stage was the whole miniforge
        # base. The runtime stage is a Debian slim and copies /opt/cog from the build stage and nothing else.
        instructions = _instructions()
        froms = [i for i in instructions if i.startswith("FROM ")]
        assert "miniforge" in froms[0] and "miniforge" not in froms[1] and "slim" in froms[1]
        runtime = instructions[instructions.index(froms[1]):]
        copies = [i for i in runtime if i.startswith("COPY ")]
        assert copies[0] == "COPY --from=build /opt/cog /opt/cog"
        assert [c for c in copies if c.startswith("COPY --from")] == [copies[0]]
        assert not any("conda" in i for i in runtime if i.startswith(("RUN", "ENV", "ENTRYPOINT")))

    def test_conda_packages_come_from_an_explicit_hash_list(self):
        lines = [x for x in (CONTAINER / "conda-explicit.txt").read_text().splitlines()
                 if x and not x.startswith("#")]
        assert lines[0] == "@EXPLICIT"
        packages = lines[1:]
        assert any("/libgdal-core-" in p for p in packages)
        # GPL-2.0 librttopo arrives only with the gpl build of libspatialite; the lgpl build is pinned.
        assert any("/libspatialite-" in p and "-lgpl_" in p for p in packages)
        assert not any("rttopo" in p for p in packages)
        assert all(re.search(r"#[0-9a-f]{64}$", p) for p in packages)
        assert "--file conda-explicit.txt" in DOCKERFILE.read_text()

    def test_python_packages_install_from_the_hash_lock(self):
        assert "--require-hashes" in DOCKERFILE.read_text()
        lock = (CONTAINER / "requirements.txt").read_text()
        assert "--hash=sha256:" in lock and "boto3==" in lock
        assert (CONTAINER / "uv.lock").is_file() and not (CONTAINER / "requirements.lock").exists()
        assert "-r requirements.txt" in DOCKERFILE.read_text()

    def test_the_package_manager_is_removed_in_the_builder_stage(self):
        assert "pip uninstall -y pip" in " ".join(_instructions())

    def test_the_image_drops_to_a_non_root_user_after_normalizing_read_bits(self):
        ins = _instructions()
        users = [i for i in ins if i.startswith("USER ")]
        assert users and users[-1] not in ("USER root", "USER 0")
        copy_index = next(n for n, i in enumerate(ins) if i.startswith("COPY ./cog_convert_pipeline/"))
        assert ins[copy_index + 1].startswith("RUN chmod -R a+rX")

    def test_the_entrypoint_is_an_exec_form_list(self):
        assert [i for i in _instructions() if i.startswith("ENTRYPOINT")][0].startswith('ENTRYPOINT ["')

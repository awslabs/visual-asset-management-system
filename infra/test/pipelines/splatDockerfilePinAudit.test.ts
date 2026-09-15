/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Splat Toolbox Dockerfile's pin posture, and the audit that reports a change to it.
 *
 * This is the third pinning rule in this directory, and the three do not overlap:
 * `containerImagePinning.test.ts` covers `image:` references in the pipeline construct sources;
 * `containerBuildSources.test.ts` covers the tracked buildspecs and the coordinateTransform Dockerfile;
 * this file covers the third-party sources the splat Dockerfile fetches while the image builds.
 *
 * Asserted against an EMBEDDED fixture rather than the file on disk, for the reason
 * `containerBuildSources.test.ts` gives for excluding it from its Dockerfile glob:
 * `backendPipelines/3dRecon/splatToolbox/container/Dockerfile` is gitignored and absent from a fresh
 * checkout, because it arrives from an upstream sync. A test that read it would pass on a machine that
 * has synthesized splat recently and fail in CI. The cost of embedding is that the fixture can drift
 * from upstream; what closes that is `assertRecordedPinPosture`, which the container source sync calls
 * at synth against the real file.
 *
 * The fixture is verbatim, quoting the line ranges named in each chunk marker. It carries the traps a
 * naive matcher gets wrong, and each is asserted by name below:
 *
 *   - Two clones in ONE `RUN` where both are pinned by their own following reset (backgroundremover,
 *     then sam2) — a matcher that stops at the first reset clears only one of them.
 *   - Two clones in ONE `RUN` where only the FIRST is pinned (3dgrut, then AttentiveEraser) — a
 *     per-RUN "has any reset" heuristic silently clears the second.
 *   - A clone pinned by `--branch`, where the branch is a version tag (eigen) — while a `--branch`
 *     naming a moving branch is not a pin at all. The Dockerfile proves branch-shaped values live in
 *     hash-named variables (`ENV DN_SPLATTER_HASH="main"`, never applied), so `--branch` cannot be
 *     read as a pin on its own.
 *   - `pip install git+...@<sha>` (pycolmap, twice) — pinned by the URL, not by a reset, so a matcher
 *     that only knows about resets reports it as unpinned.
 *   - A remote fetch of a repository path at a moving ref (`raw.githubusercontent.com/.../master/...`),
 *     which no clone or `pip git+` matcher sees at all.
 */

import * as fs from "fs";
import * as path from "path";
import {
    auditDockerfilePins,
    assertRecordedPinPosture,
    pinRecordKey,
    RECORDED_UNPINNED_SOURCES,
} from "../../lib/nestedStacks/pipelines/3dRecon/splatToolbox/constructs/dockerfilePinAudit";

/** Verbatim excerpts of the synced Dockerfile at the recorded upstream commit. */
const SYNCED_DOCKERFILE_LINES: string[] = [
    "# --- Dockerfile lines 25-25 ---",
    "FROM pytorch/pytorch:2.9.1-cuda13.0-cudnn9-devel",
    "# --- Dockerfile lines 28-51 ---",
    'ENV PYTHON_VERSION="3.12.0"',
    'ENV PYTHON_VERSION_="3.12"',
    'ENV CUDA_VERSION="13.0"',
    'ENV CUDA_VERSION_="130"',
    'ENV CUDSS_VERSION="0.7.1"',
    'ENV TORCH_VERSION="2.9.1"',
    'ENV TORCH_VISION_VERSION="0.24.1"',
    'ENV CMAKE_VERSION="3.30.3"',
    'ENV NODE_VERSION="22"',
    'ENV EIGEN_VERSION="3.4"',
    'ENV COLMAP_VERSION="4.0.4"',
    'ENV SPLAT_TRANSFORM_VERSION="2.5.2"',
    'ENV MAPANYTHING_VERSION="1.1.2"',
    'ENV SPZ_VERSION="2.1.0"',
    'ENV OPENEXR_VERSION="3.1.5"',
    'ENV OPENIMAGEIO_VERSION="2.5.13.0"',
    "",
    "# Pinned versions",
    'ENV CERES_HASH="0ba987acaf9e8674070f116ed624edf017d2b630"',
    'ENV NERFSTUDIO_HASH="50e0e3c70c775e89333256213363badbf074f29d"',
    'ENV GSPLAT_HASH="4e52698e45eaaed929ed3a5065e96a688d085df6"',
    'ENV THREEDGRUT_HASH="9846a28babb2171802907017c44d3adaa927f4e4"',
    'ENV BGREMOVER_HASH="d6278fef141d1669e5fffbbc4cf185edfdc89cf9"',
    'ENV SAM2_HASH="2b90b9f5ceec907a1c18123530e92e794ad901a4"',
    "# --- Dockerfile lines 206-208 ---",
    "",
    "# Install tiny-cuda-nn latest stable (requires torch to be installed first)",
    "RUN pip install git+https://github.com/NVlabs/tiny-cuda-nn.git#subdirectory=bindings/torch",
    "# --- Dockerfile lines 210-221 ---",
    "# Install Cmake and Eigen",
    "RUN wget https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/cmake-${CMAKE_VERSION}-linux-x86_64.sh \\",
    "    && mkdir /opt/cmake \\",
    "    && sh cmake-${CMAKE_VERSION}-linux-x86_64.sh --prefix=/opt/cmake --skip-license \\",
    "    && ln -sf /opt/cmake/bin/cmake /usr/local/bin/cmake \\",
    "    && rm cmake-${CMAKE_VERSION}-linux-x86_64.sh \\",
    "    && git clone --single-branch --depth=1 --branch ${EIGEN_VERSION} https://gitlab.com/libeigen/eigen.git \\",
    "    && mkdir -p ${CODE_PATH}/eigen/build \\",
    "    && cd ${CODE_PATH}/eigen/build \\",
    '    && cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="/usr/local/bin/eigen/${EIGEN_VERSION}" \\',
    "    && make install \\",
    "    && cd ${CODE_PATH}",
    "# --- Dockerfile lines 240-248 ---",
    "# Build Ceres with cuDSS support",
    "RUN git clone https://github.com/ceres-solver/ceres-solver.git --recursive \\",
    "    && cd ${CODE_PATH}/ceres-solver \\",
    "    && git reset --hard ${CERES_HASH} \\",
    "    && cd ${CODE_PATH} \\",
    "    && mkdir ceres-bin && cd ceres-bin \\",
    '    && cmake -DCUDA=ON -DUSE_CUDSS=ON -DCUDA_ARCHITECTURES="${TCNN_CUDA_ARCHITECTURES}" -Dcudss_DIR=/usr/lib/x86_64-linux-gnu/libcudss/13/cmake/cudss -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF ../ceres-solver \\',
    "    && make -j3 && make install \\",
    "    && cd ${CODE_PATH}",
    "# --- Dockerfile lines 258-271 ---",
    "COPY ./src/pipeline/training/patch_optimizer_save.py /tmp/patch_optimizer_save.py",
    "RUN git clone https://github.com/nerfstudio-project/nerfstudio.git \\",
    "    && cd ${CODE_PATH}/nerfstudio \\",
    "    && git reset --hard ${NERFSTUDIO_HASH} \\",
    "    && find . -name \"*.py\" -exec sed -i 's/torch\\.load(\\([^,)]*\\))/torch.load(\\1, weights_only=False)/g' {} \\; \\",
    "    && find . -name \"*.py\" -exec sed -i 's/torch\\.load(\\([^,)]*,\\s*[^,)]*\\))/torch.load(\\1, weights_only=False)/g' {} \\; \\",
    "    && sed -i '/pycolmap/d' pyproject.toml \\",
    "    && sed -i '/gsplat/s/^/# /' pyproject.toml \\",
    "    && sed -i '/# TODO(1480) enable when pycolmap windows wheels are available/,+1d' pyproject.toml \\",
    "    && sed -i '/@torch_compile()/d' nerfstudio/models/splatfacto.py \\",
    "    && cp /tmp/patch_optimizer_save.py nerfstudio/engine/patch_optimizer_save.py \\",
    "    && sed -i '/^from __future__ import annotations/a from nerfstudio.engine import patch_optimizer_save  # noqa: F401' nerfstudio/engine/trainer.py \\",
    "    && pip install -e . \\",
    "    && cd ${CODE_PATH}",
    "# --- Dockerfile lines 273-285 ---",
    "# Install latest gsplat from source (for splatfacto, splatfacto-big, splatfacto-mcmc)",
    "RUN git clone https://github.com/nerfstudio-project/gsplat.git --recursive \\",
    "    && cd ${CODE_PATH}/gsplat \\",
    "    && git reset --hard ${GSPLAT_HASH} \\",
    '    && sed -i \'/os.environ\\["MASTER_ADDR"\\] = "localhost"/s/^/# /\' gsplat/distributed.py \\',
    "    && sed -i '/os.environ\\[\"MASTER_PORT\"\\] = str(_find_free_port())/s/^/# /' gsplat/distributed.py \\",
    "    && sed -i 's/point_indices = self\\.parser\\.point_indices\\[image_name\\]/point_indices = self.parser.point_indices.get(image_name, np.array([], dtype=np.int32))/' examples/datasets/colmap.py \\",
    "    && python setup.py build_ext --inplace \\",
    "    && python setup.py develop \\",
    "    && pip install -e libs/scene -e libs/stage \\",
    "    && pip install --no-build-isolation -r examples/requirements.txt \\",
    "    && pip install --force-reinstall imageio tqdm \\",
    "    && cd ${CODE_PATH}",
    "# --- Dockerfile lines 291-298 ---",
    "# Install DN-Splatter (dn-splatter + ags-mesh models)",
    'ENV DN_SPLATTER_HASH="main"',
    "RUN git clone https://github.com/maturk/dn-splatter.git ${CODE_PATH}/dn-splatter \\",
    "    && cd ${CODE_PATH}/dn-splatter \\",
    "    && pip install -e . --no-deps \\",
    "    && pip install --no-cache-dir geffnet scikit-learn PyMCubes pymeshlab open3d \\",
    "    && cd ${CODE_PATH}",
    "",
    "# --- Dockerfile lines 312-318 ---",
    "# Install Splatfacto-w with gsplat==1.4.0 to separate location",
    "# Install with dependencies to get complete environment",
    "RUN mkdir -p /opt/splatfacto_w_env \\",
    "    && git clone https://github.com/KevinXu02/splatfacto-w ${CODE_PATH}/splatfacto-w \\",
    "    && pip install --target=/opt/splatfacto_w_env --no-deps gsplat==1.4.0 \\",
    "    && pip install --target=/opt/splatfacto_w_env --upgrade git+https://github.com/KevinXu02/splatfacto-w",
    "",
    "# --- Dockerfile lines 331-333 ---",
    "# Install gsplat-specific pycolmap to separate location",
    "RUN mkdir -p /opt/gsplat_pycolmap \\",
    "    && pip install --target=/opt/gsplat_pycolmap git+https://github.com/rmbrualla/pycolmap@cc7ea4b7301720ac29287dbe450952511b32125e",
    "# --- Dockerfile lines 362-370 ---",
    "",
    "# Download vocab tree as fallback if not provided via S3 models archive",
    "RUN git clone https://github.com/ZachMckennedyFWig/ColmapFaissVocabTrees.git \\",
    "    && mv ${CODE_PATH}/ColmapFaissVocabTrees/vocab_tree_flickr100K_words32K.bin ${CODE_PATH} \\",
    "    && rm -rf ${CODE_PATH}/ColmapFaissVocabTrees",
    "",
    "# Install AWS CLI",
    'RUN wget "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -O "awscliv2.zip" \\',
    "    && unzip awscliv2.zip && ./aws/install && rm -rf aws awscliv2.zip",
    "# --- Dockerfile lines 377-396 ---",
    "",
    "# Install Background Removal and SAM2 in single layer",
    "RUN git clone https://github.com/nadermx/backgroundremover.git \\",
    "    && cd ${CODE_PATH}/backgroundremover \\",
    "    && git reset --hard ${BGREMOVER_HASH} \\",
    "    && sed -i '53s/^        print/        #print/' ${CODE_PATH}/backgroundremover/backgroundremover/u2net/detect.py \\",
    "    && sed -i 's/from moviepy import VideoFileClip/from moviepy.editor import VideoFileClip/' ${CODE_PATH}/backgroundremover/backgroundremover/bg.py \\",
    "    && pip install --no-cache-dir -r ${CODE_PATH}/backgroundremover/requirements.txt \\",
    "    && pip install --force-reinstall --no-cache-dir 'numpy==1.26.4' 'moviepy==1.0.3' \\",
    "    && git clone https://github.com/facebookresearch/sam2.git ${CODE_PATH}/sam \\",
    "    && cd ${CODE_PATH}/sam \\",
    "    && git reset --hard ${SAM2_HASH} \\",
    '    && sed -i \'s/"torch>=2.5.1",/#"torch>=2.5.1",/\' setup.py \\',
    '    && sed -i \'s/"torchvision>=0.20.1",/#"torchvision>=0.20.1",/\' setup.py \\',
    '    && sed -i \'/^    "pillow>=9.4.0",$/a \\    "cog>=0.14.12",\' setup.py \\',
    '    && sed -i \'s/"hydra-core>=1.3.2",/"hydra-core==1.3.2",/\' setup.py \\',
    "    && pip install --no-deps --no-cache-dir -e . \\",
    "    && pip install --no-cache-dir hydra-core==1.3.2 cog>=0.14.12 opencv-python matplotlib \\",
    "    && pip install --force-reinstall --no-cache-dir 'numpy==1.26.4' \\",
    "    && cd ${CODE_PATH}",
    "# --- Dockerfile lines 401-434 ---",
    "",
    "# Build NVIDIA 3DGRUT and Attentive Eraser in single layer",
    "# Stable Diffusion XL model will be downloaded from HuggingFace at runtime if using eraser",
    "RUN git clone --recursive https://github.com/nv-tlabs/3dgrut.git \\",
    "    && cd ${CODE_PATH}/3dgrut \\",
    "    && git reset --hard ${THREEDGRUT_HASH} \\",
    "    && sed -i 's/checkpoint = torch.load(checkpoint_path)/checkpoint = torch.load(checkpoint_path, weights_only=False)/' threedgrut/render.py \\",
    "    && sed -i 's/checkpoint = torch.load(conf.resume)/checkpoint = torch.load(conf.resume, weights_only=False)/' threedgrut/trainer.py \\",
    "    && sed -i '/fused-ssim/d' requirements.txt \\",
    "    && sed -i 's/^xformers/#xformers/' requirements.txt \\",
    "    && python -m pip install --no-build-isolation -r requirements.txt \\",
    "    && python -m pip install --no-build-isolation -e . \\",
    "    && cd ${CODE_PATH} \\",
    "    && git clone https://github.com/Anonym0u3/AttentiveEraser.git \\",
    "    && cd ${CODE_PATH}/AttentiveEraser \\",
    "    && sed -i 's/^torchvision/#torchvision/' requirements.txt \\",
    "    && sed -i 's/^transformers/#transformers/' requirements.txt \\",
    "    && sed -i 's/^xformers/#xformers/' requirements.txt \\",
    "    && sed -i 's/^diffusers/#diffusers/' requirements.txt \\",
    "    && pip install -r requirements.txt \\",
    "    && cd ${CODE_PATH} \\",
    "    && python3 - <<'PATCH_SCRIPT'",
    "import urllib.request, re",
    "",
    "# Download latest SIP pipeline from AttentiveEraser repo",
    "urls = [",
    "    ('https://raw.githubusercontent.com/Anonym0u3/AttentiveEraser/master/pipelines/pipeline_stable_diffusion_xl_attentive_eraser.py',",
    "     '/opt/ml/code/AttentiveEraser/pipelines/pipeline_stable_diffusion_xl_attentive_eraser.py'),",
    "    ('https://raw.githubusercontent.com/Anonym0u3/AttentiveEraser/master/pipelines/pipeline_stable_diffusion_xl_attentive_eraser_inversion.py',",
    "     '/opt/ml/code/AttentiveEraser/pipelines/pipeline_stable_diffusion_xl_attentive_eraser_inversion.py'),",
    "]",
    "for url, path in urls:",
    "    urllib.request.urlretrieve(url, path)",
    "    print(f'Downloaded: {path}')",
    "# --- Dockerfile lines 463-473 ---",
    "RUN python3 ${CODE_PATH}/patch_tracer.py \\",
    "    ${CODE_PATH}/3dgrut/threedgut_tracer/tracer.py \\",
    "    ${CODE_PATH}/3dgrut/threedgrt_tracer/tracer.py \\",
    "    ${CODE_PATH}/3dgrut/threedgrut/strategy/mcmc.py \\",
    "    ${CODE_PATH}/3dgrut/threedgrut/optimizers/__init__.py && \\",
    '    echo "Patches applied successfully" && \\',
    "    rm ${CODE_PATH}/patch_tracer.py",
    "",
    "# Install Node.js, npm, and ffmpeg",
    "RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \\",
    "    && apt-get install -y nodejs ffmpeg",
    "# --- Dockerfile lines 488-490 ---",
    "",
    "# Install light glue",
    "RUN pip install git+https://github.com/cvg/LightGlue.git hydra-core",
    "# --- Dockerfile lines 527-532 ---",
    "",
    "# Install gsplat-specific pycolmap for multi-GPU (MUST BE LAST to avoid being overwritten)",
    "RUN mkdir -p /opt/gsplat_pycolmap \\",
    "    && pip install --target=/opt/gsplat_pycolmap --no-deps git+https://github.com/rmbrualla/pycolmap@cc7ea4b7301720ac29287dbe450952511b32125e \\",
    "    && rm -rf /opt/gsplat_pycolmap/numpy* /opt/gsplat_pycolmap/scipy* \\",
    "    && pip install --target=/opt/gsplat_pycolmap --upgrade 'numpy==1.26.4'",
];

const SYNCED_DOCKERFILE = SYNCED_DOCKERFILE_LINES.join("\n");

const CONSTRUCT_SOURCE = path.join(
    __dirname,
    "..",
    "..",
    "lib",
    "nestedStacks",
    "pipelines",
    "3dRecon",
    "splatToolbox",
    "constructs",
    "splatToolbox-construct.ts"
);

function keysOf(dockerfile: string): string[] {
    return auditDockerfilePins(dockerfile).map(pinRecordKey).sort();
}

describe("splat Dockerfile pin audit", () => {
    const keys = keysOf(SYNCED_DOCKERFILE);

    it("reads a fixture that still carries every trap it claims to", () => {
        // Control. Every absence asserted below is satisfied by a fixture that lost the line, so the
        // lines have to be shown present before their absence from the offender list means anything.
        for (const required of [
            "git clone https://github.com/nadermx/backgroundremover.git",
            "git clone https://github.com/facebookresearch/sam2.git",
            "git reset --hard ${SAM2_HASH}",
            "git clone --recursive https://github.com/nv-tlabs/3dgrut.git",
            "git reset --hard ${THREEDGRUT_HASH}",
            "git clone https://github.com/Anonym0u3/AttentiveEraser.git",
            "--branch ${EIGEN_VERSION} https://gitlab.com/libeigen/eigen.git",
            "git+https://github.com/rmbrualla/pycolmap@cc7ea4b7301720ac29287dbe450952511b32125e",
            'ENV DN_SPLATTER_HASH="main"',
            "raw.githubusercontent.com/Anonym0u3/AttentiveEraser/master/pipelines/",
        ]) {
            expect(SYNCED_DOCKERFILE).toContain(required);
        }
        // Both pycolmap sites, so "pinned by @<sha>" is not proved by one of the two.
        expect(SYNCED_DOCKERFILE.split("rmbrualla/pycolmap@").length - 1).toBe(2);
    });

    it("reports exactly the recorded set of unpinned sources", () => {
        // The count is asserted in band: a matcher that extracted nothing would satisfy every
        // "must be absent" assertion in this file, and set equality alone reads as agreement.
        expect(keys.length).toBe(11);
        expect(keys).toEqual([...RECORDED_UNPINNED_SOURCES].sort());
    });

    it("does not flag a clone pinned by its own reset, including the second in a shared RUN", () => {
        expect(keys).not.toContain("git-clone https://github.com/nadermx/backgroundremover.git");
        expect(keys).not.toContain("git-clone https://github.com/facebookresearch/sam2.git");
        expect(keys).not.toContain("git-clone https://github.com/ceres-solver/ceres-solver.git");
        expect(keys).not.toContain(
            "git-clone https://github.com/nerfstudio-project/nerfstudio.git"
        );
        expect(keys).not.toContain("git-clone https://github.com/nerfstudio-project/gsplat.git");
    });

    it("flags the unpinned clone that shares a RUN with a pinned one", () => {
        // 3dgrut is pinned by the reset that follows it; AttentiveEraser is cloned afterwards in the
        // same RUN with no reset of its own. One of the two must be flagged and the other must not.
        expect(keys).not.toContain("git-clone https://github.com/nv-tlabs/3dgrut.git");
        expect(keys).toContain("git-clone https://github.com/Anonym0u3/AttentiveEraser.git");
    });

    it("treats a version tag on --branch as a pin and a moving branch as not one", () => {
        expect(keys).not.toContain("git-clone https://gitlab.com/libeigen/eigen.git");

        const movingBranch = [
            'ENV SOME_HASH="main"',
            "RUN git clone --branch main https://github.com/example/moving.git ${CODE_PATH}/moving \\",
            "    && cd ${CODE_PATH}/moving",
            "RUN git clone --branch ${SOME_HASH} https://github.com/example/viavar.git",
        ].join("\n");
        expect(keysOf(movingBranch)).toEqual([
            "git-clone https://github.com/example/moving.git",
            "git-clone https://github.com/example/viavar.git",
        ]);
    });

    it("does not flag a pip git+ install pinned by @<revision>", () => {
        expect(keys).not.toContain("pip-git https://github.com/rmbrualla/pycolmap");
        expect(keys).toContain("pip-git https://github.com/NVlabs/tiny-cuda-nn.git");
    });

    it("flags a remote fetch of a repository path at a moving ref", () => {
        // The source class neither a clone nor a pip-git matcher can see. Both files are fetched from
        // the same repository at `master` while the image builds.
        const rawRefs = keys.filter((key) => key.startsWith("raw-ref "));
        expect(rawRefs.length).toBe(2);
        for (const key of rawRefs) {
            expect(key).toContain("/AttentiveEraser/master/pipelines/");
        }

        const pinnedRaw =
            "RUN wget https://raw.githubusercontent.com/example/repo/1.2.3/setup.py -O /tmp/setup.py";
        expect(keysOf(pinnedRaw).filter((key) => key.startsWith("raw-ref "))).toEqual([]);
    });

    it("flags a download whose URL carries no version", () => {
        expect(keys).toContain("download https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip");
        expect(keys).toContain("download https://deb.nodesource.com/setup_${NODE_VERSION}.x");
        // The version-bearing downloads in the same file are not flagged, so the rule is not
        // "every download".
        expect(keys.filter((key) => key.startsWith("download ")).length).toBe(2);
        expect(SYNCED_DOCKERFILE).toContain("cmake-${CMAKE_VERSION}-linux-x86_64.sh");
    });
});

describe("assertRecordedPinPosture", () => {
    it("accepts the recorded Dockerfile", () => {
        expect(() => assertRecordedPinPosture(SYNCED_DOCKERFILE)).not.toThrow();
    });

    it("throws naming an unrecorded unpinned source", () => {
        const withEighthClone = [
            SYNCED_DOCKERFILE,
            "RUN git clone https://github.com/example/newly-added.git ${CODE_PATH}/newly-added \\",
            "    && cd ${CODE_PATH}/newly-added && pip install -e .",
        ].join("\n");
        expect(() => assertRecordedPinPosture(withEighthClone)).toThrow(
            /https:\/\/github\.com\/example\/newly-added\.git/
        );
    });

    it("throws when a recorded source has been pinned upstream", () => {
        // The other direction. Without it a stale record is invisible: the audit would keep reporting a
        // posture the Dockerfile no longer has, and the review-visible record silently becomes wrong.
        const nowPinned = SYNCED_DOCKERFILE.replace(
            "git clone https://github.com/maturk/dn-splatter.git ${CODE_PATH}/dn-splatter \\",
            "git clone https://github.com/maturk/dn-splatter.git ${CODE_PATH}/dn-splatter \\\n" +
                "    && cd ${CODE_PATH}/dn-splatter \\\n" +
                "    && git reset --hard 0123456789abcdef0123456789abcdef01234567 \\"
        );
        expect(nowPinned).not.toEqual(SYNCED_DOCKERFILE);
        expect(() => assertRecordedPinPosture(nowPinned)).toThrow(
            /no longer unpinned.*maturk\/dn-splatter/s
        );
    });

    it("is called on the synced Dockerfile, and its message survives the sync's error wrap", () => {
        // The audit throws from inside syncSplatToolboxContainerSources' try/catch, which re-wraps
        // every error. The wrap must interpolate the cause, or the offending URL never reaches the
        // operator — only "container source sync failed" would.
        const source = fs.readFileSync(CONSTRUCT_SOURCE, "utf8");
        expect(source).toContain("assertRecordedPinPosture(finalDockerfile)");
        expect(source).toContain("Cause: ${error}");
    });
});

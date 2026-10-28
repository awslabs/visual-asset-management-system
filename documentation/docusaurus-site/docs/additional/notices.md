# Notices

This page contains important legal notices, disclaimers, and licensing information for the Visual Asset Management System (VAMS).

---

## Non-Production Disclaimer

:::warning[Non-Production Grade]
Visual Asset Management System (VAMS) is a solution that is near-production-grade at its default configuration. Consult with your organizational security team prior to production use. You are responsible for testing, securing, and optimizing the solution as appropriate for production-grade use based on your specific quality control practices and standards.
:::

Deploying VAMS may incur AWS charges for creating or using AWS chargeable resources, such as running Amazon EC2 instances, using Amazon S3 storage, Amazon DynamoDB tables, Amazon OpenSearch domains, and other services. You are responsible for the cost of AWS services used while running this solution.

---

## Content Security Legal Disclaimer

The sample code, software libraries, command line tools, proofs of concept, templates, or other related technology (including any of the foregoing that are provided by our personnel) is provided to you as AWS Content, as defined in the [Online Customer Agreement](https://aws.amazon.com/agreement/), or the relevant written agreement between you and AWS (whichever applies). You should not use this AWS Content in your production accounts, or on production or other critical data.

You are responsible for testing, securing, and optimizing the AWS Content, such as sample code, as appropriate for production-grade use based on your specific quality control practices and standards.

---

## Third-Party Software Notice

VAMS includes third-party software subject to their respective licenses. This software is provided under the terms and conditions set forth by the third-party developers.

### Third-Party Software Warning

:::info[Third-Party Software]
This solution allows you to interact with third-party software libraries and generative AI (GAI) models from third-party providers. Your use of the software libraries and third-party GAI models is governed by the terms provided to you by the third-party software library and GAI model providers when you acquired your license to use them (for example, their terms of service, license agreement, acceptable use policy, and privacy policy).
:::

You are responsible for ensuring that your use of the third-party software libraries and GAI models comply with the terms governing them, and any laws, rules, regulations, policies, or standards that apply to you.

You are also responsible for making your own independent assessment of the third-party software libraries and GAI models that you use, including their outputs and how third-party software library GAI model providers use any data that might be transmitted to them based on your deployment configuration. AWS does not make any representations, warranties, or guarantees regarding the third-party software libraries and GAI models, which are "Third-Party Content" under your agreement with AWS.

### Key Third-Party Dependencies

The complete list of third-party dependencies and their licenses is maintained in the project's `NOTICE.md` file. Key categories include:

| Category         | Notable Libraries                                                | Licenses                      |
| ---------------- | ---------------------------------------------------------------- | ----------------------------- |
| Web application  | React, Vite, Cloudscape Design System, Amplify                   | MIT, Apache-2.0               |
| 3D visualization | Three.js, BabylonJS, CesiumJS, Potree, PlayCanvas, Needle Engine | MIT, Apache-2.0, BSD-2-Clause |
| Backend          | Pydantic, Casbin, boto3, aws-lambda-powertools                   | MIT, Apache-2.0, MIT-0        |
| 3D conversion    | trimesh, NumPy, Pillow (3D Basic Conversion pipeline)            | MIT, BSD-3-Clause, MIT-CMU    |
| Infrastructure   | AWS CDK, cdk-nag, constructs                                     | Apache-2.0                    |
| CLI              | Click, requests, boto3, pycognito                                | BSD-3-Clause, Apache-2.0      |

### Optional LGPL-Licensed Components

Certain optional components use LGPL-licensed libraries:

-   **OpenCascade.js** -- Dynamically loaded for CAD format support in the Three.js Viewer (STEP, IGES, BREP). Disabled by default and loaded on-demand from a CDN only when explicitly enabled.
-   **CadQuery** -- Used in the CAD/Mesh Metadata Extraction pipeline and the 3D Preview Thumbnail pipeline for STEP/STP file tessellation.
-   **casadi** (LGPL-3.0-or-later) and **nlopt** (LGPL-2.1-or-later) -- Optimization libraries that CadQuery requires, installed in the 3D Preview Thumbnail pipeline image.

:::note
LGPL license terms apply only when these optional features are enabled. Review the license requirements with your legal team before enabling CAD-related features.
:::

The 3D Preview Thumbnail pipeline image also contains MPL-2.0 code: the certifi package (required by requests), the tqdm package (required by open3d), and the Eigen library compiled into open3d. `NOTICE.md` lists all three.

The Robotics Data Viewer is the Lichtblick web app (MPL-2.0), which the web application install builds from the upstream source at a pinned tag and commit and serves beside the web application, with the upstream license and a `SOURCE.txt` naming the source repository, tag and commit. VAMS compiles the upstream files unmodified. `NOTICE.md` lists it.

When the 3D Preview Thumbnail pipeline reads a STEP file, it also loads the native libraries that the cadquery-ocp wheel bundles with Open CASCADE. They include JBIG-KIT (GPL-2.0-or-later), FreeImage (GPL-2.0, GPL-3.0 or the FreeImage Public License), LibRaw (LGPL-2.1 or CDDL-1.0) and FreeType (FreeType License or GPL-2.0-or-later). `NOTICE.md` lists every bundled library with its version and license.

### 3D Preview Thumbnail Gaussian Splat Conversion Notice

The 3D Preview Thumbnail pipeline converts `.spz`, `.sog`, `.ksplat`, `.lcc`, `.lcc2` and `.compressed.ply` Gaussian splats with the PlayCanvas [splat-transform](https://github.com/playcanvas/splat-transform) CLI (MIT license), installed at an exact version from a committed `package-lock.json`, and runs it on a pinned [Node.js](https://nodejs.org/) 22 release (MIT license) whose tarball is verified by SHA-256 when the image is built. Its dependency tree is permissively licensed (MIT, ISC, BSD-3-Clause, including the Dawn binaries bundled in `webgpu`); the full list is in `NOTICE.md`. Conversion runs on the CPU; no GPU is used.

### XGRIDS LCC and LCC2 Formats

The 3D Preview Thumbnail pipeline reads XGRIDS LCC (`.lcc`) and LCC2 (`.lcc2`) scene descriptions to choose a level of detail and the files to download, and the SuperSplat Editor viewer opens `.lcc` scenes. The LCC and LCC2 data organization formats originated from XGRIDS. Their specifications and terms are the [LCC white paper](https://github.com/xgrids/LCCWhitepaper) and the [LCC2 white paper](https://github.com/xgrids/LCC2Whitepaper).

### Coordinate Transform Pipeline Library Notice

The Coordinate Transform pipeline depends on the open-source [laspy](https://github.com/laspy/laspy) library for reading and writing LAS/LAZ point cloud files. laspy is distributed under a 3-clause BSD license (BSD-3-Clause). The license text is a verbatim instance of the standard BSD-3-Clause template — its terms are equivalent to other permissive licenses such as MIT and Apache-2.0 (permitting use, modification, and redistribution with attribution and no warranty), with the additional standard third clause prohibiting use of the copyright holders' names to endorse derived products. It imposes no copyleft obligations.

:::note
The laspy BSD-3-Clause license is functionally similar to the MIT and Apache-2.0 licenses already used throughout VAMS and does not introduce any copyleft (LGPL/GPL) requirements. The pipeline's other core libraries — PDAL (BSD-3-Clause), pyproj (MIT), and NumPy (BSD-3-Clause) — are likewise permissively licensed.
:::

### Video SOP/BOM Extraction Pipeline Library Notice

The Video SOP/BOM Extraction pipeline container installs the Debian trixie `ffmpeg` package (FFmpeg 7.1.x) at image build time to probe video streams, extract and encode audio (FLAC), and extract key frames; the package is not version-pinned, and `7:7.1.5-0+deb13u1` is the version observed in the reference build. Debian builds FFmpeg with `--enable-gpl`, so the `ffmpeg` and `ffprobe` binaries are distributed under the GNU General Public License version 2 or later (GPL-2.0-or-later). The pipeline runs them as separate processes and does not link against the FFmpeg libraries. The pipeline's Python dependencies — boto3 and botocore (Apache-2.0), jsonschema (MIT), and Pillow (HPND) — are permissively licensed; the exact versions are in the repository's `NOTICE.md`.

:::warning
GPL-2.0-or-later terms apply only when this optional pipeline is enabled (`app.pipelines.useGenAiVideoSopBom.enabled`). Review the license requirements with your legal team before enabling it. See the [Video SOP/BOM Extraction pipeline](../pipelines/video-sop-bom.md#third-party-library-licenses) page.
:::

### Gaussian Splat Toolbox Pipeline Model Notice

The Gaussian Splat Toolbox container image ships the segmentation model weights its optional
background-removal and object-removal steps load, so a job does not download them while it runs. The
image carries the U^2-Net weights (`u2net`, `u2netp`, `u2net_human_seg`) as redistributed by
[backgroundremover](https://github.com/nadermx/backgroundremover) (MIT) under the
[U^2-Net](https://github.com/xuebinqin/U-2-Net) Apache-2.0 license, their ONNX forms plus
[BiRefNet](https://github.com/ZhengPeng7/BiRefNet) portrait matting (MIT) resolved through
[rembg](https://github.com/danielgatis/rembg) (MIT), the SAM 2.1 hiera-large checkpoint
([SAM 2](https://github.com/facebookresearch/sam2), Apache-2.0), and four torchvision checkpoints
(BSD-3-Clause) that `torch.hub` loads by content-hashed filename.

:::note
Stable Diffusion XL is **not** shipped in the image. The default object-removal action (`erase`) fetches
it at run time, so that action requires internet egress from the pipeline's subnets. `REMOVE_OBJECT`
with `OBJECT_REMOVAL_ACTION=remove` uses only the shipped weights. The opt-in dn-splatter model
variants likewise fetch LPIPS and ZoeDepth weights at run time.
:::

### Commercial Licensed Components

Some viewer plugins and processing pipelines require separate commercial licenses:

| Component                     | Vendor                                                        | License         |
| ----------------------------- | ------------------------------------------------------------- | --------------- |
| VNTANA 3D Model Viewer        | [VNTANA](https://www.vntana.com/)                             | Commercial      |
| VNTANA 3D Optimization Engine | VNTANA (AWS Marketplace)                                      | Commercial EULA |
| Veerum 3D Viewer              | [Veerum](https://veerum.com/)                                 | Commercial      |
| RapidPipeline 3D Processor    | [RapidPipeline](https://rapidpipeline.com/) (AWS Marketplace) | Commercial      |

### NVIDIA License Notices

**Isaac Lab Training Pipeline:** Uses NVIDIA Isaac Sim container images, which are subject to the [NVIDIA Software License Agreement](https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license). Users must accept the NVIDIA EULA by setting `acceptNvidiaEula: true` in the deployment configuration to use this pipeline.

**NVIDIA Cosmos Predict Pipeline (v1 -- Predict1):** The v1 pipeline is not a deployable pipeline. Its container definition is retained under `backendPipelines/genAi/nvidia/cosmos/predict/containerv1/` as a reference implementation for anyone building an equivalent pipeline of their own, and there is no configuration key that deploys it — `app.pipelines.useNvidiaCosmos` provisions the v2.5 pipeline described below. The licence terms are recorded here because the container definition ships in the repository.

Cosmos-Predict1 (7B) world foundation models are licensed under the [NVIDIA Open Model License](https://developer.nvidia.com/cosmos-license). The v1 container definition references six models from HuggingFace, each requiring separate license acceptance: two NVIDIA Cosmos diffusion models, one NVIDIA tokenizer, one NVIDIA guardrail model (all NVIDIA Open Model License), one Google T5-11B text encoder (Apache 2.0), and one Meta Llama-Guard-3-8B safety model (Meta Llama 3 Community License). It uses the NVIDIA NGC PyTorch base image subject to the [NVIDIA Deep Learning Container License](https://developer.nvidia.com/ngc/nvidia-deep-learning-container-license), and installs the Ubuntu `ffmpeg` package (GPL-2.0-or-later) at image build time for preview GIF generation. Anyone adapting it takes on those acceptance obligations directly.

**NVIDIA Cosmos Predict Pipeline (v2 -- Predict2.5):** Uses NVIDIA Cosmos-Predict2.5 (2B and 14B) world foundation models licensed under the [NVIDIA Open Model License](https://developer.nvidia.com/cosmos-license). The v2.5 pipeline uses the `cosmos-oss` framework package (Apache 2.0), `nvidia/Cosmos-Predict2.5-2B` and `nvidia/Cosmos-Predict2.5-14B` model checkpoints (NVIDIA Open Model License), transformer-engine 2.2.0 (Apache 2.0), and flash-attn 2.7.3 (BSD-3-Clause). The v2.5 container uses the NVIDIA CUDA 12.8.1 base image with cuDNN (`nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04`) and installs the Ubuntu `ffmpeg` package (GPL-2.0-or-later) at image build time for preview GIF generation. The 14B model requires an 8-GPU instance for context parallelism — `g6e.48xlarge` (8x L40S 48GB) or `p5.48xlarge` (8x H100 80GB). `p4d.24xlarge` is not supported: its NVIDIA driver predates the CUDA 12.8 runtime the container requires.

**NVIDIA Cosmos Reason Pipeline (v2):** Uses NVIDIA Cosmos-Reason2 (2B and 8B) Vision Language Models licensed under the [NVIDIA Open Model License](https://developer.nvidia.com/cosmos-license). The Reason pipeline analyzes video content to generate text-based captions, descriptions, and reasoning. The pipeline uses vLLM inference engine (Apache 2.0) and downloads `nvidia/Cosmos-Reason2-2B` (~5GB) and `nvidia/Cosmos-Reason2-8B` (~16GB) model checkpoints from HuggingFace. The container uses the NVIDIA CUDA 12.8.1 base image with cuDNN (`nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04`) and installs the Ubuntu `ffmpeg` package (GPL-2.0-or-later) at image build time as the video codec dependency of the inference engine. The 2B model runs on g5/g6e instances with 24GB+ VRAM, while the 8B model requires 32GB+ VRAM. Users must accept the NVIDIA Cosmos Reason license on HuggingFace before deployment.

**NVIDIA Cosmos Transfer Pipeline (v2.5):** Uses NVIDIA Cosmos-Transfer2.5-2B model for video transformation with control signal conditioning, licensed under the [NVIDIA Open Model License](https://developer.nvidia.com/cosmos-license). The Transfer pipeline generates stylized videos from source videos combined with control signals (edge, depth, segmentation, or visual blur). The pipeline downloads `nvidia/Cosmos-Transfer2.5-2B` (~20GB), `video-depth-anything` (~2GB, Apache 2.0) for depth map generation, and `sam2` (~5GB, Apache 2.0) for semantic segmentation from HuggingFace. The container uses the NVIDIA CUDA 12.8.1 base image with cuDNN (`nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04`) and installs the Ubuntu `ffmpeg` package (GPL-2.0-or-later) at image build time as the video codec dependency of the framework. The Transfer model requires 65.4GB VRAM across an 8-GPU instance — `g6e.48xlarge` (8x L40S 48GB) or `p5.48xlarge` (8x H100 80GB). `p4d.24xlarge` is not supported: its NVIDIA driver predates the CUDA 12.8 runtime the Transfer 2.5 container requires. Users must accept all model licenses on HuggingFace before deployment.

**NVIDIA Cosmos 3 Pipeline (omni):** Uses the NVIDIA Cosmos 3 omnimodal world foundation models licensed under the [OpenMDW-1.1 License](https://openmdw.ai/license/1-1/). The pipeline is served from the `NVIDIA/cosmos-framework` repository (Apache 2.0) and downloads the `nvidia/Cosmos3-Nano` (16B, ~35GB), `nvidia/Cosmos3-Super`, `nvidia/Cosmos3-Super-Text2Image`, and `nvidia/Cosmos3-Super-Image2Video` (64B, ~133GB each) model checkpoints from HuggingFace, along with the gated `nvidia/Cosmos-Guardrail1` model (NVIDIA Open Model License). The container uses the NVIDIA CUDA 12.8.1 base image with cuDNN (`nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04`), pins the CUDA 12.8 torch stack (torch, flash-attn, transformer-engine), and installs the Ubuntu `ffmpeg` package (GPL-2.0-or-later) at image build time for preview GIF generation. The Nano (16B) model runs on four L40S GPUs (`g6e.12xlarge`), across which its parameters are sharded; the Super (64B) models require multi-GPU P-series instances (`p5.48xlarge`/`p5e.48xlarge` with 8x H100/H200, or `p4de.24xlarge` with 8x A100 80GB). Users must accept the model licenses on HuggingFace before deployment.

**NVIDIA GR00T Fine-tuning Pipeline:** Fine-tunes the [`nvidia/GR00T-N1.5-3B`](https://huggingface.co/nvidia/GR00T-N1.5-3B) base model, licensed under the NVIDIA Open Model License and downloaded from HuggingFace after the license has been accepted there. The container uses the NVIDIA CUDA 12.4.1 base image with cuDNN (`nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04`) and installs the Ubuntu `ffmpeg` package (GPL-2.0-or-later) at image build time as the video codec dependency of the fine-tuning code's dataset loading.

:::note[FFmpeg GPL notice]
The Cosmos Predict, Cosmos Reason, Cosmos Transfer, Cosmos 3 and GR00T fine-tuning container images install FFmpeg from the Ubuntu `ffmpeg` package at image build time. Ubuntu builds FFmpeg with `--enable-gpl`, so the binary is licensed under GPL-2.0-or-later. The package is not version-pinned: each image ships whatever build the archive serves for its Ubuntu release when the image is built, and `/usr/share/doc/ffmpeg/copyright` in the image records that build's license text. Where a container invokes FFmpeg itself (Cosmos Predict and Cosmos 3 preview GIF generation) it runs the binary as a separate process and does not link against the FFmpeg libraries; elsewhere the package is a system dependency of the upstream inference or training code. The GPL terms apply only when the corresponding optional pipeline is enabled. Review the license requirements with your legal team before enabling one.
:::

Applications using Cosmos models must include the attribution: **"Built on NVIDIA Cosmos"** (required under the NVIDIA Open Model License for Cosmos Predict, Reason, and Transfer, and under the [OpenMDW-1.1 License](https://openmdw.ai/license/1-1/) for Cosmos 3). See the full pipeline documentation ([Cosmos Predict](../pipelines/nvidia-cosmos-predict.md), [Cosmos Reason](../pipelines/nvidia-cosmos-reason.md), [Cosmos Transfer](../pipelines/nvidia-cosmos-transfer.md), [Cosmos 3](../pipelines/nvidia-cosmos-3.md)) for details.

### Image to 3D Pipeline Model Licenses

The [Image to 3D](../pipelines/image-to-3d.md) pipeline offers six selectable open-weight image-to-mesh models and one agentic path that builds the subject in Blender.

:::caution[Some models need pre-approval before they can be used]
A gated repository is a license gate, not just a download step. The account behind the deployment's
HuggingFace token must accept the license and — for **Meta SAM 3D Objects** and the **Meta DINOv3**
encoder that TRELLIS.2 conditions on — request access and wait for Meta to approve it. A pending
request behaves exactly like a denied one. **BRIA RMBG-2.0** is granted on accepting BRIA's terms. The prompted
background-removal backend uses `facebook/sam-vit-huge`, which is Apache-2.0 and ungated — deliberately, in
preference to SAM 3, whose weights carry the same reviewed access request. Stable3DGen,
TripoSR, Step1X-3D and Hunyuan3D-2 need no approval.

Note that TRELLIS.2's own code and weights are MIT and ungated, so its license position looks clear while
its gated dependency is not: a token that downloads `microsoft/TRELLIS.2-4B` is still refused for DINOv3.
Obtain approval before deploying — the failure surfaces during the model download, after a GPU instance
has been provisioned and the container image pulled.
::: Each model is licensed separately, and only the models enabled in the deployment configuration are downloaded and built into container images. Review the license of each model you enable with your legal team, and accept the model license on HuggingFace before deployment.

**Meta SAM 3D Objects (`sam3d`):** Distributed under the Meta SAM License (last updated 19 November 2025), which grants a non-exclusive, worldwide, non-transferable and royalty-free licence to use, reproduce, distribute and modify the materials. The grant carries no field-of-use limit, so commercial use is permitted, and there is no separate acceptable-use policy. Three obligations attach:

-   **Redistribution under the same terms.** Distributing the materials or any derivative work to a third party is only permitted under this Agreement, and a copy of the Agreement must accompany them. A container image with the weights baked in is such a distribution, which is why this pipeline fetches the weights at run time from the gated repository instead.
-   **Trade controls.** Use must comply with sanctions and export and import controls. The licence prohibits uses subject to the International Traffic in Arms Regulations and end uses related to military or warfare purposes, nuclear applications, espionage, and guns or illegal weapons. The licensee must not be a target of trade controls.
-   **Acknowledgement in research publications.** Publishing research results produced with the materials requires acknowledging their use. The licence requires no attribution in product materials and grants no trademark rights.

The licence also forbids reverse engineering or decompiling the materials. The repository is manually gated on HuggingFace, so access has to be requested and approved before the first download.

SAM 3D Objects estimates depth through a second model its own pipeline configuration names: MoGe (`Ruicheng/moge-vitl`), under MIT and ungated. It is downloaded alongside the SAM weights and carries the MIT notice obligation, not the Meta SAM License terms.

The SAM 3D Objects image does not carry `nvdiffrast`. Two of the upstream pipeline's optional stages — its mesh postprocessing and its texture baking — rasterize through a module that imports it, so both are declined: the mesh arrives carrying vertex colours, and the simplification and hole filling those stages would have performed are done by this pipeline's own cleanup ladder instead. The image does install NVIDIA's `kaolin`, so the redistribution question recorded for TRELLIS.2 still applies to it on that count.

**Stable-X Stable3DGen / Hi3DGen (`stable3dgen`):** The inference code is distributed under the MIT license, and so are the geometry weights (`Stable-X/trellis-normal-v0-1`). The normal predictor it bridges through (`Stable-X/yoso-normal-v1-8-1`, with code from `hugoycj/StableNormal`) is Apache-2.0, and the background-removal model it falls back to when an image carries no alpha channel (`ZhengPeng7/BiRefNet`) is MIT. None of the four is gated on HuggingFace, so no access request or license acceptance is needed before deployment.

Although Stable3DGen is derived from the TRELLIS architecture, it does **not** inherit TRELLIS's dependency on the NVIDIA ecosystem: kaolin, nvdiffrast and FlexiCubes are replaced by the repository's own marching-cubes implementation over scikit-image. Nothing in the image is built from NVIDIA source, so the redistribution question recorded for TRELLIS.2 below does not apply to it. This is the reason it is the default model.

**Microsoft TRELLIS.2 (`trellis2`):** The TRELLIS.2 4B inference code and model weights are distributed under the MIT license, which imposes no copyleft obligations. The upstream repository declares optional rendering and voxelization dependencies from the NVIDIA ecosystem (for example, `nvdiffrast` and Kaolin) that carry NVIDIA source-code license terms restricting redistribution. Whether a given TRELLIS.2 code path pulls in those NVIDIA components remains an open question; confirm which optional dependencies your build installs, and review their terms separately from the MIT-licensed core before enabling this model in a redistributed product.

**VAST-AI TripoSR (`triposr`):** Distributed under the MIT license, which permits commercial use, modification, and redistribution with attribution and no warranty, and imposes no copyleft obligations.

**StepFun Step1X-3D (`step1x3d`):** Distributed under the Apache License, Version 2.0. In addition to the permissions granted by the MIT license, Apache-2.0 includes an express patent grant from each contributor and requires that modified files be marked as changed and that the license and any `NOTICE` file be carried forward in redistributions. The Step1X-3D container image additionally installs NVlabs nvdiffrast, which its upstream requirements list as a git dependency. nvdiffrast is distributed under the NVIDIA Source Code License (1-Way Commercial), whose Use Limitation reads "The Work and any derivative works thereof only may be used or intended for use non-commercially", with "non-commercially" defined as "for research or evaluation purposes only and not for any direct or indirect monetary gain". The carve-out permitting commercial use is NVIDIA's own. **An image built with Step1X-3D enabled is therefore limited to research and evaluation use, and not to any use for direct or indirect monetary gain.** The TRELLIS.2 image carries the same dependency and the same limitation. Neither model's own license says so -- Step1X-3D is Apache-2.0 and TRELLIS.2 is MIT -- so the limitation comes entirely from a transitive dependency, and the full dependency set of each image should be reviewed for other components under NVIDIA licenses. Stable3DGen depends on no NVIDIA-licensed component and is the model to choose where the deployment is commercial.

**Tencent Hunyuan3D-2 (`hunyuan3d`):** Distributed under the Tencent Hunyuan 3D 2.0 Community License Agreement. Unlike the other models here, its restriction is territorial rather than field-of-use: the agreement grants a royalty-free licence "for any purpose and in any field of use", so commercial use is permitted, but only within a Territory defined as "the worldwide territory, excluding the territory of the European Union, United Kingdom and South Korea", and it states that "any such use outside the Territory is unlicensed and unauthorized".

:::danger[Hunyuan3D-2 is not licensed in the European Union, the United Kingdom or South Korea]
`getConfig()` rejects a configuration that enables this model in an AWS Region inside the excluded territory, including the EU Sovereign Cloud Region. That check reads the deployment Region, which is a proxy for where the use happens rather than a decision of it — a deployment in a permitted Region can still serve users in the excluded territory, and no synthesis-time check can see that. Confirm the licence applies to your deployment before enabling the model.
:::

Two further terms bind a deployment rather than a build. A deployment whose products or services exceed **one million monthly active users** in the preceding calendar month must request a separate licence from Tencent, which Tencent may grant at its sole discretion. And outputs may not be used to improve any AI model other than Hunyuan3D-2 itself or a derivative of it.

Redistribution requires carrying a copy of the agreement, marking modified files as changed, and including the notice "Tencent Hunyuan 3D 2.0 is licensed under the Tencent Hunyuan 3D 2.0 Community License Agreement, Copyright © 2025 Tencent. All Rights Reserved. The trademark rights of 'Tencent Hunyuan' are owned by Tencent or its affiliate." The agreement is governed by the laws of the Hong Kong Special Administrative Region. The upstream source files carry an older "Tencent Hunyuan Non-Commercial License Agreement" header that the repository's own `LICENSE` file supersedes; review both.

Unlike the other two textured models, the Hunyuan3D-2 image installs no `nvdiffrast` — its texture stage ships its own rasterizer — so it carries none of the NVIDIA Source Code License restrictions recorded above.

**Blender agent (`blenderagent`):** This path carries no reconstruction model. Its container image installs **Blender** (GPL-2.0-or-later), **OpenAI Codex CLI** (Apache-2.0), **LiteLLM** (MIT), and **MoGe-2** (MIT in code and weights, `Ruicheng/moge-2-vitl-normal`, with a vendored DINOv2 under Apache-2.0) on **PyTorch** (BSD-3-Clause) and **utils3d** (MIT) for the scripted refit's depth estimate, and the agent reasons with a foundation model on Amazon Bedrock under Amazon Bedrock's terms for the configured model. Blender is a tool the agent runs as a separate process; the meshes and textures the agent writes are the agent's own output and are not derivatives of Blender, so the GPL attaches to redistributing the image rather than to the models it produces — the same position as the GPL-3.0 repair rungs installed in every Image to 3D image. Codex CLI is pinned to one release and used with no OpenAI account or key; the model is reached only through Amazon Bedrock, on the deployment's own IAM role. Retain the Codex, LiteLLM, MoGe-2, utils3d and PyTorch notices in redistributions. No `nvdiffrast` or other NVIDIA-licensed source is built into this image.

### GPL-Licensed Mesh Repair Components

The Image to 3D pipeline's print-readiness cleanup runs a ladder of mesh repair backends. Two of them — `trimesh` (MIT) and `manifold3d` (Apache-2.0) — are permissively licensed. The other two, **pymeshfix** and **PyMeshLab**, are licensed under GPL-3.0. All four packages are installed in every Image to 3D container image, and the `REPAIR_BACKENDS` pipeline setting selects which of them a given run uses. That setting defaults to the full ladder, so the GPL-3.0 backends execute unless an operator narrows the selection to the permissively licensed backends.

:::note
Because **pymeshfix** and **PyMeshLab** are present in every Image to 3D container image, GPL-3.0 terms apply to those images regardless of which backends a run selects. GPL-3.0 is a strong copyleft license whose obligations differ materially from the MIT, Apache-2.0, and LGPL-2.1 terms elsewhere in VAMS, and they include source availability obligations if you redistribute the images. Review the license requirements with your legal team before deploying the Image to 3D pipeline, and leave the pipeline disabled where GPL-3.0 components cannot be accepted in your container images. This mirrors the copyleft considerations already noted for Blender (GPL-3.0) in the GenAI Metadata 3D Labeling pipeline and for the LGPL-2.1 CAD components.
:::

---

## Operational Metrics Collection

To measure the performance of this solution and to help improve and develop AWS Content, AWS may collect and use anonymous operational metrics related to your use of this AWS Content. AWS will not access your content, as defined in the [Online Customer Agreement](https://aws.amazon.com/agreement/). Data collection is subject to the [AWS Privacy Policy](https://aws.amazon.com/privacy/).

You may opt out of operational metrics collection by removing the tag(s) starting with `uksb-` or `SO` from the description(s) in any AWS CloudFormation templates or CDK TemplateOptions.

---

## License Information

VAMS is licensed under the Apache License, Version 2.0. You may obtain a copy of the License at:

[https://www.apache.org/licenses/LICENSE-2.0](https://www.apache.org/licenses/LICENSE-2.0)

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

---

## Contributing

Contributions to VAMS are welcome. See the project's `CONTRIBUTING.md` file for guidelines on how to contribute, including code style requirements, testing expectations, and the pull request process.

---

## Security

### Shared Responsibility Model

When you build systems on AWS infrastructure, security responsibilities are shared between you and AWS. This [shared responsibility](https://aws.amazon.com/compliance/shared-responsibility-model/) model reduces your operational burden because AWS operates, manages, and controls the components including the host operating system, virtualization layer, and physical security of the facilities in which the services operate.

VAMS is provided under this shared responsibility model. Any customization for customer use must go through a security review to confirm that modifications do not introduce new vulnerabilities. Any team implementing VAMS takes on the responsibility of ensuring their implementation has gone through a proper security review.

### Security Recommendations

1. Run `npm audit` in the `web/` directory prior to deploying the frontend to ensure all packages are up to date.
2. When deploying to an AWS account, create an AWS IAM role for deployment that limits access to the least privilege necessary.
3. Run AWS CDK bootstrap with the least-privileged AWS IAM role needed to deploy CDK and VAMS environment components.
4. Review authentication token timeouts (defaulting to 1 hour) with your organization's security team.
5. Consider configuring IP range restrictions using `authorizerOptions.allowedIpRanges` in the deployment configuration.

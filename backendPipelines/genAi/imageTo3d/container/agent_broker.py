# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A broker that runs image-editing models on Amazon Bedrock on the agent's behalf, through files.

The agent's commands have no network and no credentials -- that is the confinement the stage rests on --
and yet the texture work needs an image model: the photograph covers the surface the camera saw, and the
rest of the model wants fabric, hair and skin that continue from it rather than a flat colour. So the agent
does not call Bedrock. It writes a request file into ``requests/`` in its workspace, and this broker,
running in the pipeline process beside the agent with the task role's credentials, picks the request up,
runs the named Stability AI model, writes the result image into the workspace and a result file beside the
request. The agent polls for the result. Nothing the agent can write reaches anywhere but Bedrock, only
the operations named here run, and every path is confined to the workspace.

Request (``requests/<name>.json``)::

    {"operation": "inpaint", "image": "textures/solid_basecolor.png", "mask": "textures/inferred_mask.png",
     "prompt": "...", "negative_prompt": "...", "output": "textures/filled.png", "seed": 0}
    {"operation": "style_transfer", "image": "textures/solid_basecolor.png", "style": "refs/x_cutout.png",
     "prompt": "...", "style_strength": 0.6, "output": "textures/styled.png"}

Result (``requests/<name>.result.json``)::

    {"status": "ok", "output": "textures/filled.png", "model": "...", "seconds": 4.2}
    {"status": "error", "message": "..."}

A deployment that does not enable the image-editing models (``useStabilityImageEditing``) still runs the
broker, which answers every request with an error the agent reads, so a request never waits on a result
that will not come.
"""

import base64
import io
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# The Stability AI edit models on Bedrock, by operation, as the inference profiles they run under.
OPERATIONS = {
    "inpaint": "us.stability.stable-image-inpaint-v1:0",
    "structure": "us.stability.stable-image-control-structure-v1:0",
    "style_transfer": "us.stability.stable-style-transfer-v1:0",
    "search_recolor": "us.stability.stable-image-search-recolor-v1:0",
    "search_replace": "us.stability.stable-image-search-replace-v1:0",
    "erase": "us.stability.stable-image-erase-object-v1:0",
}
# The largest edge sent to the model; the result is resized back to the input's size.
MAX_EDGE = 1024
POLL_SECONDS = 2.0


class ImageEditBroker(threading.Thread):
    """Watches ``workspace/requests`` and serves each request once, in order of arrival."""

    def __init__(self, workspace: Path, region: str, client=None, enabled: bool = True):
        super().__init__(name="image-edit-broker", daemon=True)
        self.enabled = enabled
        self.workspace = Path(workspace).resolve()
        self.requests_dir = self.workspace / "requests"
        self.region = region
        self._client = client
        self._stop_event = threading.Event()
        self.served = []

    def stop(self):
        self._stop_event.set()

    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
            self._client = boto3.client("bedrock-runtime", region_name=self.region, config=retry_config)
        return self._client

    def run(self):
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        while not self._stop_event.is_set():
            self.serve_pending()
            self._stop_event.wait(POLL_SECONDS)

    def serve_pending(self) -> int:
        count = 0
        for request_path in sorted(self.requests_dir.glob("*.json")):
            if request_path.name.endswith(".result.json"):
                continue
            result_path = request_path.with_name(request_path.stem + ".result.json")
            if result_path.exists():
                continue
            self.serve(request_path, result_path)
            count += 1
        return count

    def serve(self, request_path: Path, result_path: Path) -> None:
        started = time.time()
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            result = self.execute(request)
            result["seconds"] = round(time.time() - started, 1)
            result["status"] = "ok"
        except Exception as e:  # every failure becomes a result the agent can read
            logger.warning(f"Image edit request {request_path.name} failed: {e}")
            result = {"status": "error", "message": str(e)[:1500], "seconds": round(time.time() - started, 1)}
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        self.served.append((request_path.name, result["status"]))
        logger.info(f"Image edit request {request_path.name}: {result['status']} in {result['seconds']}s")

    # -- request handling ---------------------------------------------------------------------------------

    def inside(self, relative: str, must_exist: bool) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("a path is required")
        path = (self.workspace / relative).resolve()
        if self.workspace not in path.parents and path != self.workspace:
            raise ValueError(f"path leaves the workspace: {relative}")
        if must_exist and not path.is_file():
            raise ValueError(f"no such file in the workspace: {relative}")
        return path

    @staticmethod
    def _encode(image, size=None):
        from PIL import Image

        if size is not None and image.size != size:
            image = image.resize(size, Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    @staticmethod
    def _fit(size):
        width, height = size
        scale = min(1.0, MAX_EDGE / max(width, height))
        # The models want multiples of 64.
        return (max(64, int(round(width * scale / 64)) * 64), max(64, int(round(height * scale / 64)) * 64))

    def execute(self, request: dict) -> dict:
        from PIL import Image

        if not self.enabled:
            raise ValueError(
                "Image editing is not enabled in this deployment "
                "(app.pipelines.useImageTo3d.models.blenderagent.useStabilityImageEditing). Paint the hidden "
                "surfaces from the photographs' own colours instead.")
        operation = str(request.get("operation", "")).strip()
        model = OPERATIONS.get(operation)
        if model is None:
            raise ValueError(f"unknown operation {operation!r}; one of {', '.join(sorted(OPERATIONS))}")
        output = self.inside(request.get("output") or f"textures/{operation}.png", must_exist=False)
        prompt = str(request.get("prompt", "")).strip()

        image = Image.open(self.inside(request.get("image"), must_exist=True)).convert("RGB")
        original_size = image.size
        working_size = self._fit(original_size)
        body = {"output_format": "png"}
        if request.get("seed") is not None:
            body["seed"] = int(request["seed"])

        # A request with a mask keeps every unmasked texel exactly: the model works at the working size and its
        # whole output comes back through a resize, so it is composited under the mask rather than shipped.
        # Inpaint and erase send the mask to the model; any other operation may still carry one to keep by.
        mask = None
        if operation in ("inpaint", "erase") or request.get("mask"):
            mask = Image.open(self.inside(request.get("mask"), must_exist=True)).convert("L")
            if mask.size != original_size:
                mask = mask.resize(original_size, Image.LANCZOS)

        if operation == "inpaint":
            body.update({"image": self._encode(image, working_size), "mask": self._encode(mask, working_size),
                         "prompt": prompt or "continue the surrounding material seamlessly"})
            if request.get("negative_prompt"):
                body["negative_prompt"] = str(request["negative_prompt"])
            if request.get("grow_mask") is not None:
                body["grow_mask"] = int(request["grow_mask"])
        elif operation == "structure":
            # The picture regenerated along the input's structure: outline and pose kept, content from the prompt.
            body.update({"image": self._encode(image, working_size), "prompt": prompt})
            if request.get("control_strength") is not None:
                body["control_strength"] = float(request["control_strength"])
            if request.get("negative_prompt"):
                body["negative_prompt"] = str(request["negative_prompt"])
        elif operation == "search_replace":
            body.update({"image": self._encode(image, working_size), "prompt": prompt,
                         "search_prompt": str(request.get("search_prompt", "")).strip()})
        elif operation == "style_transfer":
            style = Image.open(self.inside(request.get("style"), must_exist=True)).convert("RGB")
            body.update({"init_image": self._encode(image, working_size),
                         "style_image": self._encode(style, self._fit(style.size)), "prompt": prompt})
            for key in ("style_strength", "composition_fidelity", "change_strength"):
                if request.get(key) is not None:
                    body[key] = float(request[key])
        elif operation == "search_recolor":
            body.update({"image": self._encode(image, working_size), "prompt": prompt,
                         "select_prompt": str(request.get("select_prompt", "")).strip()})
        elif operation == "erase":
            body.update({"image": self._encode(image, working_size), "mask": self._encode(mask, working_size)})

        response = self.client().invoke_model(modelId=model, body=json.dumps(body),
                                              contentType="application/json", accept="application/json")
        payload = json.loads(response["body"].read())
        images = payload.get("images") or []
        if not images:
            raise RuntimeError(f"{model} returned no image: {json.dumps(payload)[:500]}")
        result = Image.open(io.BytesIO(base64.b64decode(images[0]))).convert("RGB")
        if result.size != original_size:
            result = result.resize(original_size, Image.LANCZOS)
        if mask is not None:
            result = Image.composite(result, image, mask)
        output.parent.mkdir(parents=True, exist_ok=True)
        result.save(output)
        return {"output": os.path.relpath(output, self.workspace).replace(os.sep, "/"), "model": model,
                "finishReasons": payload.get("finish_reasons"), "workingSize": list(working_size),
                "unmaskedPreserved": mask is not None}

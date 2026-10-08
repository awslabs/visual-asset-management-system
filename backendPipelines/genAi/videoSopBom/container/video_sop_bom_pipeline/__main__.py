# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entry point for the Video SOP/BOM container (PID 1 under the exec-form ENTRYPOINT)."""

import argparse
import logging
import os
import sys

from . import media
from .clients import build_clients
from .definition import load_definition
from .errors import PIPELINE_ERROR, PipelineRejection
from .pipeline import run
from .sfn import send_task_failure
from .signals import RunState, install_sigterm_handler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("video_sop_bom_pipeline")


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="video_sop_bom_pipeline")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--definition-s3-uri", help="s3://<auxBucket>/<auxTempPrefix>definition.json written by constructPipeline")
    source.add_argument("--definition-file", help="a local definition.json (local runs)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        logger.error("AWS_REGION is not set")
        return 2
    # The uid the work actually runs under: the image declares a non-root USER and nothing in a run's
    # outcome says which account it succeeded as.
    getuid = getattr(os, "getuid", None)
    logger.info("Video SOP/BOM Pipeline - Container Start uid=%s", getuid() if getuid else "n/a")
    clients = build_clients(region)
    state = RunState(clients=clients)
    install_sigterm_handler(state)
    try:
        definition = load_definition(args.definition_s3_uri or args.definition_file, clients)
    except PipelineRejection as rejection:
        send_task_failure(clients, rejection.code, rejection.cause)
        return 1
    except Exception as exc:
        send_task_failure(clients, PIPELINE_ERROR, f"could not load the definition document: {type(exc).__name__}: {exc}")
        return 1
    status = run(definition, media, clients, state=state)
    return 0 if status == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main())

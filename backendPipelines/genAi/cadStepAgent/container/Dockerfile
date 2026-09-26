# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# One image serves both runtimes. The build platform is supplied by the CodeBuild project
# (linux/arm64 for the AgentCore Runtime, linux/amd64 for AWS Batch on Fargate). Pinned by digest to
# the multi-arch image index; the tag records which release the digest resolves to (3.12-slim-trixie),
# so either platform builds from the same release.
FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

# Sonames the OCP (OpenCascade) wheel links that the slim base does not carry.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglu1-mesa \
    libxrender1 \
    libxext6 \
    libx11-6 \
    libgomp1 \
    libfontconfig1 \
    && apt-get clean && rm -rf /var/lib/apt/lists

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY ./requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY ./cad_step_agent/ ./cad_step_agent/

# The agent runs as cadagent, never root. A generated script runs in a subprocess of that same account
# with a scrubbed environment (no credential variables, no task token, no API key), an isolated
# interpreter (-I), its own session, resource limits, a per-attempt working directory and a timeout
# (sandbox.py). Every process above the script that holds the task environment -- the agent and the
# init below -- makes itself non-dumpable, so the script cannot read their /proc entries.
RUN useradd --create-home --uid 10001 --user-group cadagent \
    && mkdir -p /work && chown cadagent:cadagent /work && chmod 700 /work
ENV TMPDIR=/work
USER cadagent

# PID 1 on both runtimes is the image's own init (init.py): non-dumpable before it starts the command,
# it forwards the stop signals to the command and reaps orphaned processes. The job definition asks
# ECS for no init process: the one ECS injects is an ordinary same-uid process holding the whole task
# environment, readable by a generated script through /proc/1/environ.
ENTRYPOINT ["python3", "-m", "cad_step_agent.init"]

# AgentCore Runtime protocol: HTTP on 8080 (/invocations, /ping). The Batch job definition overrides
# the command with `python3 -m cad_step_agent.batch_main` (the ENTRYPOINT stays) and hands the
# definition over in the environment.
EXPOSE 8080
CMD ["python3", "-m", "cad_step_agent.agentcore_app"]

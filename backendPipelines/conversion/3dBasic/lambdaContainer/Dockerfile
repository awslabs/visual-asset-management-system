# Pinned by digest, not by tag. `lambda/python:3.12` is a moving reference: a rebuild months apart can
# produce a different image from identical sources, so an image that stops working cannot be told apart
# from a change in this repository. The tag is kept alongside the digest for readability -- Docker
# resolves the digest and ignores the tag.
#
# The digest is the multi-arch INDEX (a manifest list), not a single platform's manifest, which is what
# keeps `--platform=linux/amd64` resolving. To move it, resolve the new digest and confirm it is an index
# carrying linux/amd64 BEFORE editing -- a wrong digest fails the build rather than falling back:
#   TOK=$(curl -s "https://public.ecr.aws/token/?scope=repository:lambda/python:pull&service=public.ecr.aws" | jq -r .token)
#   curl -sI -H "Authorization: Bearer $TOK" \
#        -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json" \
#        https://public.ecr.aws/v2/lambda/python/manifests/3.12 | grep -i docker-content-digest
#
# The builder stage starts from the same digest as the final image, so the Assimp binary it compiles is
# linked against the glibc and libstdc++ the function runs with.
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12@sha256:a53086fc3118c5457097083ba3d1ccf1d8fdfcba9e7a90fbfc5d2b06c625215b AS assimp-builder

# FBX input is read by the Assimp command-line tool. Assimp publishes no prebuilt Linux command-line
# binary (its linux-x64 release archive holds only the shared library) and no source archive of its own
# (GitHub generates the tag archives, so their bytes and hash can change), so the tool is built from a
# shallow git clone of the release tag, and the build fails unless the clone's commit is the pinned
# ASSIMP_COMMIT. Only the FBX importer and the glTF exporter are built (the tool also links the assbin
# and assxml exporters its dump command needs), statically linked to Assimp. ASSIMP_REPO points the
# build at a mirror (a build host without GitHub access); the commit is still checked.
ARG ASSIMP_VERSION=6.0.6
ARG ASSIMP_REPO=https://github.com/assimp/assimp.git
ARG ASSIMP_TAG=v6.0.6
ARG ASSIMP_COMMIT=a79ff6bbce54d3e01186a918dc6374ed55317426
RUN dnf install -y gcc-c++ cmake make git && dnf clean all
RUN git -c advice.detachedHead=false clone --quiet --depth 1 --branch "${ASSIMP_TAG}" "${ASSIMP_REPO}" /tmp/assimp \
    && test "$(git -C /tmp/assimp rev-parse HEAD)" = "${ASSIMP_COMMIT}" \
    && cmake -S /tmp/assimp -B /tmp/assimp/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
       -DASSIMP_BUILD_TESTS=OFF -DASSIMP_BUILD_ASSIMP_TOOLS=ON -DASSIMP_BUILD_SAMPLES=OFF \
       -DASSIMP_INSTALL=OFF -DASSIMP_WARNINGS_AS_ERRORS=OFF -DASSIMP_BUILD_ZLIB=ON \
       -DASSIMP_BUILD_ALL_IMPORTERS_BY_DEFAULT=OFF -DASSIMP_BUILD_FBX_IMPORTER=ON \
       -DASSIMP_BUILD_ALL_EXPORTERS_BY_DEFAULT=OFF -DASSIMP_BUILD_GLTF_EXPORTER=ON \
       -DASSIMP_BUILD_ASSBIN_EXPORTER=ON -DASSIMP_BUILD_ASSXML_EXPORTER=ON \
    && cmake --build /tmp/assimp/build --target assimp_cmd -j"$(nproc)" \
    && install -m 0755 /tmp/assimp/build/bin/assimp /opt/assimp \
    && /opt/assimp version

# One image source, two final targets (`--target lambda` or `--target batch`). Everything up to
# `base-with-code` is shared, so both targets hold the same glibc, libstdc++, Assimp binary, locked
# wheels and conversion code; they differ only in the last layer (how the container starts). A build
# without --target produces the last stage, `lambda`, which is the image the function has always run.
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12@sha256:a53086fc3118c5457097083ba3d1ccf1d8fdfcba9e7a90fbfc5d2b06c625215b AS base-with-code

#Note: Lambda containers automatically use a non-root user that is created in the base image

# STEP input (app.pipelines.useConversion3dBasic.enableStepInput, passed by CDK as this build argument).
# When false, the image holds no Open CASCADE code: cascadio is not installed and the handler refuses a
# STEP run before anything is downloaded, naming the configuration key.
ARG ENABLE_STEP_INPUT=false
ENV ENABLE_STEP_INPUT=${ENABLE_STEP_INPUT}

# Copies requirements.txt file into the container. It is exported from uv.lock WITH hashes
# (`uv export --frozen --no-emit-project --no-dev --no-annotate -o requirements.txt`), never edited by
# hand. requirements-step.txt is the `step` group (`uv export --only-group step`). uv is a development
# and CI tool only: the image installs the committed exports with pip.
COPY requirements.txt requirements-step.txt ./
# Installs exactly the locked packages: pip refuses any file whose SHA-256 is not in requirements.txt.
RUN pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r requirements.txt
RUN if [ "${ENABLE_STEP_INPUT}" = "true" ]; then \
        pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r requirements-step.txt; \
    fi

COPY --from=assimp-builder /opt/assimp /opt/assimp/bin/assimp

# Be sure to copy over the function itself! `.dockerignore` keeps the tests, the uv project files and any
# local caches out of this copy, so the image holds the runtime modules only.
COPY . .

# The AWS Batch (Fargate) target: the same code, started by batch_entry.py instead of the Lambda runtime
# interface client, as a non-root user. A Batch job definition can override a container's command but
# not its entrypoint, so the entrypoint is set here, and the image's Lambda entrypoint
# (/lambda-entrypoint.sh) is replaced. The files are made readable by every user, since the task user
# is not the one that owns them.
FROM base-with-code AS batch
RUN chmod -R a+rX /var/task /opt/assimp
USER 10001:10001
ENTRYPOINT ["python", "-u", "batch_entry.py"]
CMD []

# The Lambda target, last so that a build without --target produces it. It is the previous final stage
# unchanged: the same CMD, no USER.
FROM base-with-code AS lambda
# Points to the handler function of your lambda function
# AWS Lambda runs container-image functions as its own non-root sandbox user regardless of a
# Dockerfile USER, so adding one here changes nothing at runtime.
# nosemgrep: dockerfile.security.missing-user.missing-user
CMD ["lambda.lambda_handler"]

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
# binary (its linux-x64 release archive holds only the shared library), so the tool is built from the
# pinned release's source, with only the FBX importer and the glTF exporter, and statically linked to
# Assimp (the tool also links the assbin and assxml exporters its dump command needs). The archive's SHA-256 is checked before it is unpacked; ASSIMP_URL points the build at a
# mirror (a build host without GitHub access) and the hash is still checked.
ARG ASSIMP_VERSION=6.0.6
ARG ASSIMP_URL=https://github.com/assimp/assimp/archive/refs/tags/v6.0.6.tar.gz
ARG ASSIMP_SHA256=5f7ed412b76575621df0525a762b31da5874afff352b3a890c6308db4ac7204c
RUN dnf install -y gcc-c++ cmake make tar gzip && dnf clean all
RUN curl -fsSL -o /tmp/assimp.tar.gz "${ASSIMP_URL}" \
    && echo "${ASSIMP_SHA256}  /tmp/assimp.tar.gz" | sha256sum -c - \
    && mkdir -p /tmp/assimp && tar -xzf /tmp/assimp.tar.gz -C /tmp/assimp --strip-components=1 \
    && cmake -S /tmp/assimp -B /tmp/assimp/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
       -DASSIMP_BUILD_TESTS=OFF -DASSIMP_BUILD_ASSIMP_TOOLS=ON -DASSIMP_BUILD_SAMPLES=OFF \
       -DASSIMP_INSTALL=OFF -DASSIMP_WARNINGS_AS_ERRORS=OFF -DASSIMP_BUILD_ZLIB=ON \
       -DASSIMP_BUILD_ALL_IMPORTERS_BY_DEFAULT=OFF -DASSIMP_BUILD_FBX_IMPORTER=ON \
       -DASSIMP_BUILD_ALL_EXPORTERS_BY_DEFAULT=OFF -DASSIMP_BUILD_GLTF_EXPORTER=ON \
       -DASSIMP_BUILD_ASSBIN_EXPORTER=ON -DASSIMP_BUILD_ASSXML_EXPORTER=ON \
    && cmake --build /tmp/assimp/build --target assimp_cmd -j"$(nproc)" \
    && install -m 0755 /tmp/assimp/build/bin/assimp /opt/assimp \
    && /opt/assimp version

FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12@sha256:a53086fc3118c5457097083ba3d1ccf1d8fdfcba9e7a90fbfc5d2b06c625215b

#Note: Lambda containers automatically use a non-root user that is created in the base image

# STEP input (app.pipelines.useConversion3dBasic.enableStepInput, passed by CDK as this build argument).
# When false, the image holds no Open CASCADE code: cascadio is not installed and the handler refuses a
# STEP run before anything is downloaded, naming the configuration key.
ARG ENABLE_STEP_INPUT=false
ENV ENABLE_STEP_INPUT=${ENABLE_STEP_INPUT}

# Copies requirements.txt file into the container. It is exported from poetry.lock WITH hashes
# (`poetry export -f requirements.txt -o requirements.txt`, which needs poetry-plugin-export), never
# edited by hand. requirements-step.txt is the `step` group (`poetry export --only step`).
COPY requirements.txt requirements-step.txt ./
# Installs exactly the locked packages: pip refuses any file whose SHA-256 is not in requirements.txt.
RUN pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r requirements.txt
RUN if [ "${ENABLE_STEP_INPUT}" = "true" ]; then \
        pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r requirements-step.txt; \
    fi

COPY --from=assimp-builder /opt/assimp /opt/assimp/bin/assimp

# Be sure to copy over the function itself! `.dockerignore` keeps the tests, the Poetry files and any
# local caches out of this copy, so the image holds the runtime modules only.
COPY . .

# Points to the handler function of your lambda function
# AWS Lambda runs container-image functions as its own non-root sandbox user regardless of a
# Dockerfile USER, so adding one here changes nothing at runtime.
# nosemgrep: dockerfile.security.missing-user.missing-user
CMD ["lambda.lambda_handler"]

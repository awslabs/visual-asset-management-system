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
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12@sha256:37fe9ca4183c36ec57e4c5eabb8893cd9fbc9c0f3053b903d9a57eb2bd6bafcc

#Note: Lambda containers automatically use a non-root user that is created in the base image

# Installs python, removes cache file to make things smaller (needed for lambda container python 3.10 but not 3.12)
# RUN yum update -y && \
#     dnf install -y python3 python3-dev python3-pip gcc git && \
#     rm -Rf /var/cache/dnf

# Copies requirements.txt file into the container. It is exported from poetry.lock
# (`poetry export --without-hashes -f requirements.txt -o requirements.txt`), never edited by hand.
COPY requirements.txt ./
# Installs dependencies found in your requirements.txt file
RUN pip install --no-cache-dir -r requirements.txt

# Be sure to copy over the function itself! `.dockerignore` keeps the tests, the Poetry files and any
# local caches out of this copy, so the image holds the runtime modules only.
COPY . .

# Points to the handler function of your lambda function
# AWS Lambda runs container-image functions as its own non-root sandbox user regardless of a
# Dockerfile USER, so adding one here changes nothing at runtime.
# nosemgrep: dockerfile.security.missing-user.missing-user
CMD ["lambda.lambda_handler"]
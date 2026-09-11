/* Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
   SPDX-License-Identifier: Apache-2.0 */

/**
 * Content-addressed container image tags for the CodeBuild-built pipelines.
 *
 * Amazon ECS and AWS Batch both cap a container image reference at 255 characters, counting the whole
 * `<account>.dkr.ecr.<region>.amazonaws.com/<repository>:<tag>` string. CDK derives the repository name
 * from the nested-stack path, so a pipeline repository URI already runs to roughly 200 characters
 * before any tag — the Splat Toolbox repository measures 207 in the reference deployment, leaving a
 * 47-character budget. A full 64-character asset hash therefore produces a 272-character reference and
 * the task definition is rejected at create time with
 * `Container.image should be 255 characters or less`.
 *
 * Neither CDK synth nor a unit test sees the LENGTH: the template is valid CloudFormation and the URI is
 * a token until deploy. `infra/test/pipelines/containerImageReferenceLength.test.ts` closes what CAN be
 * decided at synth — that a repository whose auto-generated name is known to exceed the cap carries an
 * explicit short name instead.
 *
 * The tag is truncated to keep the reference inside the limit with room for a longer deployment name.
 * 32 hexadecimal characters is 128 bits of the asset hash, so the tag stays content-addressed —
 * identical sources still produce an identical tag, and a source change still produces a new one.
 *
 * :::warning[Truncating the tag is not sufficient on its own]
 * A pipeline whose repository URI cannot fit gets an EXPLICIT short `repositoryName` (see
 * `coordinateTransformCodeBuild-construct.ts`), not a shorter tag: dropping below 32 hex characters
 * trades content-addressing for a handful of characters.
 * :::
 */

/**
 * Characters of the asset hash kept. Against the auto-named repositories that DO fit (measured 194-207
 * on the reference deployment) this yields references of 227-240, inside the 255 cap.
 */
export const IMAGE_TAG_LENGTH = 32;

/** The hard limit ECS and Batch enforce on a container image reference. */
export const MAX_IMAGE_REFERENCE_LENGTH = 255;

/**
 * The image tag for a CodeBuild-built pipeline image, derived from its source asset hash.
 *
 * Call this once per construct and pass the result to BOTH the build (as `IMAGE_TAG`) and the job
 * definition, so the tag pushed and the tag pulled cannot diverge.
 */
export function contentImageTag(assetHash: string): string {
    return assetHash.slice(0, IMAGE_TAG_LENGTH);
}

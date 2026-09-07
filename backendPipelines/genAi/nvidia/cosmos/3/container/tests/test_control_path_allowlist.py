#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""`COSMOS3_CONTROL_PATH` is a caller-supplied S3 URI, and it must name this deployment's own bucket.

The value comes from a template field or from asset metadata -- so from anyone who can edit the
asset -- and reaches `aws s3 cp` under the Batch job role, which is granted read on every asset
bucket in the deployment plus whatever a bucket policy elsewhere happens to allow. Without the check
the value went through verbatim: a definition carrying `s3://someone-elses-bucket/secret.mp4` had that
object downloaded into the run, and the run reported success.

The contract stays a FULL URI (the two transfer templates document it that way); what is added is the
bucket allowlist. Its sources are the deployment's asset buckets from `ALLOWED_INPUT_BUCKETS` on the
job definition, plus the buckets this run's own input and output locations name -- the second half is
what keeps the check effective on a job definition that does not set the variable yet.

What each group pins:

*   **A foreign bucket is rejected**, and the four non-transfer templates' asset-relative wording is
    rejected too, with a message naming the expected form instead of an `aws s3 cp` failure.
*   **The rejection lands before the expensive work.** The GPU node is already provisioned when this
    container starts, so "early" means before the model restore -- tens of gigabytes -- not merely
    before inference.
*   **Ordinary transfer runs still run.** An allowlist that rejects everything satisfies every
    rejection test above while making the pipeline unusable.
"""

import pytest

from conftest import ASSET_BUCKET, ASSET_ID, AUXILIARY_BUCKET, base_definition, transfer_definition


# ============================ rejection: the bucket is not the deployment's ============================

class TestForeignBucketsAreRejected:

    def test_a_bucket_outside_the_deployment_is_rejected(self, run_container):
        with pytest.raises(ValueError, match="COSMOS3_CONTROL_PATH"):
            run_container(transfer_definition(
                cosmosControlPath="s3://someone-elses-bucket/secret.mp4"))

    def test_the_message_names_the_bucket_that_was_rejected(self, run_container):
        """An operator reading the execution record has to be able to tell WHICH value was refused."""
        with pytest.raises(ValueError, match="someone-elses-bucket"):
            run_container(transfer_definition(
                cosmosControlPath="s3://someone-elses-bucket/secret.mp4"))

    def test_the_model_cache_bucket_is_not_an_allowed_source(self, run_container):
        """The job role can read the model cache bucket, and it is not an asset bucket -- so naming it
        is exactly the cross-bucket read the allowlist exists to refuse."""
        with pytest.raises(ValueError, match="COSMOS3_CONTROL_PATH"):
            run_container(transfer_definition(
                cosmosControlPath="s3://model-cache-bucket/hf_cache/weights.bin"))

    def test_a_bucket_whose_name_merely_starts_with_an_allowed_one_is_rejected(self, run_container):
        """The comparison is whole-name equality. A prefix or substring test would accept this."""
        with pytest.raises(ValueError, match="COSMOS3_CONTROL_PATH"):
            run_container(transfer_definition(
                cosmosControlPath=f"s3://{ASSET_BUCKET}-evil/{ASSET_ID}/controls/edge.mp4"))


# ============================ rejection: values that are not a usable object URI ============================

class TestMalformedValuesAreRejected:

    @pytest.mark.parametrize("control_path", [
        "controls/edge.mp4",                        # the four non-transfer templates' wording
        "/controls/edge.mp4",                       # an absolute local path
        "https://evil.example.com/edge.mp4",        # a URL of another scheme
        "file:///etc/passwd",
        f"S3://{ASSET_BUCKET}/{ASSET_ID}/edge.mp4",  # scheme case the AWS CLI does not accept
        f"s3://{ASSET_BUCKET}",                     # a bucket, not an object
        f"s3://{ASSET_BUCKET}/",
        f"s3://{ASSET_BUCKET}/{ASSET_ID}/controls/",  # a prefix, not an object
        f"s3://{ASSET_BUCKET}/{ASSET_ID}/../../other/edge.mp4",   # traversal out of the asset
        f"s3://{ASSET_BUCKET}/{ASSET_ID}/./edge.mp4",
        f"s3://{ASSET_BUCKET}//{ASSET_ID}/edge.mp4",              # an empty key segment
        f"s3://{ASSET_BUCKET}/{ASSET_ID}/edge.mp4?versionId=abc",  # urlparse and the CLI disagree
        f"s3://{ASSET_BUCKET}/{ASSET_ID}/edge.mp4#frag",
        f"s3://{ASSET_BUCKET}/{ASSET_ID}/edge\n.mp4",             # a control character
        "s3://",
    ])
    def test_the_value_is_rejected(self, run_container, control_path):
        with pytest.raises(ValueError, match="COSMOS3_CONTROL_PATH"):
            run_container(transfer_definition(cosmosControlPath=control_path))

    def test_a_query_string_would_otherwise_download_a_different_object(self, container):
        """Why '?' is refused rather than passed on: `parse_s3_uri` drops it, `aws s3 cp` keeps it, so
        the object checked would not be the object fetched."""
        allowed = {ASSET_BUCKET}
        assert container.parse_s3_uri(
            f"s3://{ASSET_BUCKET}/{ASSET_ID}/edge.mp4?versionId=abc")[1] == f"{ASSET_ID}/edge.mp4"
        with pytest.raises(ValueError):
            container.validate_control_s3_uri(
                f"s3://{ASSET_BUCKET}/{ASSET_ID}/edge.mp4?versionId=abc", allowed)


# ============================ the rejection reaches the failure path, early ============================

class TestRejectionIsEarlyAndFatal:
    """This container reports failure by raising: the exception leaves `main()`, Python exits
    non-zero, the Batch job is FAILED, and the state machine's addCatch routes to `pipelineEnd`,
    which resolves the parent workflow's task token."""

    def test_the_rejected_object_is_never_fetched(self, run_container):
        """The read this exists to refuse: an unrelated bucket's object pulled into the run under
        the job role's credentials."""
        with pytest.raises(ValueError):
            run_container(transfer_definition(
                cosmosControlPath="s3://someone-elses-bucket/secret.mp4"))
        assert run_container.last.downloads == [], (
            "the disallowed object was pulled into the run anyway")

    def test_no_output_is_uploaded_for_a_rejected_run(self, run_container):
        with pytest.raises(ValueError):
            run_container(transfer_definition(
                cosmosControlPath="s3://someone-elses-bucket/secret.mp4"))
        assert run_container.last.uploads == []

    def test_the_rejection_precedes_the_model_restore(self, run_container):
        """The GPU node is already running when this container starts, so the cost still avoidable is
        everything after the container's own start -- the model restore first of all, which moves tens
        of gigabytes. A check left where the value is USED (step 2b) sits after it."""
        with pytest.raises(ValueError, match="COSMOS3_CONTROL_PATH"):
            run_container(transfer_definition(
                cosmosControlPath="s3://someone-elses-bucket/secret.mp4"))
        assert run_container.last.model_restores == [], (
            "the model restore ran before the control path was checked")

    def test_inference_never_starts_for_a_rejected_run(self, run_container):
        with pytest.raises(ValueError):
            run_container(transfer_definition(
                cosmosControlPath="s3://someone-elses-bucket/secret.mp4"))
        assert run_container.last.inference == []


# ============================ acceptance: the over-restriction control ============================

class TestOrdinaryTransferRunsStillRun:

    def test_a_control_in_the_runs_own_asset_bucket_is_accepted(self, run_container):
        record = run_container(transfer_definition(
            cosmosControlPath=f"s3://{ASSET_BUCKET}/{ASSET_ID}/controls/edge.mp4"))
        assert f"s3://{ASSET_BUCKET}/{ASSET_ID}/controls/edge.mp4" in record.downloads
        assert len(record.upload_uris) == 1

    def test_a_control_in_another_asset_of_the_same_bucket_is_accepted(self, run_container):
        """Cross-asset control videos are part of the contract: the allowlist is per BUCKET, not per
        asset."""
        record = run_container(transfer_definition(
            cosmosControlPath=f"s3://{ASSET_BUCKET}/xOTHERASSET/controls/edge.mp4"))
        assert f"s3://{ASSET_BUCKET}/xOTHERASSET/controls/edge.mp4" in record.downloads

    def test_a_control_staged_in_the_auxiliary_bucket_is_accepted(self, run_container):
        """The auxiliary bucket is named by the run's own definition, so it is the deployment's."""
        record = run_container(transfer_definition(
            cosmosControlPath=f"s3://{AUXILIARY_BUCKET}/staging/edge.mp4"))
        assert f"s3://{AUXILIARY_BUCKET}/staging/edge.mp4" in record.downloads

    def test_a_second_asset_bucket_is_accepted_once_the_deployment_declares_it(self, run_container):
        """A control video in a DIFFERENT database's bucket reaches the allowlist only through
        ALLOWED_INPUT_BUCKETS, which the job definition supplies."""
        record = run_container(
            transfer_definition(cosmosControlPath="s3://vams-assets-two/xOTHER/edge.mp4"),
            allowed_input_buckets=f"vams-assets-two, {ASSET_BUCKET}")
        assert "s3://vams-assets-two/xOTHER/edge.mp4" in record.downloads

    def test_the_same_bucket_is_rejected_when_the_deployment_does_not_declare_it(self, run_container):
        """The negative half of the test above: without the variable that bucket is not reachable, so
        the acceptance there is the variable's doing rather than a vacuous pass."""
        with pytest.raises(ValueError, match="vams-assets-two"):
            run_container(transfer_definition(
                cosmosControlPath="s3://vams-assets-two/xOTHER/edge.mp4"))

    def test_a_transfer_run_with_no_control_path_auto_computes(self, run_container):
        """The common case: a blank path leaves the framework to compute the signal from the source
        video, so it must not be validated as if it were a URI."""
        record = run_container(transfer_definition(cosmosControlPath=""))
        assert record.downloads == [f"s3://{ASSET_BUCKET}/{ASSET_ID}/source.mp4"]
        assert "control_path" not in record.inference_kwargs["control_blocks"]["edge"]

    def test_a_multi_control_blend_validates_each_path_positionally(self, run_container):
        record = run_container(transfer_definition(
            cosmosControlType="edge,blur",
            cosmosControlPath=f",s3://{ASSET_BUCKET}/{ASSET_ID}/controls/blur.mp4"))
        blocks = record.inference_kwargs["control_blocks"]
        assert "control_path" not in blocks["edge"]
        assert blocks["blur"]["control_path"].endswith("control_blur_blur.mp4")

    def test_one_bad_entry_in_a_blend_fails_the_whole_run(self, run_container):
        with pytest.raises(ValueError, match="COSMOS3_CONTROL_PATH"):
            run_container(transfer_definition(
                cosmosControlType="edge,blur",
                cosmosControlPath=f"s3://{ASSET_BUCKET}/{ASSET_ID}/e.mp4,s3://elsewhere/b.mp4"))

    def test_a_text2video_run_is_unaffected(self, run_container):
        """The check is reached only for transfer, so the other three modes carry no new failure."""
        record = run_container(base_definition())
        assert len(record.upload_uris) == 1
        assert record.inference_kwargs["control_blocks"] is None


# ============================ the allowlist itself ============================

class TestAllowlistComposition:

    def test_the_runs_own_locations_are_always_allowed(self, container):
        buckets = container.allowed_control_buckets(transfer_definition())
        assert ASSET_BUCKET in buckets and AUXILIARY_BUCKET in buckets

    def test_the_environment_variable_widens_the_set(self, container, monkeypatch):
        monkeypatch.setenv(container.ALLOWED_INPUT_BUCKETS_ENV, "bucket-a,bucket-b")
        buckets = container.allowed_control_buckets(transfer_definition())
        assert {"bucket-a", "bucket-b", ASSET_BUCKET} <= buckets

    def test_a_malformed_variable_neither_empties_nor_opens_the_set(self, container, monkeypatch):
        """A blank or wildcard entry must not become an allowed bucket. '*' is not a legal S3 bucket
        name, so whole-name equality can never match it -- asserted rather than assumed."""
        monkeypatch.setenv(container.ALLOWED_INPUT_BUCKETS_ENV, " , ,*,")
        buckets = container.allowed_control_buckets(transfer_definition())
        assert ASSET_BUCKET in buckets
        with pytest.raises(ValueError):
            container.validate_control_s3_uri("s3://anything-at-all/edge.mp4", buckets)

    def test_an_empty_allowlist_rejects_everything_including_a_named_bucket(self, container):
        """The failure mode of the sources themselves: a definition with no S3 locations and no
        variable leaves nothing allowed, which must READ as a rejection rather than an allow-all."""
        assert container.allowed_control_buckets({}) == set()
        with pytest.raises(ValueError, match="none resolved"):
            container.validate_control_s3_uri("s3://any-bucket/edge.mp4", set())

    def test_an_accepted_uri_is_returned_unchanged(self, container):
        """The contract is the full URI, so validation must not rewrite it into an asset-relative or
        re-based form."""
        uri = f"s3://{ASSET_BUCKET}/{ASSET_ID}/controls/edge.mp4"
        assert container.validate_control_s3_uri(uri, {ASSET_BUCKET}) == uri

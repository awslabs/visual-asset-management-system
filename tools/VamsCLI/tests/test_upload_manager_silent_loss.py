"""A file the CLI does not upload must never be reported as uploaded.

`S39-PRE-024`: seeding a 4,101-file asset with one
`file upload --directory --recursive --parallel-uploads 16` returned **rc=0** while exactly two objects
were absent from S3 — `group000/sub0/part00000.glb` and its `.previewFile.png` companion, established
by diffing the reconstructed expected name set against a full recursive listing. It did NOT reproduce
at 13, 601, or 4,101 files with the identical shape and flags, so scale is excluded; the one measured
difference was CONTENTION (four smoke lanes driving the same API concurrently).

The silence, not the loss, is what makes it severe: a retryable error the caller can see is an
inconvenience, whereas `overall_success: True` with the object absent is undetectable without an
independent listing.

This suite covers four accounting paths by which a file the CLI did not store is reported as stored.
The **first is the measured root cause**, found by reading the CLI's own DEBUG log for a reproduction
run; the other three were found by reading the accounting and are latent rather than observed.

*   **The server's rejection was unreachable.** The completion endpoint returns `fileResults`,
    `overallSuccess` and a human-readable `message` STRING side by side at the top level, so the
    unwrapping idiom `body.get("message", body)` replaced the whole response with
    `"No files were successfully uploaded"` and the reconciliation below it became dead code. The
    server said `success: False, error: "Base files does not exist for all preview files"` and
    `overallSuccess: False`; the CLI reported the file stored and exited 0. See
    `TestAServerRejectionIsNotReportedAsSuccess`, whose fixture is that response verbatim.
*   **`_complete_sequence` infers "zero-byte file" from an empty part list.** The part list is built
    from the SERVER's `initialize_upload` response (`init_response["files"][...]["partUploadUrls"]`),
    not from the locally planned `sequence.file_parts`. So a response that omits a file's part URLs —
    or returns fewer than were asked for — makes a multi-megabyte file look like a zero-byte one: it
    is added to `successful_files`, sent to `complete_upload` with `"parts": []`, and reported as
    stored. Nothing was ever put to S3.
*   **A short part list is worse than a missing one.** Amazon S3's `CompleteMultipartUpload` accepts a
    SUBSET of the parts that were initiated and produces a correspondingly TRUNCATED object, so a
    partial list yields a file that exists, has a plausible size, and is silently corrupt.
*   **Nothing reconciles the totals.** `upload_all_sequences` sums `successful_files` and
    `failed_files` per sequence and derives `overall_success` from the failure count alone. It never
    checks that the two account for every staged file, so a file omitted from BOTH lists is invisible:
    not successful, not failed, exit code 0.

These are asserted at the accounting layer rather than by reproducing the contention, deliberately.
Scale was already excluded by a clean 4,101-file re-run, so a load-based repro is a probabilistic
search for a trigger; the accounting invariants hold regardless of WHICH upstream condition produces
the rejection or the degraded response, and they are what turn any of them from silent into loud.

:::note[The server's rejection was CORRECT — the CLI's silence about it was the defect]
The run that surfaced this had staged the companion at the asset root while its base file sat in a
subdirectory, so the base path the companion implies (`/f000-….glb`) genuinely did not exist and
`uploadFile.py`'s `head_object` check was right to refuse it. An initial reading of this as a
server-side existence race was wrong, and worth recording as wrong: the base file WAS in S3, just not
at the path the companion names, and "the object exists somewhere" is not the predicate that check
evaluates.

So nothing here asks the backend to change. What this suite guarantees is that whenever the server
rejects a file — for any reason, correct or not — the caller is told. A rejection the user can see and
act on is an inconvenience; `overall_success: True` with the object absent is undetectable without an
independent listing of three buckets, which is how it survived a release.
:::
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from vamscli.utils.file_processor import FileInfo, UploadSequence
from vamscli.utils.upload_manager import UploadManager, UploadProgress


_MB = 1024 * 1024


def _sequence(sizes, sequence_id=0, prefix="/group000/sub0/part"):
    """One sequence of files with locally planned parts, as `create_upload_sequences` leaves it."""
    files = []
    for index, size in enumerate(sizes):
        files.append(FileInfo(local_path=f"C:/nonexistent/f{index}.glb",
                              relative_key=f"{prefix}{index:05d}.glb", size=size))
    sequence = UploadSequence(files, sequence_id)
    sequence.calculate_parts()
    return sequence


def _init_response(sequence, upload_id="upload-1", drop_parts_for=None, truncate_to=None):
    """An `initialize_upload` response for `sequence`.

    `drop_parts_for` returns an EMPTY `partUploadUrls` for that relative key — the degraded response
    this suite is about. `truncate_to` returns only that many URLs, which is the corrupting variant.
    """
    files = []
    for file_info in sequence.files:
        planned = sequence.file_parts[file_info.relative_key]
        if drop_parts_for == file_info.relative_key:
            urls = []
        elif truncate_to is not None and truncate_to.get(file_info.relative_key) is not None:
            urls = [{"UploadUrl": f"https://s3.invalid/{i}"}
                    for i in range(truncate_to[file_info.relative_key])]
        else:
            urls = [{"UploadUrl": f"https://s3.invalid/{i}"} for i in range(len(planned))]
        files.append({
            "relativeKey": file_info.relative_key,
            "uploadIdS3": f"s3-{file_info.relative_key}",
            "partUploadUrls": urls,
        })
    return {"uploadId": upload_id, "files": files}


def _manager():
    """An UploadManager with no network. `complete_upload` records what it was asked to complete.

    `_api_semaphore` is created in `__aenter__`, not `__init__`, so a manager built by calling the
    constructor alone raises `AttributeError` inside `_complete_sequence` — which that method's
    `except Exception` converts into `FileUploadError("Failed to complete upload...")`. That reads
    exactly like the defect under test: the file ends up reported failed. It was the zero-byte
    CONTROL failing that identified this as a harness gap rather than a product one, which is the
    whole reason that control exists.
    """
    api_client = MagicMock()
    manager = UploadManager(api_client)
    manager._api_semaphore = asyncio.Semaphore(2)
    return manager, api_client


def _complete(manager, sequence, init_response, mark_parts_completed=True):
    """Drive `_complete_sequence` for one sequence, as `_process_sequence` would after part uploads.

    Parts are marked completed to isolate the accounting: this suite is about what happens to a file
    the SERVER gave no parts for, not about a part upload that failed (which is already reported).
    """
    from vamscli.utils.upload_manager import SequenceInitResult, PartUploadInfo

    part_uploads = []
    for file_response in init_response["files"]:
        key = file_response["relativeKey"]
        file_info = next(f for f in sequence.files if f.relative_key == key)
        planned = sequence.file_parts[key]
        for index, url_info in enumerate(file_response["partUploadUrls"]):
            part = PartUploadInfo(file_info, planned[index], url_info["UploadUrl"],
                                  sequence.sequence_id)
            if mark_parts_completed:
                part.status = "completed"
                part.etag = f"etag-{index}"
            part_uploads.append(part)

    init_result = SequenceInitResult(sequence.sequence_id, init_response["uploadId"],
                                     init_response, sequence, part_uploads)
    progress = UploadProgress([sequence])
    return asyncio.run(manager._complete_sequence(
        init_result, "db1", "asset1", "assetFile", progress))


class TestAFileWithNoServerPartsIsNotSuccess:
    """The core mechanism. A non-empty file the server gave no part URLs for was reported stored."""

    def test_a_nonempty_file_given_no_part_urls_is_reported_failed(self):
        manager, api_client = _manager()
        api_client.complete_upload.return_value = {"fileResults": []}
        sequence = _sequence([5 * _MB, 5 * _MB])
        lost = sequence.files[0].relative_key
        init_response = _init_response(sequence, drop_parts_for=lost)

        result = _complete(manager, sequence, init_response)

        assert lost in result["failed_files"], (
            f"{lost} is {5 * _MB} bytes and the server returned no part upload URLs for it, so "
            f"nothing was put to S3 — but it was reported as stored. successful="
            f"{result['successful_files']} failed={result['failed_files']}")
        assert lost not in result["successful_files"], result

    def test_it_is_not_offered_to_complete_upload_with_an_empty_part_list(self):
        # The other half of the same defect: completing a multipart upload with "parts": [] is what
        # made the server agree the file was done. Asserting only the report would leave that intact.
        manager, api_client = _manager()
        api_client.complete_upload.return_value = {"fileResults": []}
        sequence = _sequence([5 * _MB, 5 * _MB])
        lost = sequence.files[0].relative_key

        _complete(manager, sequence, _init_response(sequence, drop_parts_for=lost))

        if not api_client.complete_upload.called:
            return          # nothing completed at all is also acceptable here
        completion_files = api_client.complete_upload.call_args[0][4]
        offered = {entry["relativeKey"]: entry["parts"] for entry in completion_files}
        assert lost not in offered, (
            f"{lost} was offered to complete_upload with parts={offered.get(lost)!r}; an empty part "
            f"list on a non-empty file completes an upload that never happened")

    def test_a_genuinely_zero_byte_file_still_succeeds(self):
        # The control that keeps the arms above from being satisfied by "treat every empty part list
        # as a failure". A 0-byte file legitimately has no parts and must still be stored.
        manager, api_client = _manager()
        api_client.complete_upload.return_value = {"fileResults": []}
        sequence = _sequence([0, 5 * _MB])
        empty = sequence.files[0].relative_key

        result = _complete(manager, sequence, _init_response(sequence))

        assert empty in result["successful_files"], (
            f"a 0-byte file was reported failed: {result}")
        assert empty not in result["failed_files"], result
        completion_files = api_client.complete_upload.call_args[0][4]
        offered = {entry["relativeKey"]: entry["parts"] for entry in completion_files}
        assert offered.get(empty) == [], (
            f"a 0-byte file must still be completed with an empty part list, got {offered.get(empty)!r}")


class TestAShortPartListIsNotSuccess:
    """Amazon S3 completes a multipart upload from a SUBSET of its initiated parts and produces a
    truncated object, so a short part list is silent corruption rather than a missing file."""

    def test_a_file_given_fewer_parts_than_planned_is_reported_failed(self):
        manager, api_client = _manager()
        api_client.complete_upload.return_value = {"fileResults": []}
        # Large enough to plan several parts, so "fewer than planned" is expressible.
        sequence = _sequence([600 * _MB])
        key = sequence.files[0].relative_key
        planned = len(sequence.file_parts[key])
        if planned < 2:
            pytest.skip(f"chunking planned {planned} part(s); this arm needs at least 2")

        result = _complete(manager, sequence, _init_response(
            sequence, truncate_to={key: planned - 1}))

        assert key in result["failed_files"], (
            f"{planned - 1} of {planned} parts were uploaded and the file was reported stored; S3 "
            f"would complete it as a TRUNCATED object. result={result}")


class TestTotalsAccountForEveryStagedFile:
    """The net that makes any future omission loud. `overall_success` is derived from the failure
    count alone, so a file missing from both per-sequence lists is invisible today."""

    def test_a_file_missing_from_both_lists_is_not_reported_as_success(self):
        manager, _api_client = _manager()
        sequence = _sequence([5 * _MB, 5 * _MB, 5 * _MB])
        lost = sequence.files[1].relative_key

        # A sequence result that simply forgets one staged file — the shape any upstream degradation
        # would have to produce for the run to report rc=0 with an object absent.
        async def _fake_process(seq, *args, **kwargs):
            return {
                "sequence_id": seq.sequence_id,
                "successful_files": [f.relative_key for f in seq.files
                                     if f.relative_key != lost],
                "failed_files": [],
            }

        manager._process_sequence = _fake_process
        summary = asyncio.run(manager.upload_all_sequences(
            [sequence], "db1", "asset1", "assetFile"))

        assert summary["overall_success"] is False, (
            f"{summary['successful_files']} of {summary['total_files']} staged files were accounted "
            f"for and the run still reported overall_success=True; this is the exact shape of the "
            f"rc=0-with-missing-objects report. summary={ {k: v for k, v in summary.items() if k != 'progress'} }")

    def test_a_fully_accounted_run_still_reports_success(self):
        # The control. Without it, "overall_success is False" would be satisfiable by failing
        # every upload.
        manager, _api_client = _manager()
        sequence = _sequence([5 * _MB, 5 * _MB, 5 * _MB])

        async def _fake_process(seq, *args, **kwargs):
            return {
                "sequence_id": seq.sequence_id,
                "successful_files": [f.relative_key for f in seq.files],
                "failed_files": [],
            }

        manager._process_sequence = _fake_process
        summary = asyncio.run(manager.upload_all_sequences(
            [sequence], "db1", "asset1", "assetFile"))

        assert summary["overall_success"] is True, summary
        assert summary["successful_files"] == 3, summary
        assert summary["failed_files"] == 0, summary


class TestAServerRejectionIsNotReportedAsSuccess:
    """The measured root cause of `S39-PRE-024`'s companion loss: the reconciliation was DEAD CODE.

    The completion endpoint returns `fileResults`, `overallSuccess` and a human-readable `message`
    STRING side by side at the top level. The unwrapping idiom `body.get("message", body)` therefore
    replaced the whole response with the string `"No files were successfully uploaded"`, the following
    `isinstance(body, dict)` guard turned `file_results` into `None`, and no file was ever demoted.

    Captured live on prod5: a 14-file directory upload reported `overall_success: True`,
    `successful_files: 14`, `failed_files: 0`, exit 0 — and a recursive listing of the asset bucket, the
    auxiliary bucket and the artefacts bucket found 13 objects. The absent one was the `.previewFile.`
    companion, for which the server had answered `success: False`, `overallSuccess: False`.

    The response below is that response verbatim (secrets elided), which is the point: a hand-written
    approximation of the envelope is what allowed the idiom to look correct in the first place.
    """

    # Verbatim from the live completion response, 2026-09-05T19:30:14 on prod5.
    LIVE_REJECTION = {
        "message": "No files were successfully uploaded",
        "uploadId": "y5a3de255-b692-4790-8a61-4a46dc31bf00",
        "assetId": "s39q80clean2",
        "assetType": None,
        "fileResults": [
            {
                "relativeKey": "/f000-232945.glb.previewFile.png",
                "uploadIdS3": "yEigPT0vl0...elided",
                "success": False,
                "error": "Base files does not exist for all preview files",
                "largeFileAsynchronousHandling": False,
            }
        ],
        "overallSuccess": False,
        "largeFileAsynchronousHandling": False,
    }

    def test_the_rejected_file_is_reported_failed(self):
        manager, api_client = _manager()
        api_client.complete_upload.return_value = self.LIVE_REJECTION
        sequence = _sequence([2048], prefix="/f000-232945.glb.previewFile.pn")
        # Name the file exactly as the live response does, since the demotion matches on relativeKey.
        sequence.files[0].relative_key = "/f000-232945.glb.previewFile.png"
        sequence.calculate_parts()
        key = sequence.files[0].relative_key

        result = _complete(manager, sequence, _init_response(sequence))

        assert key in result["failed_files"], (
            f"the server answered success=False / overallSuccess=False for {key} and the CLI still "
            f"reported it stored. result={result}")
        assert key not in result["successful_files"], result

    def test_a_message_string_does_not_hide_fileresults(self):
        # Pins the specific idiom. If `message` is unwrapped unconditionally the response becomes a
        # string and `fileResults` becomes unreachable, which is the whole defect.
        manager, api_client = _manager()
        api_client.complete_upload.return_value = self.LIVE_REJECTION
        sequence = _sequence([2048])
        sequence.files[0].relative_key = "/f000-232945.glb.previewFile.png"
        sequence.calculate_parts()

        result = _complete(manager, sequence, _init_response(sequence))
        assert result["failed_files"], (
            "fileResults was unreachable, so nothing was demoted — the `message`-string unwrap is back")

    def test_overall_success_false_alone_still_fails_the_files(self):
        # The backstop. A deployment that renames or omits fileResults, or keys it differently from
        # `successful_files`, must not be able to yield a success report anyway.
        manager, api_client = _manager()
        api_client.complete_upload.return_value = {
            "message": "No files were successfully uploaded",
            "overallSuccess": False,
        }
        sequence = _sequence([5 * _MB, 5 * _MB])

        result = _complete(manager, sequence, _init_response(sequence))

        assert result["successful_files"] == [], (
            f"overallSuccess=False with no usable fileResults still reported stored files: {result}")
        assert len(result["failed_files"]) == 2, result

    def test_a_nested_message_envelope_is_still_unwrapped(self):
        # The control for the unwrap change: a response that DOES nest its body under `message` must
        # still be read, or fixing the string case would break every deployment using that envelope.
        manager, api_client = _manager()
        sequence = _sequence([5 * _MB])
        key = sequence.files[0].relative_key
        api_client.complete_upload.return_value = {
            "message": {
                "fileResults": [{"relativeKey": key, "success": False, "error": "nested envelope"}],
            }
        }

        result = _complete(manager, sequence, _init_response(sequence))
        assert key in result["failed_files"], (
            f"a nested `message` envelope stopped being unwrapped: {result}")

    def test_a_successful_completion_is_still_success(self):
        # The control that keeps all of the above from being satisfied by failing everything.
        manager, api_client = _manager()
        sequence = _sequence([5 * _MB, 5 * _MB])
        api_client.complete_upload.return_value = {
            "message": "Upload completed successfully",
            "overallSuccess": True,
            "fileResults": [{"relativeKey": f.relative_key, "success": True}
                            for f in sequence.files],
        }

        result = _complete(manager, sequence, _init_response(sequence))
        assert sorted(result["successful_files"]) == sorted(f.relative_key for f in sequence.files), result
        assert result["failed_files"] == [], result

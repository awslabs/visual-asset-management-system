"""The upload's success count must follow the SERVER's per-file verdict, not just our part uploads.

WHY THIS EXISTS. `_complete_sequence` decided success from whether its own S3 part uploads finished,
then called the completion API and never looked at the reply. Completion is where the server runs its
validations, and a file it rejects is DELETED rather than stored — so `file upload` printed
"Successful files: 4/4" for an upload where one file came back
`success: false, error: "Error verifying base file for preview file"` and no object was left in the
bucket. Silent data loss with a success report, found on a live deployment.

The `except` around the completion call only fires when the CALL fails; a 200 carrying per-file
failures passes straight through it, which is why the check has to read the RESPONSE body.

Both directions are asserted, because either alone is satisfied by a broken implementation:
  * a rejected file must be demoted — otherwise the loss stays silent;
  * an accepted file, and a response with no `success` field at all, must NOT be demoted — otherwise
    every upload fails, including against a deployment whose response predates that field.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from vamscli.utils.upload_manager import UploadManager


class _Part:
    """One part upload that has already finished, which is what makes the file a success candidate."""

    def __init__(self, relative_key, part_number=1):
        self.file_info = MagicMock()
        self.file_info.relative_key = relative_key
        self.part_number = part_number
        self.etag = f"etag-{part_number}"
        self.status = "completed"


def _sequence(keys):
    seq = MagicMock()
    seq.sequence_id = 1
    files = []
    for key in keys:
        info = MagicMock()
        info.relative_key = key
        files.append(info)
    seq.files = files
    return seq


def _init_result(keys):
    init = MagicMock()
    init.sequence_id = 1
    init.upload_id = "upload-1"
    init.sequence = _sequence(keys)
    init.init_response = {"files": [{"relativeKey": k, "uploadIdS3": f"s3-{k}"} for k in keys]}
    init.part_uploads = [_Part(k) for k in keys]
    return init


def _run(completion_response, keys=("/base.glb", "/base.glb.previewFile.png")):
    """Drive _complete_sequence with a scripted completion response and return its result."""
    api_client = MagicMock()
    api_client.complete_upload.return_value = completion_response
    manager = UploadManager(api_client=api_client)

    async def drive():
        # __aenter__ builds the session and the API semaphore the method awaits on.
        async with manager as m:
            progress = MagicMock()
            progress.completed_sequences = 0
            return await m._complete_sequence(
                _init_result(list(keys)), "db1", "asset1", "assetFile", progress
            )

    return asyncio.run(drive())


@pytest.mark.parametrize("envelope", ["bare", "message"])
def test_a_file_the_server_rejects_is_not_counted_successful(envelope):
    """The defect itself: a per-file `success: false` must move the file to failed.

    Parameterized over both envelopes because the API's list endpoints wrap their body in `message`
    while others do not, and reading only one shape would leave the other silently unchecked.
    """
    body = {
        "overallSuccess": False,
        "fileResults": [
            {"relativeKey": "/base.glb", "uploadIdS3": "s3-1", "success": True},
            {
                "relativeKey": "/base.glb.previewFile.png",
                "uploadIdS3": "s3-2",
                "success": False,
                "error": "Error verifying base file for preview file",
            },
        ],
    }
    response = body if envelope == "bare" else {"message": body}

    result = _run(response)

    assert result["successful_files"] == ["/base.glb"], result["successful_files"]
    assert result["failed_files"] == ["/base.glb.previewFile.png"], result["failed_files"]


def test_an_accepted_file_stays_successful():
    """Paired arm. Without it, demoting every file would satisfy the test above."""
    result = _run(
        {
            "overallSuccess": True,
            "fileResults": [
                {"relativeKey": "/base.glb", "uploadIdS3": "s3-1", "success": True},
                {"relativeKey": "/base.glb.previewFile.png", "uploadIdS3": "s3-2", "success": True},
            ],
        }
    )

    assert sorted(result["successful_files"]) == ["/base.glb", "/base.glb.previewFile.png"]
    assert result["failed_files"] == []


def test_a_response_without_per_file_results_demotes_nothing():
    """An older deployment, or an endpoint that reports no per-file detail, must not fail every file.

    This is the direction that would break real uploads if the check treated a MISSING `success` as
    failure, so it is asserted rather than assumed.
    """
    result = _run({"message": "Upload completed"})

    assert sorted(result["successful_files"]) == ["/base.glb", "/base.glb.previewFile.png"]
    assert result["failed_files"] == []


def test_a_result_omitting_success_is_not_treated_as_failure():
    """Only an EXPLICIT false demotes. A result carrying no `success` key is not evidence of failure."""
    result = _run(
        {
            "fileResults": [
                {"relativeKey": "/base.glb", "uploadIdS3": "s3-1"},
                {"relativeKey": "/base.glb.previewFile.png", "uploadIdS3": "s3-2"},
            ]
        }
    )

    assert sorted(result["successful_files"]) == ["/base.glb", "/base.glb.previewFile.png"]
    assert result["failed_files"] == []


def test_sync_and_file_upload_share_the_reconciled_code_path():
    """Both upload commands must reach the fix, not just `file upload`.

    `sync file push` and `file upload` each construct an `UploadManager`, and `complete_upload` has a
    single call site inside it — so the reconciliation covers both. Asserted on the source so a future
    command that hand-rolls its own completion is caught here rather than in production.
    """
    import importlib
    import inspect

    # importlib, not `from vamscli.commands import file`: the package re-exports the click Groups
    # under the same names, so that form binds a Group and inspect.getsource raises TypeError.
    file_cmd = importlib.import_module("vamscli.commands.file")
    sync_cmd = importlib.import_module("vamscli.commands.sync")

    for module in (file_cmd, sync_cmd):
        source = inspect.getsource(module)
        assert "UploadManager(" in source, f"{module.__name__} no longer uses UploadManager"
        assert "complete_upload" not in source, (
            f"{module.__name__} calls complete_upload directly, bypassing the per-file verdict "
            f"reconciliation in UploadManager._complete_sequence"
        )


def test_preview_sequences_complete_only_after_every_regular_sequence():
    """The barrier that makes the server's base-file check deterministic.

    A `.previewFile.` companion is always sequenced separately from its base file, and completion
    verifies the base file is either in the same request or already in S3. Neither is true until the
    base file's sequence has completed, so the preview group must not start until the regular group
    has finished.

    This existed only as a comment before: the implementation concatenated the two groups and handed
    them to ONE `asyncio.gather`, which starts everything immediately. List order is not ordering. The
    live symptom was 2-6 of 12 companions rejected per run, varying because it was a race.

    Asserted on the observed ORDER of completion calls rather than on the source, so a future refactor
    that reintroduces a single gather fails here.
    """
    from unittest.mock import patch

    from vamscli.utils.file_processor import FileInfo

    completion_order = []

    def _file(rel_key, size=1):
        info = MagicMock(spec=FileInfo)
        info.relative_key = rel_key
        info.size = size
        info.is_preview_file = ".previewFile." in rel_key
        return info

    def _seq(seq_id, keys):
        seq = MagicMock()
        seq.sequence_id = seq_id
        seq.files = [_file(k) for k in keys]
        return seq

    # Two regular sequences and two preview sequences, interleaved in the input so a correct result
    # cannot come from the input order alone.
    sequences = [
        _seq(1, ["/a.glb"]),
        _seq(2, ["/a.glb.previewFile.png"]),
        _seq(3, ["/b.glb"]),
        _seq(4, ["/b.glb.previewFile.png"]),
    ]

    manager = UploadManager(api_client=MagicMock())

    async def fake_process(seq, *_args, **_kwargs):
        # Yield control so a concurrent implementation genuinely interleaves rather than running to
        # completion synchronously — without this the test would pass against a single gather.
        await asyncio.sleep(0.01 if any(f.is_preview_file for f in seq.files) else 0.05)
        completion_order.append(seq.sequence_id)
        return {"sequence_id": seq.sequence_id, "successful_files": [f.relative_key for f in seq.files],
                "failed_files": [], "total_parts": 0, "successful_parts": 0, "failed_parts": 0}

    async def drive():
        async with manager as m:
            with patch.object(m, "_process_sequence", side_effect=fake_process):
                return await m.upload_all_sequences(sequences, "db1", "asset1", "assetFile")

    asyncio.run(drive())

    regular_positions = [completion_order.index(i) for i in (1, 3)]
    preview_positions = [completion_order.index(i) for i in (2, 4)]
    assert max(regular_positions) < min(preview_positions), (
        f"a preview sequence completed before a regular one: order={completion_order}. The preview "
        f"group must be awaited only after every regular sequence has completed."
    )
    # Control: the preview sequences are given a SHORTER delay above, so under a single concurrent
    # gather they would finish FIRST. That is what makes the assertion above discriminating rather
    # than accidentally satisfied by timing.
    assert completion_order[:2] == [1, 3] or completion_order[:2] == [3, 1], completion_order

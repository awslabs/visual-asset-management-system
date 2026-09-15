"""Upload manager for handling file uploads with progress monitoring."""

import asyncio
import aiohttp
import aiofiles
import time
from typing import List, Dict, Any, Optional, Callable, Tuple
from pathlib import Path
from collections import defaultdict

from ..constants import DEFAULT_PARALLEL_UPLOADS, DEFAULT_RETRY_ATTEMPTS
from .exceptions import FileUploadError, PartUploadError
from .file_processor import UploadSequence, FileInfo, format_file_size
from .api_client import APIClient
from .logging import log_warning


class PartUploadInfo:
    """Information about a part upload."""
    
    def __init__(self, file_info: FileInfo, part_info: Dict[str, Any], upload_url: str, sequence_id: int):
        self.file_info = file_info
        self.part_info = part_info
        self.upload_url = upload_url
        self.sequence_id = sequence_id  # Track which sequence this part belongs to
        self.part_number = part_info["part_number"]
        self.start_byte = part_info["start_byte"]
        self.end_byte = part_info["end_byte"]
        self.size = part_info["size"]
        self.etag = None
        self.status = "pending"  # pending, uploading, completed, failed
        self.error = None
        self.retry_count = 0
        self.upload_start_time = None
        self.upload_end_time = None
        
    @property
    def upload_duration(self) -> Optional[float]:
        """Get upload duration in seconds."""
        if self.upload_start_time and self.upload_end_time:
            return self.upload_end_time - self.upload_start_time
        return None
    
    @property
    def upload_speed(self) -> Optional[float]:
        """Get upload speed in bytes per second."""
        duration = self.upload_duration
        if duration and duration > 0:
            return self.size / duration
        return None


class UploadProgress:
    """Track upload progress across all sequences."""
    
    def __init__(self, sequences: List[UploadSequence]):
        self.sequences = sequences
        self.total_files = sum(len(seq.files) for seq in sequences)
        self.total_size = sum(seq.total_size for seq in sequences)
        self.total_parts = sum(seq.total_parts for seq in sequences)
        
        # Progress tracking
        self.completed_parts = 0
        self.completed_size = 0
        self.failed_parts = 0
        self.active_uploads = 0
        self.start_time = time.time()
        
        # Sequence tracking
        self.total_sequences = len(sequences)
        self.initialized_sequences = 0
        self.uploaded_sequences = 0  # Sequences with all parts uploaded
        self.completed_sequences = 0  # Sequences with completion API finished
        
        # File-level progress
        self.file_progress = {}  # relative_key -> {"completed_parts": int, "total_parts": int, "completed_size": int, "total_size": int}
        
        # Initialize file progress
        for sequence in sequences:
            for file_info in sequence.files:
                parts = sequence.file_parts[file_info.relative_key]
                self.file_progress[file_info.relative_key] = {
                    "completed_parts": 0,
                    "total_parts": len(parts),
                    "completed_size": 0,
                    "total_size": file_info.size,
                    "status": "pending"  # pending, uploading, completed, failed
                }
    
    def update_part_progress(self, part_info: PartUploadInfo):
        """Update progress for a completed part."""
        file_key = part_info.file_info.relative_key
        
        if part_info.status == "completed":
            self.completed_parts += 1
            self.completed_size += part_info.size
            
            # Update file progress
            self.file_progress[file_key]["completed_parts"] += 1
            self.file_progress[file_key]["completed_size"] += part_info.size
            
            # Check if file is complete
            if (self.file_progress[file_key]["completed_parts"] >= 
                self.file_progress[file_key]["total_parts"]):
                self.file_progress[file_key]["status"] = "completed"
                
        elif part_info.status == "failed":
            self.failed_parts += 1
            self.file_progress[file_key]["status"] = "failed"
    
    @property
    def overall_progress(self) -> float:
        """Get overall progress as percentage."""
        if self.total_size == 0:
            return 100.0
        return (self.completed_size / self.total_size) * 100
    
    @property
    def elapsed_time(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time
    
    @property
    def estimated_time_remaining(self) -> Optional[float]:
        """Estimate time remaining in seconds."""
        if self.completed_size == 0:
            return None
        
        elapsed = self.elapsed_time
        rate = self.completed_size / elapsed
        remaining_size = self.total_size - self.completed_size
        
        if rate > 0:
            return remaining_size / rate
        return None
    
    @property
    def upload_speed(self) -> float:
        """Get current upload speed in bytes per second."""
        elapsed = self.elapsed_time
        if elapsed > 0:
            return self.completed_size / elapsed
        return 0.0


class SequenceInitResult:
    """Result of sequence initialization."""
    
    def __init__(self, sequence_id: int, upload_id: str, init_response: Dict[str, Any],
                 sequence: UploadSequence, part_uploads: List[PartUploadInfo]):
        self.sequence_id = sequence_id
        self.upload_id = upload_id
        self.init_response = init_response
        self.sequence = sequence
        self.part_uploads = part_uploads


class UploadManager:
    """Manages file uploads with progress monitoring and retry logic."""
    
    def __init__(self, api_client: APIClient, max_parallel: int = DEFAULT_PARALLEL_UPLOADS,
                 max_retries: int = DEFAULT_RETRY_ATTEMPTS, force_skip: bool = False,
                 progress_callback: Optional[Callable[[UploadProgress], None]] = None):
        self.api_client = api_client
        self.max_parallel = max_parallel
        self.max_retries = max_retries
        self.force_skip = force_skip
        self.progress_callback = progress_callback
        self.session = None
        
    async def __aenter__(self):
        """Async context manager entry."""
        connector = aiohttp.TCPConnector(limit=self.max_parallel * 2)
        timeout = aiohttp.ClientTimeout(total=3600)  # 1 hour timeout
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        # Serialize the API-Gateway calls that pass through the VAMS custom Lambda authorizer
        # (initialize_upload / complete_upload). A directory upload creates many sequences; firing
        # all their initialize/complete calls at once stampedes the authorizer, which 403s the burst.
        # Part uploads go straight to S3 presigned URLs (no authorizer) and stay fully parallel via
        # the separate max_parallel semaphore. Bounded low (2) to smooth the burst without serializing.
        self._api_semaphore = asyncio.Semaphore(2)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
    
    async def _initialize_sequence(self, sequence: UploadSequence, database_id: str, 
                                   asset_id: str, upload_type: str, progress: UploadProgress) -> SequenceInitResult:
        """Initialize a single sequence (Stage 1)."""
        # Prepare files for API
        api_files = []
        for file_info in sequence.files:
            parts = sequence.file_parts[file_info.relative_key]
            api_files.append({
                "relativeKey": file_info.relative_key,
                "file_size": file_info.size,
                "num_parts": len(parts)
            })
        
        # Initialize upload (run synchronous API call in executor). Bounded by the API semaphore so
        # concurrent sequence inits don't stampede the VAMS authorizer.
        try:
            loop = asyncio.get_event_loop()
            async with self._api_semaphore:
                init_response = await loop.run_in_executor(
                    None,
                    self.api_client.initialize_upload,
                    database_id, asset_id, upload_type, api_files
                )
        except Exception as e:
            raise FileUploadError(f"Failed to initialize upload for sequence {sequence.sequence_id}: {e}")
        
        upload_id = init_response["uploadId"]
        
        # Create part upload tasks
        part_uploads = []
        for file_response in init_response["files"]:
            file_key = file_response["relativeKey"]
            file_info = next(f for f in sequence.files if f.relative_key == file_key)
            parts = sequence.file_parts[file_key]
            
            for i, part_url_info in enumerate(file_response["partUploadUrls"]):
                part_info = parts[i]  # Parts are in order
                part_upload = PartUploadInfo(
                    file_info, part_info, part_url_info["UploadUrl"], sequence.sequence_id
                )
                part_uploads.append(part_upload)
        
        # Update initialized count and notify
        progress.initialized_sequences += 1
        if self.progress_callback:
            self.progress_callback(progress)
        
        return SequenceInitResult(
            sequence.sequence_id, upload_id, init_response, sequence, part_uploads
        )
    
    async def _complete_sequence(self, init_result: SequenceInitResult, database_id: str,
                                asset_id: str, upload_type: str, progress: UploadProgress) -> Dict[str, Any]:
        """Complete a sequence upload (Stage 3).
        
        This is called after all parts for this sequence have been uploaded.
        """
        sequence = init_result.sequence
        init_response = init_result.init_response
        part_uploads = init_result.part_uploads
        
        # Prepare completion data
        completion_files = []
        successful_files = set()
        failed_files = set()
        
        # Group parts by file
        file_parts = defaultdict(list)
        for part_upload in part_uploads:
            file_key = part_upload.file_info.relative_key
            file_parts[file_key].append(part_upload)
        
        # Check which files completed successfully
        for file_info in sequence.files:
            file_key = file_info.relative_key
            parts = file_parts.get(file_key, [])
            # What the LOCAL plan says this file needs. `parts` above is derived from the SERVER's
            # initialize_upload response, so the two disagreeing means the server handed back fewer
            # part upload URLs than were asked for, and the missing bytes were never sent.
            # `calculate_file_parts` returns 0 parts for a 0-byte file and >=1 for anything larger,
            # which is what makes this a sound discriminator rather than a size heuristic.
            planned_parts = sequence.file_parts.get(file_key) or []

            # Zero-byte files have no parts, so they're automatically successful. This is the ONLY
            # case an empty part list may be read as success: previously any file with no parts took
            # this branch, so a multi-megabyte file the server returned no part URLs for was added to
            # successful_files and completed with "parts": [] — reported stored, never uploaded, exit
            # code 0. That is the silent-loss shape measured as two absent objects on a 4,101-file
            # directory upload that returned rc=0.
            if len(planned_parts) == 0:
                successful_files.add(file_key)

                # Find the corresponding file response to get uploadIdS3
                file_response = next(f for f in init_response["files"] if f["relativeKey"] == file_key)

                completion_files.append({
                    "relativeKey": file_key,
                    "uploadIdS3": file_response["uploadIdS3"],
                    "parts": []  # Empty parts list for zero-byte files
                })
            elif len(parts) != len(planned_parts):
                # A SHORT part list is worse than a missing one: S3's CompleteMultipartUpload accepts
                # a subset of the initiated parts and produces a correspondingly TRUNCATED object, so
                # completing this would store a file that exists, has a plausible size, and is wrong.
                # Fail it instead, which surfaces in failed_files and makes the command exit non-zero.
                failed_files.add(file_key)
                log_warning(
                    f"Upload of {file_key} is incomplete and was NOT stored: the upload "
                    f"initialization returned {len(parts)} part upload URL(s) for a file needing "
                    f"{len(planned_parts)}"
                )
            else:
                all_parts_successful = all(part.status == "completed" for part in parts)

                if all_parts_successful:
                    successful_files.add(file_key)
                    
                    # Find the corresponding file response to get uploadIdS3
                    file_response = next(f for f in init_response["files"] if f["relativeKey"] == file_key)
                    
                    completion_files.append({
                        "relativeKey": file_key,
                        "uploadIdS3": file_response["uploadIdS3"],
                        "parts": [
                            {
                                "PartNumber": part.part_number,
                                "ETag": part.etag
                            }
                            for part in sorted(parts, key=lambda p: p.part_number)
                        ]
                    })
                else:
                    failed_files.add(file_key)
        
        # Complete upload if we have any successful files (run synchronous API call in executor).
        # Bounded by the API semaphore (authorizer-hitting call), like initialize.
        completion_result = None
        if completion_files:
            try:
                loop = asyncio.get_event_loop()
                async with self._api_semaphore:
                    completion_result = await loop.run_in_executor(
                        None,
                        self.api_client.complete_upload,
                        init_result.upload_id, database_id, asset_id, upload_type, completion_files
                    )
            except Exception as e:
                raise FileUploadError(f"Failed to complete upload for sequence {sequence.sequence_id}: {e}")

        # RECONCILE against the server's per-file verdict. Everything above decides success from
        # whether OUR S3 part uploads finished, which is only half the story: completion runs the
        # server-side validations, and a file it rejects is DELETED from the bucket. Without this the
        # command reported "Successful files: 2/2" for an upload where one file had
        # `success: false, error: "Error verifying base file for preview file"` and no object was left
        # behind — silent data loss with a success report, measured live on a `.previewFile.` companion.
        #
        # The `except` above only fires when the call itself fails; a 200 carrying per-file failures
        # passes through it untouched, which is why the check has to be on the RESPONSE.
        if completion_result:
            body = completion_result if isinstance(completion_result, dict) else {}
            # Unwrap the legacy envelope ONLY when `message` carries a nested body. On this endpoint it
            # does not: the completion response puts `fileResults`, `overallSuccess` and a HUMAN-READABLE
            # `message` string side by side at the top level. The idiom `body.get("message", body)` then
            # replaced the whole response with the string "No files were successfully uploaded", the
            # `isinstance(body, dict)` guard below turned `file_results` into None, and this entire
            # reconciliation became dead code — which is how a completion the server answered with
            # `success: False, error: "Base files does not exist for all preview files"` and
            # `overallSuccess: False` was still reported to the caller as a stored file with exit 0.
            # Measured live: 14 of 14 reported successful, 13 objects in S3.
            nested = body.get("message")
            if isinstance(nested, dict):
                body = nested

            file_results = body.get("fileResults")
            demoted = set()
            for entry in (file_results or []):
                if not isinstance(entry, dict):
                    continue
                key = entry.get("relativeKey")
                # Only demote on an EXPLICIT false. A result omitting `success` is not evidence of
                # failure, and treating a missing key as failure would fail every upload against a
                # deployment whose response predates this field.
                if key in successful_files and entry.get("success") is False:
                    successful_files.discard(key)
                    failed_files.add(key)
                    demoted.add(key)
                    log_warning(
                        f"Upload of {key} was rejected at completion and is NOT stored: "
                        f"{entry.get('error') or 'no reason given'}"
                    )

            # Backstop for the same class of defect one level up. `overallSuccess: False` is the
            # server saying plainly that this completion stored nothing, and it must not be possible
            # for the caller to see success anyway because the per-file list was missing, renamed, or
            # keyed differently than `successful_files` is. Only fires when the per-file pass demoted
            # NOTHING, so a partial rejection is still attributed to the exact files it names.
            if body.get("overallSuccess") is False and not demoted and successful_files:
                unstored = sorted(successful_files)
                failed_files.update(unstored)
                successful_files.clear()
                log_warning(
                    f"The server reported overallSuccess=False for this upload, so its "
                    f"{len(unstored)} file(s) are NOT stored"
                    + (f": {body.get('message')}" if isinstance(body.get('message'), str) else "")
                )

        # Update completed sequences count
        progress.completed_sequences += 1
        if self.progress_callback:
            self.progress_callback(progress)
        
        return {
            "sequence_id": sequence.sequence_id,
            "upload_id": init_result.upload_id,
            "successful_files": list(successful_files),
            "failed_files": list(failed_files),
            "completion_result": completion_result,
            "total_parts": len(part_uploads),
            "successful_parts": sum(1 for p in part_uploads if p.status == "completed"),
            "failed_parts": sum(1 for p in part_uploads if p.status == "failed")
        }
    
    async def _process_sequence(self, sequence: UploadSequence, database_id: str, asset_id: str,
                               upload_type: str, semaphore: asyncio.Semaphore, 
                               progress: UploadProgress) -> Dict[str, Any]:
        """Process a single sequence: initialize, upload parts, complete.
        
        This allows sequences to be processed independently - as soon as one is initialized,
        its parts can start uploading while other sequences are still initializing.
        """
        try:
            # Stage 1: Initialize this sequence
            init_result = await self._initialize_sequence(sequence, database_id, asset_id, upload_type, progress)
            
            # Stage 2: Upload all parts for this sequence
            upload_tasks = [
                self._upload_part_with_retry(part, semaphore, progress)
                for part in init_result.part_uploads
            ]
            
            # Wait for all parts of THIS sequence to complete
            await asyncio.gather(*upload_tasks, return_exceptions=True)
            
            # Update uploaded sequences count (all parts done)
            progress.uploaded_sequences += 1
            if self.progress_callback:
                self.progress_callback(progress)
            
            # Stage 3: Complete this sequence (completion API call)
            return await self._complete_sequence(init_result, database_id, asset_id, upload_type, progress)
            
        except Exception as e:
            return {
                "sequence_id": sequence.sequence_id,
                "error": str(e),
                "successful_files": [],
                "failed_files": [f.relative_key for f in sequence.files]
            }
    
    async def _upload_part_with_retry(self, part_upload: PartUploadInfo, 
                                    semaphore: asyncio.Semaphore, 
                                    progress: UploadProgress):
        """Upload a single part with retry logic."""
        async with semaphore:
            progress.active_uploads += 1
            
            try:
                for attempt in range(self.max_retries + 1):
                    part_upload.retry_count = attempt
                    part_upload.status = "uploading"
                    part_upload.upload_start_time = time.time()
                    
                    try:
                        await self._upload_single_part(part_upload)
                        part_upload.status = "completed"
                        part_upload.upload_end_time = time.time()
                        progress.update_part_progress(part_upload)
                        
                        if self.progress_callback:
                            self.progress_callback(progress)
                        
                        return
                        
                    except Exception as e:
                        part_upload.error = str(e)
                        
                        if attempt < self.max_retries:
                            # Wait before retry with exponential backoff
                            wait_time = min(2 ** attempt, 30)  # Max 30 seconds
                            await asyncio.sleep(wait_time)
                        else:
                            # Final attempt failed
                            if self.force_skip:
                                part_upload.status = "failed"
                                progress.update_part_progress(part_upload)
                                
                                if self.progress_callback:
                                    self.progress_callback(progress)
                                return
                            else:
                                # In a real CLI, this would prompt the user
                                # For now, we'll just fail the part
                                part_upload.status = "failed"
                                progress.update_part_progress(part_upload)
                                
                                if self.progress_callback:
                                    self.progress_callback(progress)
                                return
                                
            finally:
                progress.active_uploads -= 1
    
    async def _upload_single_part(self, part_upload: PartUploadInfo):
        """Upload a single part to S3."""
        file_path = part_upload.file_info.local_path
        start_byte = part_upload.start_byte
        end_byte = part_upload.end_byte
        
        # Read the part data
        async with aiofiles.open(file_path, 'rb') as f:
            await f.seek(start_byte)
            data = await f.read(end_byte - start_byte + 1)
        
        # Upload to S3
        async with self.session.put(part_upload.upload_url, data=data) as response:
            if response.status != 200:
                raise PartUploadError(
                    f"Part upload failed with status {response.status}: {await response.text()}"
                )
            
            # Extract ETag from response headers
            etag = response.headers.get('ETag')
            if not etag:
                raise PartUploadError("No ETag returned from S3")
            
            # Remove quotes from ETag if present
            part_upload.etag = etag.strip('"')
    
    async def upload_all_sequences(self, sequences: List[UploadSequence], database_id: str,
                                 asset_id: str, upload_type: str) -> Dict[str, Any]:
        """Upload all sequences using optimized parallel pipeline.
        
        This processes sequences independently:
        - Each sequence initializes, uploads, and completes independently
        - As soon as a sequence is initialized, its parts start uploading
        - All sequences share the same part upload pool (respecting max_parallel)
        - Preview sequences complete their API calls AFTER regular sequences
        - Progress updates in real-time as sequences initialize and complete
        """
        progress = UploadProgress(sequences)
        
        if self.progress_callback:
            self.progress_callback(progress)
        
        # Create a shared semaphore for all part uploads across all sequences
        semaphore = asyncio.Semaphore(self.max_parallel)
        
        # Separate regular and preview sequences
        regular_sequences = []
        preview_sequences = []
        
        for seq in sequences:
            # Check if sequence contains preview files
            has_preview_files = any(f.is_preview_file for f in seq.files)
            if has_preview_files:
                preview_sequences.append(seq)
            else:
                regular_sequences.append(seq)
        
        all_sequences_ordered = regular_sequences + preview_sequences

        # A `.previewFile.` companion is ALWAYS placed in its own sequence, separate from its base file
        # (see file_processor.create_upload_sequences), and the server's completion checks that the base
        # file either accompanies the companion in the same request or already exists in S3. Neither
        # holds unless the base file's sequence has COMPLETED first.
        #
        # Concatenating the two lists and handing them all to one `asyncio.gather` does not achieve
        # that: gather starts every coroutine immediately, so list order carries no ordering guarantee
        # and the previous comment ("preview sequences will wait for regular sequences") was
        # aspirational. Measured consequence on a live deployment: a companion's completion frequently
        # ran before its base file's and was refused with
        # "Error verifying base file for preview file", the object was deleted server-side, and between
        # 2 and 6 of 12 companions were lost per run — varying by run, because it was a race.
        #
        # So await the regular sequences to completion BEFORE starting the preview ones. Part uploads
        # within each group stay fully parallel; only the group boundary is a barrier, and companions
        # are small (MAX_PREVIEW_FILE_SIZE), so the added wall-clock is the tail of the regular group.
        async def _run(group):
            if not group:
                return []
            return await asyncio.gather(*[
                self._process_sequence(seq, database_id, asset_id, upload_type, semaphore, progress)
                for seq in group
            ], return_exceptions=True)

        try:
            sequence_results = list(await _run(regular_sequences))
            sequence_results += list(await _run(preview_sequences))
        except Exception as e:
            sequence_results = [{
                "sequence_id": seq.sequence_id,
                "error": str(e),
                "successful_files": [],
                "failed_files": [f.relative_key for f in seq.files]
            } for seq in all_sequences_ordered]

        # Handle any exceptions in results.
        # Indexed against all_sequences_ordered, NOT `sequences`: the two differ whenever any preview
        # sequence exists, so indexing the original list attributed an exception to the wrong sequence
        # and reported another sequence's files as the failed ones.
        final_results = []
        for i, result in enumerate(sequence_results):
            if isinstance(result, Exception):
                failed_sequence = all_sequences_ordered[i]
                final_results.append({
                    "sequence_id": failed_sequence.sequence_id,
                    "error": str(result),
                    "successful_files": [],
                    "failed_files": [f.relative_key for f in failed_sequence.files]
                })
            else:
                final_results.append(result)
        
        # Calculate final statistics
        overall_success = True
        total_successful_files = 0
        total_failed_files = 0
        
        for result in final_results:
            successful = len(result.get("successful_files", []))
            failed = len(result.get("failed_files", []))
            total_successful_files += successful
            total_failed_files += failed

            if failed > 0 or result.get("error"):
                overall_success = False

        # RECONCILE the per-sequence verdicts against what was STAGED. Everything above derives
        # success from the failure count, so a file that appears in NEITHER list is invisible: not
        # successful, not failed, overall_success True, exit code 0. That is the only shape in which
        # this command can report rc=0 while an object is absent from S3, and it is what a 4,101-file
        # directory upload did — two objects missing, no failure reported.
        #
        # This is a net, not a diagnosis: it fires whatever upstream degradation dropped the file, and
        # its job is to make the run LOUD. A caller can then retry, which a silent success denies them.
        staged_keys = {f.relative_key for seq in sequences for f in seq.files}
        accounted_keys = set()
        for result in final_results:
            accounted_keys.update(result.get("successful_files", []))
            accounted_keys.update(result.get("failed_files", []))
        unaccounted = sorted(staged_keys - accounted_keys)
        if unaccounted:
            overall_success = False
            total_failed_files += len(unaccounted)
            log_warning(
                f"{len(unaccounted)} staged file(s) were neither confirmed nor reported failed and "
                f"must be treated as NOT stored: {', '.join(unaccounted[:10])}"
                + (f" (and {len(unaccounted) - 10} more)" if len(unaccounted) > 10 else "")
            )
            final_results.append({
                "sequence_id": None,
                "error": "files unaccounted for by any sequence result",
                "successful_files": [],
                "failed_files": unaccounted,
            })

        return {
            "overall_success": overall_success,
            "total_files": progress.total_files,
            "successful_files": total_successful_files,
            "failed_files": total_failed_files,
            "total_size": progress.total_size,
            "total_size_formatted": format_file_size(progress.total_size),
            "upload_duration": progress.elapsed_time,
            "average_speed": progress.upload_speed,
            "average_speed_formatted": format_file_size(int(progress.upload_speed)) + "/s",
            "sequence_results": final_results,
            "progress": progress
        }


def format_duration(seconds: float) -> str:
    """Format duration in human readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"

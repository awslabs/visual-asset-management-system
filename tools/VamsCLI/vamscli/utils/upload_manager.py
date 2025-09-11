"""Upload manager for handling file uploads with progress monitoring."""

import asyncio
import aiohttp
import aiofiles
import time
from typing import List, Dict, Any, Optional, Callable
from pathlib import Path

from ..constants import DEFAULT_PARALLEL_UPLOADS, DEFAULT_RETRY_ATTEMPTS
from .exceptions import FileUploadError, PartUploadError
from .file_processor import UploadSequence, FileInfo, format_file_size
from .api_client import APIClient


class PartUploadInfo:
    """Information about a part upload."""
    
    def __init__(self, file_info: FileInfo, part_info: Dict[str, Any], upload_url: str):
        self.file_info = file_info
        self.part_info = part_info
        self.upload_url = upload_url
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
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
    
    async def upload_sequence(self, sequence: UploadSequence, database_id: str, asset_id: str,
                            upload_type: str, progress: UploadProgress) -> Dict[str, Any]:
        """Upload a single sequence of files."""
        # Prepare files for API
        api_files = []
        for file_info in sequence.files:
            parts = sequence.file_parts[file_info.relative_key]
            api_files.append({
                "relativeKey": file_info.relative_key,
                "file_size": file_info.size,
                "num_parts": len(parts)
            })
        
        # Initialize upload
        try:
            init_response = self.api_client.initialize_upload(
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
                part_upload = PartUploadInfo(file_info, part_info, part_url_info["UploadUrl"])
                part_uploads.append(part_upload)
        
        # Upload parts with concurrency control
        semaphore = asyncio.Semaphore(self.max_parallel)
        tasks = [self._upload_part_with_retry(part_upload, semaphore, progress) 
                for part_upload in part_uploads]
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Prepare completion data
        completion_files = []
        successful_files = set()
        failed_files = set()
        
        # Group parts by file
        file_parts = {}
        for part_upload in part_uploads:
            file_key = part_upload.file_info.relative_key
            if file_key not in file_parts:
                file_parts[file_key] = []
            file_parts[file_key].append(part_upload)
        
        # Check which files completed successfully
        for file_key, parts in file_parts.items():
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
        
        # Complete upload if we have any successful files
        completion_result = None
        if completion_files:
            try:
                completion_result = self.api_client.complete_upload(
                    upload_id, database_id, asset_id, upload_type, completion_files
                )
            except Exception as e:
                raise FileUploadError(f"Failed to complete upload for sequence {sequence.sequence_id}: {e}")
        
        return {
            "sequence_id": sequence.sequence_id,
            "upload_id": upload_id,
            "successful_files": list(successful_files),
            "failed_files": list(failed_files),
            "completion_result": completion_result,
            "total_parts": len(part_uploads),
            "successful_parts": sum(1 for p in part_uploads if p.status == "completed"),
            "failed_parts": sum(1 for p in part_uploads if p.status == "failed")
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
        """Upload all sequences and return comprehensive results."""
        progress = UploadProgress(sequences)
        
        if self.progress_callback:
            self.progress_callback(progress)
        
        sequence_results = []
        overall_success = True
        
        for sequence in sequences:
            try:
                result = await self.upload_sequence(sequence, database_id, asset_id, upload_type, progress)
                sequence_results.append(result)
                
                if result["failed_files"]:
                    overall_success = False
                    
            except Exception as e:
                sequence_results.append({
                    "sequence_id": sequence.sequence_id,
                    "error": str(e),
                    "successful_files": [],
                    "failed_files": [f.relative_key for f in sequence.files]
                })
                overall_success = False
        
        # Calculate final statistics
        total_successful_files = sum(len(r.get("successful_files", [])) for r in sequence_results)
        total_failed_files = sum(len(r.get("failed_files", [])) for r in sequence_results)
        
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
            "sequence_results": sequence_results,
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

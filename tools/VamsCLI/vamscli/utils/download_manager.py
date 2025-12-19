"""Download manager for handling file downloads with progress monitoring."""

import asyncio
import aiohttp
import aiofiles
import time
import os
from typing import List, Dict, Any, Optional, Callable, NamedTuple
from pathlib import Path

from ..constants import DEFAULT_PARALLEL_DOWNLOADS, DEFAULT_DOWNLOAD_RETRY_ATTEMPTS, DEFAULT_DOWNLOAD_TIMEOUT
from .exceptions import FileDownloadError, DownloadError
from .api_client import APIClient


class DownloadFileInfo(NamedTuple):
    """Information about a file to download."""
    relative_key: str
    local_path: Path
    download_url: str
    file_size: Optional[int] = None
    version_id: Optional[str] = None


class DownloadProgress:
    """Track download progress across all files."""
    
    def __init__(self, files: List[DownloadFileInfo]):
        self.files = files
        self.total_files = len(files)
        self.total_size = sum(f.file_size or 0 for f in files)
        
        # Progress tracking
        self.completed_files = 0
        self.completed_size = 0
        self.failed_files = 0
        self.active_downloads = 0
        self.start_time = time.time()
        
        # File-level progress
        self.file_progress = {}  # relative_key -> {"completed_size": int, "total_size": int, "status": str}
        
        # Initialize file progress
        for file_info in files:
            self.file_progress[file_info.relative_key] = {
                "completed_size": 0,
                "total_size": file_info.file_size or 0,
                "status": "pending"  # pending, downloading, completed, failed
            }
    
    def update_file_progress(self, relative_key: str, completed_size: int, status: str):
        """Update progress for a file."""
        if relative_key in self.file_progress:
            old_completed = self.file_progress[relative_key]["completed_size"]
            self.file_progress[relative_key]["completed_size"] = completed_size
            self.file_progress[relative_key]["status"] = status
            
            # Update overall progress
            size_diff = completed_size - old_completed
            self.completed_size += size_diff
            
            if status == "completed":
                self.completed_files += 1
            elif status == "failed":
                self.failed_files += 1
    
    @property
    def overall_progress(self) -> float:
        """Get overall progress as percentage."""
        if self.total_size == 0:
            return 100.0 if self.completed_files == self.total_files else 0.0
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
    def download_speed(self) -> float:
        """Get current download speed in bytes per second."""
        elapsed = self.elapsed_time
        if elapsed > 0:
            return self.completed_size / elapsed
        return 0.0


class DownloadManager:
    """Manages file downloads with progress monitoring and retry logic."""
    
    def __init__(self, api_client: APIClient, max_parallel: int = DEFAULT_PARALLEL_DOWNLOADS,
                 max_retries: int = DEFAULT_DOWNLOAD_RETRY_ATTEMPTS, 
                 timeout: int = DEFAULT_DOWNLOAD_TIMEOUT,
                 progress_callback: Optional[Callable[[DownloadProgress], None]] = None):
        self.api_client = api_client
        self.max_parallel = max_parallel
        self.max_retries = max_retries
        self.timeout = timeout
        self.progress_callback = progress_callback
        self.session = None
        
    async def __aenter__(self):
        """Async context manager entry."""
        connector = aiohttp.TCPConnector(limit=self.max_parallel * 2)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
    
    async def download_files(self, files: List[DownloadFileInfo]) -> Dict[str, Any]:
        """Download all files and return comprehensive results."""
        progress = DownloadProgress(files)
        
        if self.progress_callback:
            self.progress_callback(progress)
        
        # Create download tasks with concurrency control
        semaphore = asyncio.Semaphore(self.max_parallel)
        tasks = [self._download_file_with_retry(file_info, semaphore, progress) 
                for file_info in files]
        
        # Execute downloads
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        successful_files = []
        failed_files = []
        
        for i, result in enumerate(results):
            file_info = files[i]
            if isinstance(result, Exception):
                failed_files.append({
                    "relative_key": file_info.relative_key,
                    "local_path": str(file_info.local_path),
                    "error": str(result)
                })
            elif result.get("success"):
                successful_files.append({
                    "relative_key": file_info.relative_key,
                    "local_path": str(file_info.local_path),
                    "size": result.get("size", 0)
                })
            else:
                failed_files.append({
                    "relative_key": file_info.relative_key,
                    "local_path": str(file_info.local_path),
                    "error": result.get("error", "Unknown error")
                })
        
        return {
            "overall_success": len(failed_files) == 0,
            "total_files": len(files),
            "successful_files": len(successful_files),
            "failed_files": len(failed_files),
            "total_size": progress.total_size,
            "total_size_formatted": format_file_size(progress.total_size),
            "download_duration": progress.elapsed_time,
            "average_speed": progress.download_speed,
            "average_speed_formatted": format_file_size(int(progress.download_speed)) + "/s",
            "successful_downloads": successful_files,
            "failed_downloads": failed_files
        }
    
    async def _download_file_with_retry(self, file_info: DownloadFileInfo, 
                                      semaphore: asyncio.Semaphore, 
                                      progress: DownloadProgress) -> Dict[str, Any]:
        """Download a single file with retry logic."""
        async with semaphore:
            progress.active_downloads += 1
            
            try:
                for attempt in range(self.max_retries + 1):
                    try:
                        progress.update_file_progress(file_info.relative_key, 0, "downloading")
                        
                        if self.progress_callback:
                            self.progress_callback(progress)
                        
                        result = await self._download_single_file(file_info, progress)
                        
                        progress.update_file_progress(
                            file_info.relative_key, 
                            result.get("size", 0), 
                            "completed"
                        )
                        
                        if self.progress_callback:
                            self.progress_callback(progress)
                        
                        return {"success": True, "size": result.get("size", 0)}
                        
                    except Exception as e:
                        if attempt < self.max_retries:
                            # Wait before retry with exponential backoff
                            wait_time = min(2 ** attempt, 30)  # Max 30 seconds
                            await asyncio.sleep(wait_time)
                        else:
                            # Final attempt failed
                            progress.update_file_progress(file_info.relative_key, 0, "failed")
                            
                            if self.progress_callback:
                                self.progress_callback(progress)
                            
                            return {"success": False, "error": str(e)}
                            
            finally:
                progress.active_downloads -= 1
    
    async def _download_single_file(self, file_info: DownloadFileInfo, progress: DownloadProgress) -> Dict[str, Any]:
        """Download a single file from S3."""
        # Ensure local directory exists
        file_info.local_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Download file
        async with self.session.get(file_info.download_url) as response:
            if response.status != 200:
                raise DownloadError(
                    f"Download failed with status {response.status}: {await response.text()}"
                )
            
            # Get file size from headers if not provided
            content_length = response.headers.get('Content-Length')
            file_size = int(content_length) if content_length else file_info.file_size or 0
            
            # Write file with progress updates
            downloaded_size = 0
            async with aiofiles.open(file_info.local_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(8192):  # 8KB chunks
                    await f.write(chunk)
                    downloaded_size += len(chunk)
                    
                    # Update progress periodically
                    progress.update_file_progress(file_info.relative_key, downloaded_size, "downloading")
                    
                    if self.progress_callback:
                        self.progress_callback(progress)
        
        return {"size": downloaded_size}


def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    
    while size >= 1024.0 and i < len(size_names) - 1:
        size /= 1024.0
        i += 1
    
    return f"{size:.1f} {size_names[i]}"


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


class FileTreeBuilder:
    """Build file tree structures from API responses."""
    
    @staticmethod
    def build_file_tree(files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build a hierarchical tree structure from flat file list."""
        tree = {}
        
        for file_item in files:
            relative_path = file_item.get('relativePath', '')
            if not relative_path:
                continue
                
            # Split path into components
            path_parts = relative_path.strip('/').split('/')
            current_level = tree
            
            # Navigate/create tree structure
            for i, part in enumerate(path_parts):
                if part not in current_level:
                    current_level[part] = {
                        'is_folder': i < len(path_parts) - 1 or file_item.get('isFolder', False),
                        'file_info': file_item if i == len(path_parts) - 1 else None,
                        'children': {}
                    }
                current_level = current_level[part]['children']
        
        return tree
    
    @staticmethod
    def get_files_under_prefix(files: List[Dict[str, Any]], prefix: str, recursive: bool = False) -> List[Dict[str, Any]]:
        """Get all files under a specific prefix, excluding folder objects."""
        if not prefix.endswith('/'):
            prefix += '/'
        
        matching_files = []
        for file_item in files:
            # Skip folder objects - only download actual files
            if file_item.get('isFolder'):
                continue
                
            relative_path = file_item.get('relativePath', '')
            
            if relative_path.startswith(prefix):
                if recursive:
                    # Include all files under the prefix
                    matching_files.append(file_item)
                else:
                    # Only include direct children (no additional slashes after prefix)
                    remaining_path = relative_path[len(prefix):]
                    if '/' not in remaining_path or remaining_path.endswith('/'):
                        matching_files.append(file_item)
        
        return matching_files
    
    @staticmethod
    def flatten_file_list(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Flatten file list and detect conflicts."""
        flattened = {}
        conflicts = []
        
        for file_item in files:
            if file_item.get('isFolder'):
                continue  # Skip folders in flattened view
                
            relative_path = file_item.get('relativePath', '')
            filename = Path(relative_path).name
            
            if filename in flattened:
                conflicts.append(filename)
            else:
                flattened[filename] = file_item
        
        if conflicts:
            raise FileDownloadError(f"Filename conflicts detected in flattened download: {', '.join(conflicts)}")
        
        return list(flattened.values())


class AssetTreeTraverser:
    """Traverse asset link trees for multi-asset downloads."""
    
    def __init__(self, api_client: APIClient):
        self.api_client = api_client
    
    async def traverse_asset_tree(self, database_id: str, asset_id: str, max_depth: int) -> List[Dict[str, Any]]:
        """Traverse asset link children tree to specified depth."""
        visited_assets = set()
        assets_to_download = []
        
        await self._traverse_recursive(database_id, asset_id, 0, max_depth, visited_assets, assets_to_download)
        
        return assets_to_download
    
    async def _traverse_recursive(self, database_id: str, asset_id: str, current_depth: int, 
                                max_depth: int, visited_assets: set, assets_to_download: List[Dict[str, Any]]):
        """Recursively traverse asset tree."""
        # Avoid infinite loops
        asset_key = f"{database_id}:{asset_id}"
        if asset_key in visited_assets:
            return
        
        visited_assets.add(asset_key)
        
        # Add current asset to download list
        assets_to_download.append({
            "databaseId": database_id,
            "assetId": asset_id,
            "depth": current_depth
        })
        
        # If we haven't reached max depth, get children
        if current_depth < max_depth:
            try:
                links_response = self.api_client.get_asset_links_for_asset(database_id, asset_id, child_tree_view=True)
                children = links_response.get('children', [])
                
                # Traverse each child
                for child in children:
                    child_database_id = child.get('databaseId')
                    child_asset_id = child.get('assetId')
                    
                    if child_database_id and child_asset_id:
                        await self._traverse_recursive(
                            child_database_id, child_asset_id, current_depth + 1,
                            max_depth, visited_assets, assets_to_download
                        )
                        
                        # Also traverse nested children
                        await self._traverse_children_recursive(
                            child.get('children', []), current_depth + 1,
                            max_depth, visited_assets, assets_to_download
                        )
                        
            except Exception as e:
                # Log error but continue with other assets
                pass
    
    async def _traverse_children_recursive(self, children: List[Dict[str, Any]], current_depth: int,
                                         max_depth: int, visited_assets: set, assets_to_download: List[Dict[str, Any]]):
        """Recursively traverse nested children from tree view."""
        for child in children:
            child_database_id = child.get('databaseId')
            child_asset_id = child.get('assetId')
            
            if child_database_id and child_asset_id:
                await self._traverse_recursive(
                    child_database_id, child_asset_id, current_depth,
                    max_depth, visited_assets, assets_to_download
                )
                
                # Traverse nested children
                await self._traverse_children_recursive(
                    child.get('children', []), current_depth + 1,
                    max_depth, visited_assets, assets_to_download
                )

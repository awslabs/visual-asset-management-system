"""File processing utilities for upload operations."""

import os
import math
from typing import List, Dict, Any, Tuple
from pathlib import Path

from ..constants import (
    DEFAULT_CHUNK_SIZE_SMALL, DEFAULT_CHUNK_SIZE_LARGE, MAX_FILE_SIZE_SMALL_CHUNKS,
    MAX_SEQUENCE_SIZE, MAX_PREVIEW_FILE_SIZE, ALLOWED_PREVIEW_EXTENSIONS,
    MAX_FILES_PER_REQUEST, MAX_TOTAL_PARTS_PER_REQUEST, MAX_PARTS_PER_FILE, MAX_PART_SIZE
)
from .exceptions import (
    InvalidFileError, FileTooLargeError, PreviewFileError, UploadSequenceError
)


class FileInfo:
    """Information about a file to be uploaded."""
    
    def __init__(self, local_path: str, relative_key: str, size: int = None):
        self.local_path = Path(local_path)
        self.relative_key = relative_key
        if size is not None:
            self.size = size
        else:
            self.size = self.local_path.stat().st_size
        self.is_preview_file = '.previewFile.' in relative_key
        
    def __repr__(self):
        return f"FileInfo(local_path='{self.local_path}', relative_key='{self.relative_key}', size={self.size})"


class UploadSequence:
    """A sequence of files to be uploaded together."""
    
    def __init__(self, files: List[FileInfo], sequence_id: int):
        self.files = files
        self.sequence_id = sequence_id
        self.total_size = sum(f.size for f in files)
        self.total_parts = 0
        self.file_parts = {}  # file_path -> list of part info
        
    def calculate_parts(self):
        """Calculate parts for all files in this sequence."""
        self.total_parts = 0
        self.file_parts = {}
        
        for file_info in self.files:
            parts = calculate_file_parts(file_info.size)
            self.file_parts[file_info.relative_key] = parts
            self.total_parts += len(parts)
    
    def __repr__(self):
        return f"UploadSequence(id={self.sequence_id}, files={len(self.files)}, size={self.total_size}, parts={self.total_parts})"


def calculate_file_parts(file_size: int) -> List[Dict[str, Any]]:
    """Calculate parts for a single file based on size."""
    if file_size == 0:
        # Zero-byte files have no parts - backend expects num_parts: 0
        return []
    
    # Determine chunk size based on file size
    if file_size > MAX_FILE_SIZE_SMALL_CHUNKS:
        chunk_size = DEFAULT_CHUNK_SIZE_LARGE  # 1GB chunks for very large files
    else:
        chunk_size = DEFAULT_CHUNK_SIZE_SMALL  # 150MB chunks for smaller files
    
    parts = []
    part_number = 1
    start_byte = 0
    
    while start_byte < file_size:
        end_byte = min(start_byte + chunk_size - 1, file_size - 1)
        part_size = end_byte - start_byte + 1
        
        parts.append({
            "part_number": part_number,
            "start_byte": start_byte,
            "end_byte": end_byte,
            "size": part_size
        })
        
        start_byte = end_byte + 1
        part_number += 1
    
    return parts


def validate_file_for_upload(file_path: Path, upload_type: str, relative_key: str = None) -> None:
    """Validate a file for upload."""
    if not file_path.exists():
        raise InvalidFileError(f"File not found: {file_path}")
    
    if not file_path.is_file():
        raise InvalidFileError(f"Path is not a file: {file_path}")
    
    file_size = file_path.stat().st_size
    
    # Check if it's a preview file
    is_preview_file = relative_key and '.previewFile.' in relative_key
    
    # Validate preview files
    if upload_type == "assetPreview" or is_preview_file:
        if file_size > MAX_PREVIEW_FILE_SIZE:
            raise FileTooLargeError(
                f"Preview file {file_path.name} exceeds maximum size of 5MB "
                f"(actual size: {file_size / (1024*1024):.1f}MB)"
            )
        
        # Check file extension
        file_extension = file_path.suffix.lower()
        if file_extension not in ALLOWED_PREVIEW_EXTENSIONS:
            raise PreviewFileError(
                f"Preview file {file_path.name} has unsupported extension '{file_extension}'. "
                f"Allowed extensions: {', '.join(ALLOWED_PREVIEW_EXTENSIONS)}"
            )


def validate_file_extensions(files: List[FileInfo], allowed_extensions: str, upload_type: str = "assetFile") -> None:
    """
    Validate files against allowed extensions from database configuration.
    
    Args:
        files: List of FileInfo objects to validate
        allowed_extensions: Comma-delimited string of allowed extensions (e.g., ".glb,.gltf")
        upload_type: Type of upload ("assetFile" or "assetPreview")
    
    Raises:
        InvalidFileError: When files have disallowed extensions
    """
    # Skip validation entirely for asset preview uploads
    if upload_type == "assetPreview":
        return
    
    # Parse allowed extensions
    if not allowed_extensions or not allowed_extensions.strip():
        return  # No restrictions
    
    extensions_list = [ext.strip().lower() for ext in allowed_extensions.split(',')]
    
    # Check for ".all" wildcard
    if '.all' in extensions_list:
        return  # All extensions allowed
    
    # Collect files that don't meet restrictions
    invalid_files = []
    
    for file_info in files:
        # Skip preview files (asset preview uploads)
        if file_info.is_preview_file:
            continue
        
        # Skip .previewFile. auxiliary files
        if '.previewFile.' in file_info.relative_key:
            continue
        
        # Get file extension
        file_ext = Path(file_info.local_path).suffix.lower()
        
        # Check if extension is allowed
        if file_ext not in extensions_list:
            invalid_files.append({
                'file': file_info.relative_key,
                'extension': file_ext
            })
    
    # Raise error if any files are invalid
    if invalid_files:
        # Format the error message
        error_lines = [
            f"Database has file extension restrictions: {allowed_extensions}",
            "",
            "The following files do not meet the restriction:"
        ]
        
        for invalid in invalid_files:
            error_lines.append(f"  - {invalid['file']} (extension: {invalid['extension']})")
        
        raise InvalidFileError("\n".join(error_lines))


def collect_files_from_directory(directory: Path, recursive: bool = False, 
                                asset_location: str = "/") -> List[FileInfo]:
    """Collect files from a directory."""
    files = []
    
    if not directory.exists():
        raise InvalidFileError(f"Directory not found: {directory}")
    
    if not directory.is_dir():
        raise InvalidFileError(f"Path is not a directory: {directory}")
    
    # Normalize asset location - ensure it starts with / and ends with /
    if not asset_location.startswith('/'):
        asset_location = '/' + asset_location
    if not asset_location.endswith('/'):
        asset_location += '/'
    
    # Collect files
    pattern = "**/*" if recursive else "*"
    for file_path in directory.glob(pattern):
        if file_path.is_file():
            # Calculate relative path from directory
            relative_path = file_path.relative_to(directory)
            # Convert to forward slashes and combine with asset location
            relative_key = asset_location + str(relative_path).replace('\\', '/')
            
            file_info = FileInfo(
                local_path=str(file_path),
                relative_key=relative_key
            )
            files.append(file_info)
    
    if not files:
        raise InvalidFileError(f"No files found in directory: {directory}")
    
    return files


def collect_files_from_list(file_paths: List[str], asset_location: str = "/") -> List[FileInfo]:
    """Collect files from a list of file paths."""
    files = []
    
    # Normalize asset location - ensure it starts with / and ends with /
    if not asset_location.startswith('/'):
        asset_location = '/' + asset_location
    if not asset_location.endswith('/'):
        asset_location += '/'
    
    # Check for duplicate filenames
    filenames = []
    for file_path_str in file_paths:
        file_path = Path(file_path_str)
        filename = file_path.name
        
        if filename in filenames:
            raise InvalidFileError(
                f"Duplicate filename '{filename}' found. When uploading multiple files, "
                "each file must have a unique name."
            )
        filenames.append(filename)
        
        # Create relative key using just the filename
        relative_key = asset_location + filename
        
        file_info = FileInfo(
            local_path=str(file_path),
            relative_key=relative_key
        )
        files.append(file_info)
    
    return files


def validate_individual_file_constraints(file_info: FileInfo) -> None:
    """Validate individual file against backend constraints.
    
    This validates constraints that apply to individual files, not sequences.
    Called during sequence creation to ensure each file can be uploaded.
    """
    file_parts = calculate_file_parts(file_info.size)
    num_parts = len(file_parts)
    
    # Check individual file part limit
    if num_parts > MAX_PARTS_PER_FILE:
        raise UploadSequenceError(
            f"File '{file_info.relative_key}' requires {num_parts} parts, "
            f"but maximum is {MAX_PARTS_PER_FILE} parts per file. "
            f"File size: {format_file_size(file_info.size)}"
        )


def validate_sequence_constraints(sequence: UploadSequence) -> None:
    """Validate a single upload sequence against backend constraints.
    
    This validates constraints that apply per API request/sequence:
    - Maximum files per request
    - Maximum total parts per request
    """
    # Check file count limit per sequence
    if len(sequence.files) > MAX_FILES_PER_REQUEST:
        raise UploadSequenceError(
            f"Sequence {sequence.sequence_id} has {len(sequence.files)} files, "
            f"but maximum is {MAX_FILES_PER_REQUEST} files per request."
        )
    
    # Check total parts limit per sequence
    if sequence.total_parts > MAX_TOTAL_PARTS_PER_REQUEST:
        raise UploadSequenceError(
            f"Sequence {sequence.sequence_id} has {sequence.total_parts} parts, "
            f"but maximum is {MAX_TOTAL_PARTS_PER_REQUEST} parts per request."
        )


def create_upload_sequences(files: List[FileInfo]) -> List[UploadSequence]:
    """Create upload sequences from a list of files with backend constraint validation.
    
    This function automatically creates multiple sequences when needed to respect
    backend constraints. Constraints are enforced per-sequence, not for the entire
    upload, allowing unlimited files to be uploaded in batches.
    
    For example, with MAX_FILES_PER_REQUEST=50:
    - 200 files will create 4 sequences (50 files each)
    - Each sequence is uploaded as a separate API request
    - All files are handled automatically without manual splitting
    """
    if not files:
        raise UploadSequenceError("No files provided for sequencing")
    
    # Validate individual file constraints (applies to all files regardless of sequencing)
    for file_info in files:
        validate_individual_file_constraints(file_info)
    
    sequences = []
    regular_files = []
    preview_files = []
    
    # Separate preview files from regular files
    for file_info in files:
        if file_info.is_preview_file:
            preview_files.append(file_info)
        else:
            regular_files.append(file_info)
    
    # Process regular files first
    sequence_id = 1
    current_sequence = []
    current_size = 0
    current_parts = 0
    
    for file_info in regular_files:
        file_parts = calculate_file_parts(file_info.size)
        file_part_count = len(file_parts)
        
        # Check if adding this file would exceed limits
        would_exceed_size = current_size + file_info.size > MAX_SEQUENCE_SIZE
        would_exceed_files = len(current_sequence) + 1 > MAX_FILES_PER_REQUEST
        would_exceed_parts = current_parts + file_part_count > MAX_TOTAL_PARTS_PER_REQUEST
        
        # If file is >= 3GB or adding it would exceed any limit, start new sequence
        if (file_info.size >= MAX_SEQUENCE_SIZE or would_exceed_size or 
            would_exceed_files or would_exceed_parts):
            
            # Save current sequence if it has files
            if current_sequence:
                sequence = UploadSequence(current_sequence, sequence_id)
                sequence.calculate_parts()
                validate_sequence_constraints(sequence)
                sequences.append(sequence)
                sequence_id += 1
                current_sequence = []
                current_size = 0
                current_parts = 0
            
            # Large file gets its own sequence
            sequence = UploadSequence([file_info], sequence_id)
            sequence.calculate_parts()
            validate_sequence_constraints(sequence)
            sequences.append(sequence)
            sequence_id += 1
        else:
            # Add to current sequence
            current_sequence.append(file_info)
            current_size += file_info.size
            current_parts += file_part_count
    
    # Add remaining regular files
    if current_sequence:
        sequence = UploadSequence(current_sequence, sequence_id)
        sequence.calculate_parts()
        validate_sequence_constraints(sequence)
        sequences.append(sequence)
        sequence_id += 1
    
    # Process preview files (group them following same rules)
    if preview_files:
        current_sequence = []
        current_size = 0
        current_parts = 0
        
        for file_info in preview_files:
            file_parts = calculate_file_parts(file_info.size)
            file_part_count = len(file_parts)
            
            # Check if adding this file would exceed limits
            would_exceed_size = current_size + file_info.size > MAX_SEQUENCE_SIZE
            would_exceed_files = len(current_sequence) + 1 > MAX_FILES_PER_REQUEST
            would_exceed_parts = current_parts + file_part_count > MAX_TOTAL_PARTS_PER_REQUEST
            
            if would_exceed_size or would_exceed_files or would_exceed_parts:
                if current_sequence:
                    sequence = UploadSequence(current_sequence, sequence_id)
                    sequence.calculate_parts()
                    validate_sequence_constraints(sequence)
                    sequences.append(sequence)
                    sequence_id += 1
                    current_sequence = []
                    current_size = 0
                    current_parts = 0
            
            current_sequence.append(file_info)
            current_size += file_info.size
            current_parts += file_part_count
        
        # Add remaining preview files
        if current_sequence:
            sequence = UploadSequence(current_sequence, sequence_id)
            sequence.calculate_parts()
            validate_sequence_constraints(sequence)
            sequences.append(sequence)
    
    if not sequences:
        raise UploadSequenceError("Failed to create upload sequences")
    
    return sequences


def validate_preview_files_have_base_files(files: List[FileInfo]) -> Tuple[bool, List[str]]:
    """Validate that all preview files have corresponding base files in the upload."""
    preview_files = [f for f in files if f.is_preview_file]
    if not preview_files:
        return True, []
    
    # Get all base file paths
    base_files = set()
    for file_info in files:
        if not file_info.is_preview_file:
            base_files.add(file_info.relative_key)
    
    # Check each preview file
    missing_base_files = []
    for preview_file in preview_files:
        # Extract base file path from preview file
        base_file_path = preview_file.relative_key.split('.previewFile.')[0]
        
        if base_file_path not in base_files:
            missing_base_files.append(preview_file.relative_key)
    
    return len(missing_base_files) == 0, missing_base_files


def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes == 0:
        return "0B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    
    return f"{s}{size_names[i]}"


def get_upload_summary(sequences: List[UploadSequence]) -> Dict[str, Any]:
    """Get a summary of the upload sequences."""
    total_files = sum(len(seq.files) for seq in sequences)
    total_size = sum(seq.total_size for seq in sequences)
    total_parts = sum(seq.total_parts for seq in sequences)
    
    preview_files = []
    regular_files = []
    
    for sequence in sequences:
        for file_info in sequence.files:
            if file_info.is_preview_file:
                preview_files.append(file_info)
            else:
                regular_files.append(file_info)
    
    return {
        "total_files": total_files,
        "total_size": total_size,
        "total_size_formatted": format_file_size(total_size),
        "total_parts": total_parts,
        "total_sequences": len(sequences),
        "regular_files": len(regular_files),
        "preview_files": len(preview_files),
        "sequences": sequences
    }
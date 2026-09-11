"""Scan dataset discovery - associates point clouds with imagery."""

from pathlib import Path

import numpy as np

from coord_xform.models import CameraExtrinsics, ScanDataset

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif"}


def discover_scan_dataset(scan_path: Path) -> ScanDataset:
    """Discover a scan dataset from a directory or single file.

    If scan_path is a directory, looks for point cloud files and
    associated imagery. If it's a file, looks for images in the
    same directory or sibling directories.
    """
    if scan_path.is_dir():
        return _discover_from_directory(scan_path)
    return _discover_from_file(scan_path)


def _discover_from_directory(directory: Path) -> ScanDataset:
    """Discover scan dataset from a directory containing PLY + images."""
    point_cloud_path = None
    image_paths: list[Path] = []

    # Find point cloud (prefer world_colored, then sensor_lidar, then any PLY)
    for pattern in ["world_colored.ply", "sensor_lidar*.ply", "*.ply"]:
        matches = list(directory.glob(pattern))
        if matches:
            point_cloud_path = matches[0]
            break

    # Also check for E57/LAS
    if point_cloud_path is None:
        for ext in [".e57", ".las", ".laz"]:
            matches = list(directory.glob(f"*{ext}"))
            if matches:
                point_cloud_path = matches[0]
                break

    if point_cloud_path is None:
        raise FileNotFoundError(
            f"No point cloud file found in {directory}"
        )

    # Find images
    for path in directory.iterdir():
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            if "_masked" not in path.stem:
                image_paths.append(path)

    # Camera at scan origin (typical for static scanner setups)
    camera = CameraExtrinsics(
        position=np.array([0.0, 0.0, 0.0], dtype=np.float64),
        image_path=image_paths[0] if image_paths else None,
        scan_index=0,
    )

    return ScanDataset(
        point_cloud_path=point_cloud_path,
        image_paths=image_paths,
        camera=camera,
    )


def _discover_from_file(file_path: Path) -> ScanDataset:
    """Discover scan dataset from a single point cloud file."""
    directory = file_path.parent
    image_paths: list[Path] = []

    for path in directory.iterdir():
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            if "_masked" not in path.stem:
                image_paths.append(path)

    camera = None
    if image_paths:
        camera = CameraExtrinsics(
            position=np.array([0.0, 0.0, 0.0], dtype=np.float64),
            image_path=image_paths[0],
            scan_index=0,
        )

    return ScanDataset(
        point_cloud_path=file_path,
        image_paths=image_paths,
        camera=camera,
    )


def discover_multi_scan(base_path: Path) -> list[ScanDataset]:
    """Discover multiple scan datasets from a parent directory.

    Looks for subdirectories named scan_* or fusion_scan_* and
    discovers each as an independent scan dataset.
    """
    datasets: list[ScanDataset] = []

    scan_dirs = sorted(
        d
        for d in base_path.iterdir()
        if d.is_dir()
        and (d.name.startswith("scan_") or d.name.startswith("fusion_scan_"))
    )

    for i, scan_dir in enumerate(scan_dirs):
        dataset = discover_scan_dataset(scan_dir)
        if dataset.camera:
            dataset.camera.scan_index = i
        datasets.append(dataset)

    return datasets

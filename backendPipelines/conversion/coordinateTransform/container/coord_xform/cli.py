"""Command-line interface for the coordinate transformation pipeline."""

from pathlib import Path
from typing import Annotated

import typer

from coord_xform.config import PipelineConfig

app = typer.Typer(
    name="coord-xform",
    help="Coordinate transformation pipeline for LiDAR point clouds and imagery.",
)


@app.command()
def transform(
    inputs: Annotated[
        list[Path],
        typer.Argument(help="Input E57 or LAS/LAZ files to transform"),
    ],
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to pipeline config YAML"),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Validate CRS and report planned transformation without executing",
        ),
    ] = False,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Override output directory"),
    ] = None,
) -> None:
    """Transform point cloud files between coordinate reference systems."""
    pipeline_config = PipelineConfig.from_yaml(config)

    if output_dir:
        pipeline_config.output.directory = output_dir

    if dry_run:
        typer.echo(f"[DRY RUN] Config loaded: {pipeline_config.name}")
        typer.echo(f"  Source CRS: {pipeline_config.source.crs}")
        typer.echo(f"  Target CRS: {pipeline_config.target.crs}")
        typer.echo(f"  Scale correction: {pipeline_config.transform.apply_scale_correction}")
        typer.echo(f"  Input files: {len(inputs)}")
        for input_path in inputs:
            typer.echo(f"    - {input_path}")
        return

    from coord_xform.pipeline import run_pipeline

    run_pipeline(pipeline_config, inputs)


@app.command()
def validate(
    inputs: Annotated[
        list[Path],
        typer.Argument(help="Input files to validate CRS for"),
    ],
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to pipeline config YAML"),
    ],
) -> None:
    """Validate CRS of input files against expected configuration."""
    pipeline_config = PipelineConfig.from_yaml(config)

    from coord_xform.validation import validate_inputs

    results = validate_inputs(pipeline_config, inputs)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        typer.echo(f"[{status}] {result.file_path}: {result.message}")

    if not all(r.passed for r in results):
        raise typer.Exit(code=1)


@app.command()
def info(
    inputs: Annotated[
        list[Path],
        typer.Argument(help="Input files to inspect"),
    ],
) -> None:
    """Display CRS and metadata information for input files."""
    from coord_xform.readers import detect_format, get_reader

    for input_path in inputs:
        fmt = detect_format(input_path)
        reader = get_reader(fmt)
        metadata = reader.read_metadata(input_path)
        typer.echo(f"\n{input_path}:")
        typer.echo(f"  Format: {fmt.value}")
        typer.echo(f"  CRS: {metadata.crs or 'Not detected'}")
        typer.echo(f"  Point count: {metadata.point_count:,}")
        typer.echo(f"  Scan positions: {metadata.scan_count}")
        if metadata.bounds:
            typer.echo(f"  Bounds: {metadata.bounds}")

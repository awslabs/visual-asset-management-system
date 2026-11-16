# CAD/Mesh Metadata Extraction Pipeline

This pipeline extracts comprehensive metadata from CAD and Mesh files using CADQuery and Trimesh libraries.

## Supported File Formats

### CAD Formats

-   STEP (.step, .stp) - 3D CAD models with assembly information
-   DXF (.dxf) - 2D CAD drawings with layer information

### Mesh Formats

-   STL (.stl) - Stereolithography mesh format
-   OBJ (.obj) - Wavefront OBJ mesh format
-   PLY (.ply) - Polygon File Format
-   GLTF/GLB (.gltf, .glb) - GL Transmission Format
-   XYZ (.xyz) - Point cloud format

The text of an OBJ, ASCII STL, XYZ or glTF file, and the JSON chunk of a GLB, must be UTF-8. A file whose text is not UTF-8 fails with a message naming the file and the first byte that is not UTF-8 (`input_text.py`); the pipeline does not guess another encoding.

## Extracted Metadata

### For CAD Files

-   Geometric details (dimensions, volumes, surface areas)
-   Assembly hierarchy (component tree, relationships)
-   Materials and properties (if embedded)
-   Shape types statistics (faces, edges, vertices counts)
-   Units of measurement
-   Custom metadata from top-level nodes

### For Mesh Files

-   Assembly hierarchy (if supported by format)
-   Triangle/vertex/polygon counts
-   Texture information (embedded or referenced)
-   Shader information (for formats like GLTF)
-   Units of measurement
-   Bounding box dimensions and model size
-   Transform matrices (rotation, scale, translation)
-   Animation data (frame counts, duration)
-   Format-specific metadata (DRACO compression, 3D tiles, etc.)

## Usage

The pipeline accepts an input S3 URI to a CAD or mesh file and outputs a JSON metadata file to the specified S3 output location.

### Input Parameters

-   `inputS3AssetFilePath`: S3 URI of the input CAD or mesh file
-   `outputS3AssetMetadataPath`: S3 URI of the metadata output directory

### Output

The pipeline generates a JSON file containing the extracted metadata, named after the input file's path within its asset with `.attribute.json` appended (`parts/housing/pump.stl.attribute.json`). The write-back step reads that name and applies the contents to that file as file-level attributes.

Each value is at most `MAX_METADATA_VALUE_LENGTH` (400,000) characters, the limit the write-back step enforces on every value; one longer value would fail the whole update. `fit_metadata_value` in `lambda.py` shortens a longer value and marks it with `_truncated`: a list keeps its first items, an object cuts its longest list members, and text is cut with a note.

## Implementation

The pipeline uses:

-   CADQuery for CAD file processing
-   Trimesh for mesh file processing
-   AWS Lambda container for execution

## Dependencies

-   Python 3.12
-   aws-lambda-powertools
-   trimesh
-   cadquery
-   numpy

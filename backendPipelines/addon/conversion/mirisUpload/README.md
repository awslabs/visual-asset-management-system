# Miris Spatial Streaming Upload Pipeline

The Miris Upload pipeline uploads a VAMS asset's root USD file to [Miris Spatial Streaming](https://miris.com) and records the resulting Miris asset as file metadata on that file, so the VAMS Miris viewer can stream it. It is part of the Miris Spatial Streaming add-on and is deployed when `app.addons.useMirisStreaming.upload.enabled` is `true`.

-   **`container/`** - The AWS Batch container: root selection and `.usdz` packaging (`usd_packager.py`), the Miris client (`miris_uploader.py`), template settings (`upload_config.py`), the stream metadata file (`stream_metadata.py`), and the time budget (`time_budget.py`)
-   **`lambda/`** - The duplicate-upload gate (`mirisUploadGate.py`, `mirisClaim.py`), `vamsExecuteMirisUpload.py`, `openPipeline.py`, `constructPipeline.py`, and `pipelineEnd.py`
-   **`vamsSchema/`** - The pipeline and workflow registered in VAMS
-   CDK: `infra/lib/nestedStacks/addon/miris/upload/`

See `documentation/docusaurus-site/docs/developer/addon/miris-spatial-streaming/upload-pipeline.md` for triggers, root selection, time limits, failure reasons, and configuration.

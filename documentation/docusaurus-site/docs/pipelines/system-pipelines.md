# System pipelines

System pipelines and workflows are the processing steps a VAMS deployment ships and owns. They carry `isSystem: true`, a category of the form `SYSTEM - <Area>`, and are registered from the `vamsSchema` bundle that lives with the pipeline's source. Every other pipeline — including a pipeline you register yourself through the API, the CLI, or an external solution's own importer — is an ordinary record.

Only the deployment's `vamsSchema` importer sets `isSystem`. The pipeline and workflow request models ignore the key, so an API caller who supplies `isSystem: true` creates an ordinary record. The value appears in pipeline and workflow responses and drives the **System** badge in the web interface.

## What a system record allows

A system record is read-only through the API, the web interface, the CLI, and the MCP server except for a small set of pause switches and template content. A refused operation returns HTTP `400` with a message naming the rule.

| Operation                   | Allowed on a system record                                                                                                                                                                                                       |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pipeline update             | Only `enabled`. Any other field in the request body is refused, even with its stored value, naming the first such field: `System pipelines are read-only except "enabled"; "<field>" cannot be changed.`                         |
| Pipeline archive or restore | Refused: `System pipelines cannot be archived or restored through the API; the deployment owns them.`                                                                                                                            |
| Template add or delete      | Refused: `Templates of system pipelines cannot be added or deleted.`                                                                                                                                                             |
| Template update             | `configBody`, `tagSchema`, and `webFormJson` may change. Every other supplied field must equal the stored value — the web form sends the whole template, so unchanged fields pass and an edited locked field is refused by name. |
| Template tag schema         | Allowed (`PUT …/tagSchema`).                                                                                                                                                                                                     |
| Workflow update             | Only `enabled`. Any other field is refused, naming the first such field: `System workflows are read-only except "enabled"; "<field>" cannot be changed.`                                                                         |
| Workflow archive or restore | Refused: `System workflows cannot be archived or restored through the API; the deployment owns them.`                                                                                                                            |
| Trigger update              | Only `enabled`. `inputFileFilters` and `defaultTemplateIds`, when present in the body, must equal the stored trigger; when absent they are kept from the stored trigger, so a replace never blanks the bundle's filters.         |
| Trigger add or delete       | Refused: `Triggers of system workflows cannot be added or deleted.`                                                                                                                                                              |
| Execute, read, list         | Unchanged — a system workflow runs like any other.                                                                                                                                                                               |

Unarchiving is an update that sets `archived: false`, so it is refused with the archive message; the deployment restores an archived built-in when it registers it again.

:::note[Authorization is unchanged]
`isSystem` is not a permission constraint field. Scope access to system records the way you scope any pipeline or workflow — by `databaseId`, `pipelineId`/`workflowId`, or `category`. The shipped system records live in the `GLOBAL` database and carry a `SYSTEM - …` category, so a constraint on `category` selects them together. See [Permissions Model](../concepts/permissions-model.md).
:::

## Deployments own system records

The `enabled` switches on a system pipeline, its workflow, and its trigger are pause switches. Each deployment that registers the bundle again — any release that revises it — writes the pipeline and workflow back as enabled and unarchived and sets the trigger's `enabled` to the deployment's `autoRegisterAutoTriggerOnFileUpload` value. A trigger switched off in the web interface therefore fires again after such a deployment.

To stop a system pipeline durably, use the deployment configuration:

-   `autoRegisterWithVAMS: false` on the pipeline's configuration block archives its pipeline and workflow on the next deployment.
-   `autoRegisterAutoTriggerOnFileUpload: false` leaves the pipeline registered but keeps its file-upload trigger off.

While `app.vectorSearch.enabled` is `true`, configuration validation requires the SYSTEM GenAI metadata pipeline to be enabled and its trigger to be armed (`autoRegisterWithVAMS` and `autoRegisterAutoTriggerOnFileUpload` both `true`), because embeddings are produced by that trigger. The durable way to stop that trigger is to set `vectorSearch.enabled` to `false` together with `autoRegisterAutoTriggerOnFileUpload` set to `false`. The general rule for built-ins is described in [Pipelines and Workflows](../concepts/pipelines-and-workflows.md).

### Web, CLI, and MCP behaviour

-   **Web** — list pages show a **System** badge beside **Disabled** and **Archived**. The edit pages open read-only with every field disabled except `enabled`; template forms allow the configuration body and tag schema; the triggers editor allows the `enabled` switch. Edit, Archive, Create template, and Delete template actions are hidden.
-   **CLI** — `vamscli pipeline update`, `pipeline delete`, `pipeline template create|update|delete`, `workflow update`, `workflow delete`, `workflow trigger set|delete` return the API's `400` message for a system record. `vamscli workflow trigger set --disable` (and `--enable`) is the supported way to pause and resume a system trigger.
-   **MCP** — the write and destructive tools report the same `400`; `set_workflow_trigger(..., {"enabled": false})` is the toggle.

## Shipped system pipelines

| Pipeline                                                                        | Pipeline and workflow id     | Category           | Configuration block                                              | Compute                                                            |
| ------------------------------------------------------------------------------- | ---------------------------- | ------------------ | ---------------------------------------------------------------- | ------------------------------------------------------------------ |
| [SYSTEM - GenAI Metadata Generation](system-genai-metadata.md)                  | `system-genai-metadata`      | `SYSTEM - GenAI`   | `app.pipelines.useSystemGenAiMetadata`                           | AWS Lambda container images; optional AWS Batch (Fargate) renderer |
| [SYSTEM - GenAI Asset Summary](system-genai-metadata.md#asset-summary-workflow) | `system-genai-asset-summary` | `SYSTEM - GenAI`   | `app.pipelines.useSystemGenAiMetadata` (with `useGenAiAnalysis`) | AWS Lambda                                                         |
| [3D Preview Thumbnail](3d-thumbnail.md)                                         | `preview-3d-thumbnail`       | `SYSTEM - Preview` | `app.pipelines.usePreview3dThumbnail`                            | AWS Batch (Fargate)                                                |

The SYSTEM GenAI metadata and 3D preview thumbnail bundles each mark one default template (`system-genai-metadata-default`, `preview-3d-thumbnail-default`) and ship one `fileUpload` trigger whose allow list equals the pipeline's input-file filters. The SYSTEM GenAI metadata bundle also registers `system-genai-metadata-scene-to-asset`, which writes the description to the asset as well as the file (see [Write targets](system-genai-metadata.md#write-targets)). The SYSTEM GenAI metadata workflow additionally declares the `perInputFileVersion` concurrency restriction, so a second execution over the same file version is refused with `400` while one is running.

The SYSTEM GenAI asset summary is a second registration of the SYSTEM GenAI metadata deployment, present when its GenAI analysis is on (`useGenAiAnalysis`) and registered with `autoRegisterWithVAMS`. Run it on a whole asset or a folder, after the per-file pipeline has described the files: it describes the asset from those descriptions and writes the asset's `genai_asset_*` metadata. It has no trigger and one template (`system-genai-asset-summary-default`), and its workflow declares the `perAsset` concurrency restriction, so a second summary of the same asset is refused with `400` while one is running.

## Related pages

-   [Pipeline System Overview](overview.md)
-   [Vector search](../concepts/vector-search.md) — what the SYSTEM GenAI metadata pipeline's embeddings feed
-   [Building custom pipelines](custom-pipelines.md) — the `vamsSchema` bundle, including the `isSystem` key

# Pipelines and Workflows

Pipelines and workflows automate asset processing in VAMS. A **pipeline** defines a single processing step (such as converting a file format, extracting metadata, or generating preview thumbnails), while a **workflow** chains one or more pipelines together in sequence and can be triggered automatically on file upload.

## Concepts

### Pipelines

A pipeline represents a discrete processing operation. Pipelines can be built-in (deployed with VAMS) or user-created. Each pipeline has an execution type that determines how it processes data:

| Execution type    | Description                                                                                                                                        |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Lambda**        | Invokes an AWS Lambda function directly. Supports both synchronous execution and callback mode.                                                    |
| **SQS**           | Sends a message to an Amazon SQS queue. The downstream consumer processes the message asynchronously.                                              |
| **EventBridge**   | Publishes an event to an Amazon EventBridge event bus. The downstream consumer processes the event asynchronously.                                 |
| **DeadlineCloud** | Submits a job to an AWS Deadline Cloud queue for render-farm and batch processing. Asynchronous, and always reports completion through a callback. |

`DeadlineCloud` is selectable only when the deployment enables that execution type.

:::info
SQS and EventBridge pipelines without callback mode enabled operate as fire-and-forget integrations. They push data to the downstream service but do not return output files, preview images, or metadata back to VAMS.
:::

### Workflows

A workflow orchestrates one or more pipeline steps in sequence using AWS Step Functions. When a workflow executes, it runs each pipeline step in order, passing the output context from one step to the next.

Workflows can be:

-   **Database-specific** -- Scoped to a particular database, using pipelines from that database.
-   **GLOBAL** -- Available across all databases, using GLOBAL pipelines.

## Viewing available pipelines

1. Navigate to **Pipelines** from the left navigation menu.
2. Select a database from the database selector, or view all pipelines across databases.
3. The pipeline list displays all pipelines you have permission to access, showing the pipeline name, its owning database, execution type, status, template count, and the actions available on it. The list can be grouped by category.

![Pipelines page showing registered pipelines with properties](/img/pipelines_page_20260323_v2.5.png)

To view details of a specific pipeline, click its name in the list. The detail view shows its full configuration — execution settings, the admin settings that govern how it may be run, and its configuration templates.

## Creating a custom pipeline

Navigate to **Pipelines** and click **Create Pipeline**. The form is a three-step wizard — **Basic**, **Execution**, then **Settings** — and the pipeline is created when you finish the last step.

### Basic

| Field             | Required | Description                                                                                                         |
| ----------------- | -------- | ------------------------------------------------------------------------------------------------------------------- |
| **Pipeline ID**   | --       | Shown when editing an existing pipeline; it is generated on creation and cannot be changed.                         |
| **Pipeline Name** | Yes      | Display name for the pipeline, up to 256 characters.                                                                |
| **Category**      | No       | Free-text grouping used to organize and filter the pipeline lists (for example, `Conversion`, `3D Reconstruction`). |
| **Description**   | No       | What the pipeline does.                                                                                             |

The owning database is the one you are creating within — pipelines created from a database's page belong to that database, and those created as `GLOBAL` are available to workflows in any database. See [GLOBAL vs. database-specific](#global-vs-database-specific).

### Execution

**Execution Type** selects how VAMS hands work to the pipeline, and determines which fields follow:

| Execution type    | Fields                                                                                                   |
| ----------------- | -------------------------------------------------------------------------------------------------------- |
| **Lambda**        | **Lambda function ARN or name** — the function VAMS invokes.                                             |
| **SQS**           | **Queue URL** — the full Amazon SQS queue URL.                                                           |
| **EventBridge**   | **Event Bus ARN** and **Source**.                                                                        |
| **DeadlineCloud** | **Farm ID**, **Queue ID**, **Storage Profile ID**, **Max Retries Per Task**, **Max Failed Tasks Count**. |

`DeadlineCloud` is available only when the deployment enables it; the fields are shown but disabled otherwise.

The remaining execution fields apply to any type:

| Field                                | Required    | Description                                                                                                                                                                                                                                                                  |
| ------------------------------------ | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Wait For Callback**                | Yes         | When enabled, VAMS waits for the pipeline to report completion through an AWS Step Functions task token instead of treating the invocation as the result. Required for `DeadlineCloud`; `SQS` and `EventBridge` are asynchronous, so without it the step is fire-and-forget. |
| **Task Timeout (seconds)**           | Conditional | Required with callback. How long the step may run before it is failed, up to 604,800 seconds (one week).                                                                                                                                                                     |
| **Task Heartbeat Timeout (seconds)** | No          | When set, the pipeline must report a heartbeat within this interval. Must be less than the task timeout.                                                                                                                                                                     |
| **Template Type**                    | No          | Format of the configuration body the pipeline receives (for example `json`, `yaml`, `openjd`), so the configuration editor and viewers highlight it correctly.                                                                                                               |

### Settings

These are the admin controls that govern how the pipeline may be run — the contract the execute form and the file-upload triggers are checked against:

| Setting                         | Description                                                                                                                                                                                                                                                                           |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Input file count**            | `None` (a results-only or generate-from-nothing pipeline), `One file`, or `Multiple files`.                                                                                                                                                                                           |
| **Asset selection rules**       | Whether an execution may select a whole asset, a folder, files from more than one asset, or is limited to a single asset.                                                                                                                                                             |
| **Metadata inputs**             | Which metadata the pipeline is given: asset metadata, per-file metadata, and file attributes.                                                                                                                                                                                         |
| **Template settings**           | Whether a configuration template must be resolved before the pipeline can run, and whether a caller may supply a one-off configuration body at run time.                                                                                                                              |
| **Input file filters**          | Allow and exclude glob patterns for the file types the pipeline accepts. An empty allow list means any file; the exclude list is applied last and may not match everything.                                                                                                           |
| **Aux Preview Pipeline Suffix** | A viewer-specific subfolder (for example `-preview`, `/PotreeViewer`) appended to the pipeline's auxiliary preview path, so a pipeline producing viewer data writes it where the matching viewer reads it. Leave empty unless the pipeline produces viewer-specific auxiliary output. |

:::note[Choosing an input file count when templates differ]
When one pipeline supports several modes that consume different inputs, set the input file count to the **lowest** value any of its templates needs and let each template raise it. The execute form then asks for a file only when the chosen template actually consumes one. See [Building custom pipelines](../pipelines/custom-pipelines.md#pipelinejson).
:::

:::warning
A pipeline cannot be deleted if it is currently used by any workflow. You must remove the pipeline from all workflows before deleting it.
:::

### Updating a pipeline

When you update an existing pipeline, VAMS prompts you to choose whether to also update all workflows that reference this pipeline. This ensures workflow definitions stay in sync with pipeline changes.

## Viewing available workflows

1. Navigate to **Workflows** from the left navigation menu.
2. Select a database or view all workflows across databases.
3. The workflow list displays workflow names, databases, descriptions, and associated actions.

![Workflows page showing available workflows](/img/workflows_page_20260323_v2.5.png)

## Creating a workflow

Navigate to **Workflows** and click **Create Workflow**. If you are not already viewing a specific database, select the database for this workflow, or **GLOBAL** for a cross-database workflow.

The editor is a step-by-step wizard: **Basic information**, **Execution settings**, **Pipelines**, then **Review**. Editing an existing workflow adds a **Triggers (optional)** step — triggers are attached to a saved workflow, so they cannot be set while creating one.

### Basic information

| Field             | Required | Description                                                        |
| ----------------- | -------- | ------------------------------------------------------------------ |
| **Workflow Name** | Yes      | Display name for the workflow.                                     |
| **Category**      | No       | Free-text grouping used to organize and filter the workflow lists. |
| **Description**   | No       | What the workflow does.                                            |

### Execution settings

The workflow's own gate: input file count, asset selection rules, metadata inputs, output target, concurrency, and its input file filters. Every execution is checked against these before any pipeline is considered.

These are **authored, not inherited** from the workflow's pipelines. Set the input file count to the **highest** value any pipeline and template combination in the workflow can require — a lower value rejects a selection a template would have accepted. The input file filters are applied **before** the pipelines' own, so a filter here that excludes a type one of its pipelines needs makes that pipeline unsatisfiable; the [Validation](#saving-the-workflow) panel warns when that happens.

### Triggers (optional)

A **file upload trigger** runs the workflow automatically when a matching file is uploaded to an asset in its database. Triggers are managed separately from the workflow's own details, and each trigger has its own allow and exclude filters rather than a single extension list:

-   **Allow filters** name the file patterns that fire the trigger. Leaving the list empty means any uploaded file fires it.
-   **Exclude filters** are applied after the allow list and remove matches from it. Because exclude runs last, a pattern that matches everything is rejected — it would suppress every upload.
-   **Default template IDs (per pipeline)** pick the template each pipeline step uses on an automatic run, since no one is present to choose one. `None (choose at run time)` leaves it to the pipeline's own default.

:::tip
Triggers are how processing chains together. A workflow that generates preview thumbnails or extracts metadata can be set to fire on `.e57` point cloud uploads, so the work happens on ingest with no one starting it.

For a trigger to fire on files that **another workflow produced**, that workflow must also permit trigger chaining. A workflow never fires on output it wrote itself, so it cannot loop on its own files.
:::

### Pipelines

Click **Add Pipeline** to add a step, then choose its pipeline. The available pipelines are those in the workflow's own database plus all GLOBAL pipelines.

The steps form an ordered list and execute top to bottom. Drag a step by its handle to reorder it, or use **Remove pipeline** to drop it. Each step may also set:

-   A **default template**, used when an execution does not name one for that step — which is what an automatic trigger run relies on.
-   A **job name**, which becomes a folder segment in that step's output path. Leave it blank unless the pipeline's own id would not identify the step: blank already falls back to the pipeline id, so each step's output stays distinct. See [Job names](../concepts/pipelines-and-workflows.md#job-names).

Once at least one step is present, a diagram of the resulting workflow appears below the list. It is a preview of the order you have built, not an editor.

:::warning[Each pipeline may appear only once]
A workflow cannot use the same pipeline for two steps — the second reference overwrites the first step's resolved configuration and both run identically, with no error reported. When one model needs two modes in a workflow, use two pipelines that share a container image. See [Specified pipelines](../concepts/pipelines-and-workflows.md#specified-pipelines).
:::

### Saving the workflow

The final **Review** step summarizes the workflow before it is written. Confirm from there — **Create Workflow** for a new workflow, **Save** when editing — and the definition is stored and its AWS Step Functions state machine is created or updated.

A **Validation** panel reports what it found before and after saving, split by consequence:

-   **Errors** block the save — for example, a workflow with no pipeline steps.
-   **Warnings** do not block it. They flag a workflow that is saveable but may not be able to run as
    intended, most often because the workflow's own settings starve one of its pipelines:

    | Warning                                                                  | Cause                                                                                                                                                                             |
    | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
    | A pipeline's accepted file types do not overlap what the workflow allows | The workflow's **allow** list admits nothing the pipeline can process, so no selection can ever satisfy it.                                                                       |
    | The workflow excludes a type the pipeline accepts                        | The allow lists may overlap perfectly and the **exclude** list still removes the file afterwards, because exclude is applied second. The warning names what is left, if anything. |
    | A pipeline uses metadata the workflow has turned off                     | The pipeline will run without that metadata rather than fail.                                                                                                                     |

    Warnings are worth resolving before anyone tries to run the workflow: a mismatch here becomes a
    rejected execution later, when the reason is less obvious.

![Workflow editor with pipeline steps configured and visual graph](/img/workflow_editor_20260323_v2.5.png)

## Executing workflows

![Workflow executions tab showing execution history on asset detail page](/img/view_asset_workflow_executions_tab_20260323_v2.5.png)

Workflows can be executed in two ways:

### Manual execution

Start an execution from an asset's **Automation** menu (in the file manager toolbar, beside **Export**),
or from the **Execute** action on a workflow. Either opens the same wizard, which has one stage per
decision to make: **Input**, then one stage for each pipeline in the workflow, then **Review**.

#### Input

Choose the files to process, and the output target when the workflow allows it to be overridden.

Files are chosen through a cascading picker — **Database → Asset → File** — that searches as you type, so
an asset holding thousands of files does not have to be listed to find one. Launching from an asset
pre-fills that asset, and you can still switch to a different one.

Only files the workflow accepts are offered. When its filters hide some of an asset's files, the picker
says how many were hidden, so a file that is missing from the list reads as "this workflow does not take
that type" rather than "it is not there". The stage also shows the file types the workflow and its
pipelines require, so you can see what is being asked for before selecting anything.

What you can select depends on the workflow's configuration:

| Workflow accepts | The Input stage offers                                                                                                                                                                 |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| One file         | A single picker. **Whole asset (all files)** appears as an option only when the workflow permits a whole-asset input.                                                                  |
| Multiple files   | A list you add rows to and remove rows from. Each row has its own database and asset picker, so one execution can combine files from several assets — or several files from one asset. |
| No files         | No file picker. The run takes its identity from the output target instead.                                                                                                             |

Each selected file may optionally pin a **file version**. The list holds that file's own stored versions,
newest first; the default, **Latest**, reads whichever version is current when the execution starts rather
than the one that was current while you filled in the form. Pin a version to reproduce an earlier run
against exactly the input it used. A whole-asset or folder selection spans many files, so it has no single
version to pin and the option does not appear.

#### Pipeline stages

One stage per pipeline in the workflow. Choose the configuration template for that step, and fill in any
values the template asks for. The rendered configuration is collapsed by default — expand it to review or,
where the pipeline allows it, override the exact configuration that will be sent.

#### Review

A summary of everything the run will use: the input files and pinned versions, the output target and path
prefix, and each step's template and values. Launching from here starts the execution.

The workflow then runs asynchronously, processing the selected files through each pipeline step in
sequence.

### Automatic execution

When a workflow has auto-trigger enabled, it runs automatically whenever a file matching the configured extensions is uploaded to an asset within the workflow's database. The uploaded file is passed as the input to the workflow.

### Monitoring execution

Execution progress can be monitored from the **Executions** page or from the asset detail page. Each execution shows:

-   The workflow and pipeline step being executed
-   Current status (running, succeeded, failed)
-   Start and end timestamps
-   Error details for failed executions

:::note
Workflow execution is asynchronous. Results (output files, previews, metadata) appear on the asset after all pipeline steps complete. Changes may take a few minutes to propagate through the system, including search results.
:::

### Execution details

Selecting an execution opens its detail view. The header states the run's identity and outcome — status,
workflow, trigger, start/stop times and duration — together with the **output target**: the output type
(`asset`, or results-only for a run that writes no files), the destination database and asset ids, and the
**output path prefix** the run wrote beneath. Knowing the prefix is what lets you find a particular run's
output in the asset when several runs have written to the same place.

The tabs below it break the run down:

| Tab           | What it shows                                                                                                                                                                                                   |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Inputs**    | Each selected input file with the exact file version the run read, plus the metadata gathered from the input assets and files and passed to the pipelines                                                       |
| **Pipelines** | One entry per pipeline step: its status and timings, the template it used, the dynamic tag values supplied, and the final configuration body actually delivered to it                                           |
| **Outputs**   | The output target and path prefix, then everything the run produced: output **files** with their version and size, **preview** files, **metadata** written back to the asset, and any **results** text returned |
| **Settings**  | The system settings the run was governed by — the workflow's own settings, and per step the settings that step ran under                                                                                        |
| **Logs**      | The execution log, selectable per step (permission-gated)                                                                                                                                                       |

:::note[Outputs lists tracked asset files only]
A pipeline may also write to the auxiliary location — scratch space, and certain non-versioned viewer data
such as point-cloud viewer tiles and some preview files. Those files are not tracked as asset outputs, so
they do not appear on this tab even though the run produced them. A pipeline that wrote _only_ to the
auxiliary location shows no outputs here despite having succeeded.
:::

The **Logs** tab can be scoped to the whole execution or to a single pipeline step. Beyond the log the
step's own process wrote, a step's logs include the log of the resource VAMS invoked for it — for a Lambda
step, that function's own log. That is usually where the reason for a launch that failed before the
pipeline started is recorded. Steps invoked through a queue, an event bus, or Deadline Cloud have no such
log, so nothing extra is shown for them. If a log could not be read, the tab says which one rather than
silently returning less.

Stored logs are captured as a run finishes, which can be before the logging service has finished ingesting
the run's events, so a stored log is often empty even for a run that succeeded. Switching the source to
live reads the events directly; for the whole execution, the state-machine history is always available
immediately.

A run is recorded as a **snapshot**, not as a set of pointers. Templates, tag schemas and pipeline
settings can all be edited or archived after a run finishes, so the execution stores the template
version, the tag values, the rendered configuration and the settings in force at the time. An execution
from months ago therefore still explains itself even if the definitions behind it have since changed.

The **Settings** tab is where that matters most. A pipeline's own settings can be adjusted by the
template chosen for a particular run — a template may change how many input files are accepted, which
files are eligible, what metadata is provided, or which asset selections are allowed. The tab shows the
resulting settings for each step and, where a template changed something, which values it overrode. That
is how to answer "why did this run accept these inputs?" after the fact.

:::note
The workflow-level settings shown are read live from the workflow, so they reflect the workflow as it
stands **now**. The per-step settings are the recorded snapshot from the run itself. Where a workflow has
been edited since, the two can legitimately differ — the tab labels which is which.
:::

## GLOBAL vs. database-specific

| Scope                 | Pipelines                                         | Workflows                                                  |
| --------------------- | ------------------------------------------------- | ---------------------------------------------------------- |
| **Database-specific** | Available only within the database they belong to | Can use pipelines from their database and GLOBAL pipelines |
| **GLOBAL**            | Available to workflows in any database            | Can only use GLOBAL pipelines                              |

GLOBAL pipelines are typically built-in processing pipelines deployed with VAMS (such as 3D conversion, preview generation, and metadata extraction). Database-specific pipelines are user-created for domain-specific processing needs.

## Built-in pipelines

VAMS may include built-in pipelines depending on your deployment configuration. These are created during deployment and registered as GLOBAL pipelines. Common built-in pipelines include:

-   **3D Conversion** -- Converts 3D file formats (for example, IFC to glTF).
-   **Preview Generation** -- Creates thumbnail preview images for assets and files.
-   **Point Cloud Processing** -- Processes point cloud data (for example, E57, LAS) for web visualization.
-   **Metadata Extraction** -- Extracts metadata from file headers and content.
-   **GenAI Labeling** -- Uses generative AI to automatically generate labels and descriptions.
-   **Gaussian Splatting** -- Generates 3D Gaussian splats from image and video media files.
-   **Physical AI Inference and Fine-Tuning** -- GPU-accelerated pipelines for NVIDIA world foundation models, vision language models (VLMs), and vision-language-action models (VLAs) including inference, simulation training, and model fine-tuning.

For detailed pipeline documentation, see the [Pipelines overview](../pipelines/overview.md), [deployment configuration reference](../deployment/configuration-reference.md), and [custom pipelines guide](../pipelines/custom-pipelines.md).

## Permissions

Access to pipelines and workflows is controlled by the VAMS permission system. Users need appropriate constraints on the `pipeline` and `workflow` object types to view, create, edit, or delete pipelines and workflows. For details, see [Permissions](permissions.md).

:::tip[CLI alternative]
Workflow operations can also be performed via the command line. See [CLI Workflow Commands](../cli/commands/workflows.md).
:::

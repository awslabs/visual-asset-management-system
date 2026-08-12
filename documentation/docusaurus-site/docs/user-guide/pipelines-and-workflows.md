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

A workflow runs one or more pipeline steps in sequence. When a workflow executes, it runs each pipeline step in order, passing the output of one step on to the next.

Workflows can be:

-   **Database-specific** -- Scoped to a particular database, using pipelines from that database.
-   **GLOBAL** -- Available across all databases, using GLOBAL pipelines.

## Viewing available pipelines

1. Navigate to **Pipelines** from the left navigation menu.
2. Select a database from the database selector, or view all pipelines across databases.
3. The pipeline list displays all pipelines you have permission to access, showing the pipeline name and id, its owning database, execution type, status, and template count. The list can be grouped by category or by database, filtered by execution type, status and database, and archived pipelines can be included.

![Pipelines page showing registered pipelines with properties](/img/pipelines_page_20260803_v2.6.png)

Each entry's **⋮** actions menu holds what you can do with that pipeline: **Edit** opens its form, where its
execution settings and the admin settings that govern how it may be run are shown; **Templates** opens its
configuration templates as a separate list; **Archive** withdraws it from use. Only the actions your
permissions allow appear in the menu, so a read-only user sees **Templates** alone.

## Creating a custom pipeline

Navigate to **Pipelines** and click **Create Pipeline**. The form is a three-step wizard — **Basic**, **Execution**, then **Settings** — and the pipeline is created when you finish the last step.

![Pipeline wizard showing the Basic step and the three-step progress indicator](/img/pipeline_wizard_20260803_v2.6.png)

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

| Execution type    | Fields                                                                                                                                                                                                           |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Lambda**        | **Lambda Resource ID** — the function VAMS invokes, by ARN or name. Leave it blank to have VAMS create a new function for the pipeline.                                                                          |
| **SQS**           | **Queue URL** — the full Amazon SQS queue URL. Required.                                                                                                                                                         |
| **EventBridge**   | **Event Bus ARN**, **Source**, and **Detail Type** — all three required.                                                                                                                                         |
| **DeadlineCloud** | **Farm ID** and **Queue ID** (required), **Job Template** (required), plus **Storage Profile ID**, **Priority**, **Max Retries Per Task**, **Max Failed Tasks Count**, and **Template Type** (`JSON` or `YAML`). |

`DeadlineCloud` is available only when the deployment enables it; the fields are shown but disabled otherwise.

The remaining execution fields apply to any type:

| Field                                | Required | Description                                                                                                                                                                                                                                                             |
| ------------------------------------ | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Wait For Callback**                | Yes      | When enabled, VAMS waits for the pipeline to report completion instead of treating the invocation itself as the result. Locked on for `DeadlineCloud`; `SQS` and `EventBridge` are asynchronous, so without it the step is fire-and-forget.                             |
| **Task Timeout (seconds)**           | No       | With callback enabled, how long the step may run before it is failed, from 1 to 604,800 seconds (one week). Leave it blank to accept the 24-hour default.                                                                                                               |
| **Task Heartbeat Timeout (seconds)** | No       | With callback enabled, the interval within which the pipeline must report a heartbeat, in the same range. Leave it blank for a pipeline that reports no heartbeat. Set it below the task timeout — a larger value never takes effect, because the task times out first. |

### Settings

These are the admin controls that govern how the pipeline may be run — the contract the execute form and the file-upload triggers are checked against:

| Setting                                | Description                                                                                                                                                                                                                                                                           |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Input file count**                   | `None` (a results-only or generate-from-nothing pipeline), `One file`, or `Multiple files`.                                                                                                                                                                                           |
| **Asset selection rules**              | **Asset span** — whether an execution's input files may come from more than one asset — plus whether a whole asset or a folder may be selected.                                                                                                                                       |
| **Input file filters — allow/exclude** | The file patterns the pipeline accepts, as extensions (`*.glb`), file names, paths, or wildcards. An empty allow list means any file; the exclude list is applied last and may not match everything. Hidden for a pipeline that takes no input files.                                 |
| **Metadata provided to the pipeline**  | Which metadata the pipeline is given: database metadata, asset metadata, per-file metadata, and file attributes. See [Metadata inputs](#metadata-inputs).                                                                                                                             |
| **Require template**                   | When on, every execution of this pipeline must use one of its configuration templates.                                                                                                                                                                                                |
| **Allow custom template override**     | When on, whoever runs the pipeline may supply a one-off configuration body in place of a saved template.                                                                                                                                                                              |
| **Aux Preview Pipeline Suffix**        | A viewer-specific subfolder (for example `-preview`, `/PotreeViewer`) appended to the pipeline's auxiliary preview path, so a pipeline producing viewer data writes it where the matching viewer reads it. Leave empty unless the pipeline produces viewer-specific auxiliary output. |

:::note[Choosing an input file count when templates differ]
When one pipeline supports several modes that consume different inputs, set the input file count to the **lowest** value any of its templates needs and let each template raise it. The execute form then asks for a file only when the chosen template actually consumes one. See [Building custom pipelines](../pipelines/custom-pipelines.md#pipelinejson).
:::

### Archiving a pipeline

**Archive** in a pipeline's actions menu withdraws it from use: it is disabled, it stops appearing in the
lists unless **Include Archived** is selected, and it cannot be chosen for a new workflow step. The pipeline
record is kept, so archiving is reversible — an archived pipeline is restored with
[`vamscli pipeline unarchive`](../cli/commands/pipelines.md).

:::warning[Check which workflows use a pipeline before archiving it]
Archiving succeeds even while workflows still reference the pipeline, and those workflows keep their
definitions. Their next execution is then rejected, because one of their steps points at a disabled,
archived pipeline. Remove the pipeline from every workflow that uses it first.
:::

### Updating a pipeline

Editing a pipeline updates the pipeline itself. The workflows that reference it keep running against the
execution configuration they were last saved with, so after changing where a pipeline sends its work — its
execution type, its target function, queue, event bus or farm, or its callback and timeout settings — open
each workflow that uses it and save it again to bring it up to date. Saving such a change reports a warning
naming the workflows that reference the pipeline, so you know which ones to revisit.

## Viewing available workflows

1. Navigate to **Workflows** from the left navigation menu.
2. Select a database or view all workflows across databases.
3. The workflow list displays each workflow's name and id, its database, category, and how many pipelines, executions and triggers it has — with the number of enabled triggers called out when some are switched off. The list can be filtered by status, by whether the workflow has an enabled trigger, and by database.

![Workflows page showing available workflows](/img/workflows_page_20260803_v2.6.png)

Each entry's **⋮** actions menu holds **Edit**, **Execute**, **View Executions**, and **Archive**, limited to the actions your permissions allow.

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

The workflow's own gate. Every execution is checked against these before any pipeline is considered:

| Setting                                | Description                                                                                                                                                                |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Input file count**                   | `None`, `One file`, or `Multiple files`.                                                                                                                                   |
| **Asset selection rules**              | The asset span, and whether a whole asset or a folder may be selected.                                                                                                     |
| **Input file filters — allow/exclude** | The file patterns the workflow admits. Hidden when the workflow takes no input files.                                                                                      |
| **Metadata provided to pipelines**     | Database metadata, asset metadata, file metadata, and file attributes.                                                                                                     |
| **Output destination**                 | **Write to an asset**, or **Results only** for a workflow that records results text and logs and writes no asset output.                                                   |
| **Allow choosing the output asset**    | Whether whoever runs the workflow may send output to a different asset and set an output path prefix. Offered for an asset destination.                                    |
| **Default output path prefix**         | The prefix an execution is pre-filled with. It supports tags resolved per run, so `/\{\{executionId\}\}/` gives every run its own folder. Leave it blank to add no prefix. |
| **Allow workflow trigger chaining**    | Whether a file written by another workflow may fire this workflow's triggers. A workflow never fires on output it wrote itself, so it cannot loop on its own files.        |
| **Concurrency restriction**            | `None`, `One per asset`, or `One per input file` — whether a new execution waits while a conflicting one is still running.                                                 |

These are **authored, not inherited** from the workflow's pipelines. Set the input file count to the **highest** value any pipeline and template combination in the workflow can require — a lower value rejects a selection a template would have accepted. The input file filters are applied **before** the pipelines' own, so a filter here that excludes a type one of its pipelines needs makes that pipeline unsatisfiable; the [Validation](#saving-the-workflow) panel warns when that happens.

:::warning[Chained triggering can run in a loop]
Two workflows that each write a file the other accepts trigger each other indefinitely. Check the input file filters of every workflow in the chain before turning chaining on.
:::

### Triggers (optional)

A **file upload trigger** runs the workflow automatically when a matching file is uploaded to an asset in its database. Triggers are managed separately from the workflow's own details, and each trigger has its own allow and exclude filters rather than a single extension list:

-   **Allow filters** name the file patterns that fire the trigger. Leaving the list empty means any uploaded file fires it.
-   **Exclude filters** are applied after the allow list and remove matches from it. Because exclude runs last, a pattern that matches everything is rejected — it would suppress every upload.
-   **Default template IDs (per pipeline)** pick the template each pipeline step uses on an automatic run, since no one is present to choose one. `None (choose at run time)` leaves it to the pipeline's own default.

A workflow can carry **more than one trigger of the same kind**, so the same workflow can react differently to different uploads — for example converting `.e57` uploads with one template and `.las` uploads with another. Each trigger has its own filters and its own default templates, and an upload runs the workflow once for every trigger whose filters match it.

The **Trigger name** field is what separates them. Leave it empty for the workflow's first trigger of a kind; give a name (letters, numbers, hyphens and underscores) to add another. A name cannot be changed once the trigger is saved, because it identifies the trigger. Two conditions are refused:

-   A workflow whose **concurrency restriction** is per-asset supports only one trigger of a kind, since several would compete for the same asset.
-   Two triggers of a kind cannot use the **same default templates**. The templates are what distinguish them, so the same set twice describes the same trigger — including two that both choose no template.

:::tip
Triggers are how processing chains together. A workflow that generates preview thumbnails or extracts metadata can be set to fire on `.e57` point cloud uploads, so the work happens on ingest with no one starting it.

For a trigger to fire on files that **another workflow produced**, that workflow must also permit trigger chaining. A workflow never fires on output it wrote itself, so it cannot loop on its own files.
:::

### Pipelines

Click **Add Pipeline** to add a step, then choose its pipeline. The available pipelines are those in the workflow's own database plus all GLOBAL pipelines.

The steps form an ordered list and execute top to bottom. Drag a step by its handle to reorder it, or use **Remove** to drop it. Each step may also set:

-   A **Default Template**, used when an execution does not name one for that step — which is what an automatic trigger run relies on.
-   A **Job Name**, a label of 3–63 letters, numbers, hyphens and underscores. Leave it blank unless the pipeline's own id would not identify the step. See [Job names](../concepts/pipelines-and-workflows.md#job-names).

Once at least one step is present, a diagram of the resulting workflow appears below the list. It is a preview of the order you have built, not an editor.

:::warning[Each pipeline may appear only once]
A workflow cannot use the same pipeline for two steps — the second reference overwrites the first step's configuration and both run identically. A pipeline another step already uses is offered as **(already in this workflow)** and cannot be chosen again. When one model needs two modes in a workflow, use two pipelines. See [Specified pipelines](../concepts/pipelines-and-workflows.md#specified-pipelines).
:::

:::warning[Each step needs its own job name]
Two steps that carry the same job name are named identically in the run's step list and logs, leaving no way to tell them apart. The form reports a repeated name beside the field.
:::

### Saving the workflow

The final **Review** step summarizes the workflow before it is written. Confirm from there — **Save** — and the workflow is stored and made runnable.

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

![Workflow editor with pipeline steps configured and visual graph](/img/workflow_editor_20260803_v2.6.png)

## Executing workflows

![Workflow executions tab showing execution history on asset detail page](/img/view_asset_workflow_executions_tab_20260803_v2.6.png)

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
| No files         | No file picker. The run takes its identity from the output target instead, and a **Metadata Sources** section appears when its pipelines read asset or database metadata.              |

Each selected file may optionally pin a **file version**. The list holds that file's own stored versions,
newest first; the default, **Latest**, reads whichever version is current when the execution starts rather
than the one that was current while you filled in the form. Pin a version to reproduce an earlier run
against exactly the input it used. A whole-asset or folder selection spans many files, so it has no single
version to pin and the option does not appear.

#### Running a workflow with no input files

A workflow that takes **no files** — one whose pipelines generate their output rather than transform an
input, for example a text-to-3D or text-to-video pipeline — has nothing to choose in a file picker. What it
can still be given is metadata, and that is what the **Metadata Sources** section of the Input stage is for.
It appears only when the workflow and its pipelines actually read asset or database metadata, and offers:

-   **Metadata source database (optional)** — the one database whose own metadata is read and handed to the
    steps. One database at a time; an all-databases selection has no metadata to read, so only a specific
    database can be named.
-   **Metadata source asset (optional)** — an asset whose asset-level metadata is read. Choose it through a
    **Database → Asset** picker that searches as you type. **Add Metadata Source Asset** adds another when
    the workflow permits input from more than one asset; a single-asset workflow offers one slot.

Everything in this section is optional, and the workflow runs whether or not you select any of it. A
metadata source is **not** an input file: nothing is read from the asset's files, no file version is
involved, and the selection does not decide where output is written.

Because there is no input file to infer a destination from, a workflow of this kind that writes to an asset
needs its **Output Target** named explicitly — choose the output database and asset in the same stage. Such a
workflow therefore has to permit choosing the output asset. A results-only workflow writes no asset output,
so it asks for neither.

:::tip[When to name a source]
Name one when a pipeline is meant to work from a particular asset's or database's stored metadata — a
prompt, a model setting, or a description held there. When nothing is named, the launch reports a warning
for each pipeline that reads metadata it was given no source for, and that pipeline runs without it.
:::

#### Pipeline stages

One stage per pipeline in the workflow. Choose the configuration template for that step, and fill in any
values the template asks for. The rendered configuration is collapsed by default — expand it to review or,
where the pipeline allows it, override the exact configuration that will be sent.

#### Review

A summary of everything the run will use: the input files and pinned versions, the output target and path
prefix, and each step's template and values. Launching from here starts the execution.

The workflow then runs asynchronously, processing the selected files through each pipeline step in
sequence.

### Metadata inputs

Besides the files it processes, an execution collects the stored metadata its pipelines asked for and
hands it to each step. Four kinds are collected independently, each switched on or off by the workflow's
and the pipeline's **Metadata provided** settings: each database's own metadata, each asset's own metadata,
each input file's metadata, and each input file's attributes. A kind reaches a pipeline only when the
workflow and that pipeline both have it on.

Which entities a run collects from follows from what it processes: every asset the selected files belong
to, every asset named as a metadata source, and every database those assets live in. A run that takes no
input files has nothing to derive from, so it collects the one database and the assets named as its
metadata sources — see [Running a workflow with no input files](#running-a-workflow-with-no-input-files).

Naming a metadata source is always optional. Nothing requires a source to be chosen, and a run launches
without one. A pipeline that genuinely needs particular metadata checks for it and reports the failure on
its own step, so a missing source shows up as that step failing rather than as a rejected launch.

Database metadata is read-only: it is given to a pipeline as input, and no pipeline writes metadata back
to a database. Pipeline metadata write-back applies to assets and files.

The metadata collected for each entity is capped on its own rather than against a shared total: at most
1,000 metadata entries and 300 KB for each database, each asset, each file's metadata, and each file's
attributes. A run over three databases holding five assets and ten files therefore collects up to 1,000
entries for each of the three databases, up to 1,000 for each of the five assets, and up to 1,000 for
each of the ten files. Entries are kept in key order, so the same entity yields the same set each time
it is read. Nothing is dropped silently — when an entity is capped, the launch reports a warning naming
it, and the same happens when a source database's metadata cannot be read. A single run reads at most
1,000 input files and 1,000 metadata-source assets.

### Automatic execution

A workflow runs automatically when one of its **triggers** matches. A file upload trigger fires when a file
matching its allow filters — and not removed by its exclude filters — is uploaded to an asset in the
workflow's database, and the uploaded file becomes the workflow's input. A workflow may carry several
triggers, and an upload runs the workflow once for every trigger it matches.

Triggers are set on a saved workflow, in the **Triggers (optional)** step that appears when editing it. Each
trigger carries its own filters and its own default template per pipeline step, since no one is present to
choose a template on an automatic run. See [Triggers (optional)](#triggers-optional).

### Monitoring execution

Execution progress can be monitored from the **Executions** page or from the asset detail page. Each execution shows:

-   The workflow and pipeline step being executed
-   Current status (running, succeeded, failed)
-   Start and end timestamps
-   Error details for failed executions

The **Executions** page lists every execution you may see, across all workflows and databases. A run is listed when you can view its workflow and every asset it read — each selected file's asset plus each asset named as a metadata source — or, for a run that read nothing, the asset it wrote to. The status, trigger, workflow, workflow database, and time-window filters narrow the list, and the output columns identify the asset each run wrote to.

![Executions page listing workflow executions with status, trigger, and output columns](/img/executions_page_20260803_v2.6.png)

:::note
Workflow execution is asynchronous. Results (output files, previews, metadata) appear on the asset after all pipeline steps complete. Changes may take a few minutes to propagate through the system, including search results.
:::

### Execution details

Selecting an execution opens its detail view. The header states the run's identity and outcome — status,
workflow, trigger, start and stop times and duration — together with where it wrote: the **Output Type**
(an asset, or **Results only** for a run that writes no files), the destination database and asset, and the
**Output Path Prefix** the run wrote beneath. Knowing the prefix is what lets you find a particular run's
output in the asset when several runs have written to the same place.

The tabs below it break the run down:

| Tab           | What it shows                                                                                                                                                                                                     |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Inputs**    | Each selected input file with the exact file version the run read, then the metadata gathered and passed to the pipelines in two blocks — each involved database's own metadata, then the asset and file metadata |
| **Pipelines** | One entry per pipeline step: its status and timings, the template it used, the tag values supplied, and the final configuration actually delivered to it                                                          |
| **Outputs**   | Where the run wrote, then everything it produced: output **files** with their version and size, **preview** files, **metadata** written back to the asset, and any **results** text returned                      |
| **Settings**  | The settings the run was governed by — the workflow's own settings, and per step the settings that step ran under                                                                                                 |
| **Logs**      | The execution log, selectable per step. Available to users whose permissions allow reading logs                                                                                                                   |

:::note[Outputs lists asset files only]
A pipeline may also write working files and certain viewer data — point-cloud viewer tiles, some preview
files — to a scratch location rather than onto the asset. Those files are not asset outputs, so they do not
appear on this tab even though the run produced them. A pipeline that wrote _only_ there shows no outputs
here despite having succeeded.
:::

The **Logs** tab can be scoped to the whole execution or to a single pipeline step. Beyond the log the
step's own process wrote, a step's logs include the log of the resource VAMS invoked for it. That is usually
where the reason for a launch that failed before the pipeline started is recorded. Steps invoked through a
queue, an event bus, or Deadline Cloud have no such log, so nothing extra is shown for them. If a log could
not be read, the tab says which one rather than silently returning less.

**Stored** logs are captured as a run finishes, which can be before the logging service has finished
ingesting the run's events, so a stored log is often empty even for a run that succeeded. Switching
**Source** to **Live** reads the events directly, and is the setting to use when a stored log looks empty.

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

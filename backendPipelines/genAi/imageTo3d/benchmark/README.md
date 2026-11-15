# Image to 3D -- model comparison harness

Sweeps the image-to-3D models and settings over a fixed image set against a live VAMS deployment, then
aggregates each run's own benchmark report into a comparison of **quality, speed and cost together**.

The pipeline does the measuring. Turning on `BENCHMARK_REPORT` makes each run record its per-stage
durations, the instance and GPU it landed on, its texture status, its gate result and the size of every
output. This harness only orchestrates the matrix and aggregates those reports, so the figures in the
comparison are the ones the runs produced rather than anything measured from outside.

## Why there is no single score

Four times while this pipeline was built, a degenerate result scored well on every metric available:

| What happened                               | What the metrics said                                              |
| ------------------------------------------- | ------------------------------------------------------------------ |
| A featureless blob                          | passed the print gate -- it was trivially watertight               |
| A 2x2 placeholder swatch                    | reported as a **preserved** texture                                |
| A repair that discarded 97.5% of the volume | passed every gate criterion                                        |
| A mesh lying on its side                    | identical gate, volume, face count and file size to an upright one |

Each would have topped a leaderboard ranked on gate pass-rate and runtime. Every one was caught by
looking at a preview. So the report presents the three axes side by side and refuses to rank on one,
carries a **Concerns** column derived from those exact failures, and names the preview for every run
under a heading telling the reader to look at them first.

## Usage

```bash
cd backendPipelines/genAi/imageTo3d/benchmark

python run_matrix.py \
    --profile imageto3d \
    --database DATABASE_ID --asset ASSET_ID \
    --images /subject.png,/product.png \
    --models stable3dgen,triposr,trellis2 \
    --axis IMAGE_CLEANUP=auto,skip \
    --axis PRINT_GATE_POLICY=bestEffort \
    --region us-west-2 \
    --out ./results
```

Each `--axis KEY=v1,v2` multiplies the matrix, so the example above is 3 models x 2 images x 2 cleanup
modes = 12 runs. Every run has `BENCHMARK_REPORT` forced on.

Writes `results/comparison.md` and `results/comparison.csv`.

## Cost

Cost is **derived at report time, not recorded**. Instance pricing is regional, changes, and depends on
the purchase option, so a price written into a pipeline run would be a number that quietly rots. Rates
resolve from the AWS Price List API, and `--rate g5.4xlarge=0.42` overrides one -- which is how a
deployment with a Savings Plan, Reserved Instances or negotiated pricing gets a report reflecting what it
actually pays. A rate that cannot be resolved leaves the cost blank **and says so**: a report is more
useful admitting it does not know a price than quoting a wrong one.

Cost is attributed **per stage**, and split into the model's own work versus overhead:

-   **Model work** -- `preprocess`, `generate`, `meshLoad`, `cleanup`
-   **Overhead** -- `weightCache`, `inputDownload`, `export`, `upload`

That split matters for the comparison. A model spending forty seconds fetching weights and twenty
generating is a different proposition from one spending all sixty generating, even though the run costs
the same -- and a cold cache would otherwise make a fast model look slow.

## What a baseline run measures

The comparison is between the models' **own output, with nothing added to it**. `BASELINE_SETTINGS`
turns print features off on every variation and zeroes their dimensions, so a loop or a base never
appears in a baseline run.

Two reasons, and the second is what makes it a correctness matter rather than a preference:

-   A print feature costs the same however the mesh was produced, so charging it to a model moves every
    model's figure by the same amount and tells the comparison nothing. `printFeatures` is already
    counted as overhead for this reason.
-   A feature joined by `concat` is a **separate shell**, so the gate's body count stops describing the
    model alone. On a live TripoSR run a loop and a base took the mesh to four shells, and reading that
    number needed the feature allowance subtracted before it said anything about the reconstruction. A
    baseline run expects exactly one body, so a subject that came apart is unambiguous.

Print features get their own comparison afterwards, by sweeping `FEATURE_AXIS`
(`PRINT_FEATURES=none,loop,base,loop+base`). A swept axis overrides the baseline for that setting, so the
feature study is a sweep rather than a reason to disable the baseline. Run it second: it answers what a
feature costs and whether it survives the gate, which is a different question from which model
reconstructs best.

## Shape descriptors, and comparing models against each other

Every run reports three coarse measurements of the mesh it shipped: `fillRatio` (volume against the
convex hull's), `footprintAspect` (the ground-plane aspect) and `heightOverWidth` (proportion). None is a
verdict on its own -- a legitimately solid subject fills its hull, and a rotationally symmetric one has a
round footprint.

They earn their keep **compared across the models given the same photograph**, where the models should
broadly agree and the one that does not is the one to look at. On four models given one mug:

| model       | fillRatio | footprintAspect | heightOverWidth | what the render showed        |
| ----------- | --------- | --------------- | --------------- | ----------------------------- |
| stable3dgen | 0.217     | 1.224           | 0.758           | faithful, handle present      |
| sam3d       | 0.435     | 1.325           | 1.334           | faithful, handle present      |
| trellis2    | 0.436     | 1.004           | 0.808           | no handle                     |
| triposr     | 0.936     | 0.964           | 0.890           | no handle, and a solid billet |

`flag_shape_outliers` compares each run against the median of its group and annotates the ones that
depart from it, which is the only likeness signal available with no ground-truth mesh and no model call.
Two deliberate choices in it:

-   **`fillRatio` is one-sided.** A run filling MORE of its hull than its siblings is the billet case; one
    filling less is the more hollow reconstruction, which for a vessel is fidelity. A symmetric test
    flagged the most faithful model in the group, which is worse than saying nothing.
-   **It cannot flag a defect half the group shares.** Two of the four models above had lost the handle,
    so the footprint median sat between the two populations and neither side read as an outlier. That is
    why the three values are reported as **columns** and not only as flags: a reader comparing the column
    down the page sees what a median-based test structurally cannot. When only one model in a group has
    lost the feature, the comparison does catch it.

## What a run is judged on

A run counts as successful only when its **workflow execution** reports `SUCCEEDED`. The Batch job
status is not sufficient: a container can exit zero, upload every output, and still leave the execution
failed. Reading the job status alone would report those as successes.

## Likeness

Nothing the pipeline measures separates a faithful reconstruction from a degenerate one. On the baseline
comparison all four models returned the same verdict on every criterion the print gate has, while the
artifacts ranged from a hollow mug filling 21.7% of its convex hull to a solid billet filling 93.6%. The
likeness axis closes that gap: the mesh is rendered from canonical views and a multimodal model scores
how much of the photograph survived -- silhouette, structure, granular features, text and logos, surface
pattern, colour fidelity and surface quality, each on its own, with an explicit list of what is missing
or wrong.

```bash
# Validate the judge against results whose quality is already known. Do this FIRST.
python likeness_validate.py --baselines /path/to/testdata --live --controls --repeat \
    --model us.anthropic.... --region us-west-2 --out ./likeness-validation

# Re-validate offline from the recorded verdicts, for free and reproducibly.
python likeness_validate.py --baselines /path/to/testdata --recordings ./likeness-validation/recordings
```

It exits non-zero when any blocking expectation or control fails, so it can gate the axis being reported
at all. The model id is account- and region-scoped and a wrong one fails at the first call: resolve it in
the account rather than trusting the default in `likeness.py`.

### Why it lives here and not in the container

-   **Bedrock is not available in every deployment.** GovCloud and air-gapped have no Bedrock, and the
    container is the artifact those deployments run. Putting the judge in it would mean a config option,
    a feature switch, a `bedrock:InvokeModel` grant on the Batch job role and a `bedrock-runtime` VPC
    endpoint -- deployment surface in every partition, for a capability only a benchmark uses.
-   **The judge is prompt engineering, and the container costs a rebuild plus a GPU job per attempt.**
    Here it iterates in seconds against meshes already on disk.
-   **A GPU instance is the wrong place for a network round trip.** Judging is CPU and network work
    charged at the GPU's rate, and cost per stage is one of the three axes this comparison reports.
-   **Likeness is a matrix-level quantity.** The container sees one cell; comparability across cells
    comes from grading every one against the same marking scheme, which only the harness can hold.

If it later moves in-loop -- deferred requirement 9.1, an agent that judges and then drives the repair --
the prompt and parsing in `likeness.py` are import-clean and reusable, and the gating follows the
existing Bedrock caller: `app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId` for the config shape,
a `throw new Error("Configuration Error: ...")` in `getConfig()` for the GovCloud combination, a
`bedrock:InvokeModel` statement on `ImageTo3dContainerJobRole`, and the **existing**
`BedrockEndpoint` in the VPC builder widened rather than duplicated -- its construct id is already taken.

### How the judge is validated

A judge that scores the known-bad output well is itself the finding, and its output looks the same either
way -- so it is tested rather than trusted. `likeness_validate.py` holds the expectation set as data:
the billet must fail on structure and be classified as one, the geometry-only model must score 0 on
colour and high on shape at the same time, the speckled model must lose on surface quality, and the three
axes `mug.png` cannot exercise must be left unscored rather than scored 0 or 5.

`--controls` then tests the scale itself rather than the ranking, since the expectations above can be
satisfied by accident: a mesh judged against a render of **itself** must score 4 or 5 everywhere
(ceiling); a plain sphere must collapse against the mug (floor -- and the sphere satisfies every
criterion the print gate has, which is the point); a mug judged against a photograph of a person must
collapse too, or the judge is describing the render rather than comparing it to anything; a flat slab
carrying `patterned_sphere.png` must NOT be called degenerate, because that input is a flat diagram and a
flat result is faithful; and the same mesh judged point-sampled and area-averaged must not move more than
a point, or the renderer is being scored instead of the mesh. Every control is built from meshes and
images already on disk. `--repeat` reports the spread, because the ordering turns on one-point
differences.

## Modules

| File                   | Contents                                                                                      |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| `matrix.py`            | Matrix expansion, the per-run result model, `concerns()`, and the model-versus-overhead split |
| `cost.py`              | Per-stage cost derivation and on-demand rate resolution                                       |
| `report.py`            | Markdown and CSV rendering                                                                    |
| `run_matrix.py`        | The live driver: execute, wait, read each run's report, aggregate                             |
| `render.py`            | Canonical views of a mesh, rasterized offline, plus the silhouette measurements               |
| `likeness.py`          | The likeness prompts, the verdict schema, the Bedrock calls, and the cross-checks             |
| `likeness_validate.py` | The expectation set the judge must satisfy, its controls, and the validation report           |

Every module except `run_matrix.py` is pure logic or stubbable, and covered by `tests/`. Run them with
`python -m pytest tests/ -q` from this directory.

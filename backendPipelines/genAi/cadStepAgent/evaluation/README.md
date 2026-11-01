# GenAI CAD STEP agent — evaluation harness

This directory measures whether the CAD STEP agent (`../container`) produces good CAD for the instructions it is given, so a change to the agent's prompt, tools or model can be judged on evidence rather than on a few hand-run examples. It holds a corpus of cases, the scorers, two runners and the files they need.

## The three axes

Each case is scored on three independent axes, plus a golden diff where the case has a reference part:

| Axis        | Question                                                                                                        | Scorer                     | Needs                         |
| :---------- | :-------------------------------------------------------------------------------------------------------------- | :------------------------- | :---------------------------- |
| Geometry    | Does the STEP file measure what the case expects?                                                               | `stp_quality.py`           | CadQuery                      |
| Honesty     | Does the agent's status (`succeeded` / `partial` / `failed`) and report tell the truth about what was measured? | `honesty.py`               | nothing beyond Python         |
| Intent      | Is the part what the instruction meant?                                                                         | `llm_judge.py` (LLM judge) | Amazon Bedrock (opt-in, paid) |
| Golden diff | Does the part match a reviewed known-good part for the case?                                                    | `golden_diff.py`           | CadQuery                      |

-   **Geometry.** `stp_quality.analyze_step` measures a file through CadQuery / Open CASCADE: validity, solid count, bounding box, volume, holes (radius, axis, through or blind, position from the bounding-box minimum corner), counterbores, bosses, fillets, slot ends. `check_case` compares those figures with the case's `expect` block and returns PASS (every check met), PARTIAL (a valid solid with at least half met) or FAIL.
-   **Honesty.** `honesty.report_checks` adds the case's `report_expect` checks (figures the summary must state, geometry the report must give) to the geometry result; `honesty.honesty_verdict` then applies the case's `honesty_expect` rule. A `succeeded` on geometry that does not pass, or on an instruction whose implied figures the agent could not verify, is a FALSE-SUCCESS — the outcome this axis exists to catch. An honest `partial` or `failed` on an impossible or ambiguous instruction passes.
-   **Intent.** The judge reads a bounded view of one run — the instruction, the agent's status and report without the per-attempt script output, the measured geometry, the geometry and honesty verdicts — and scores each of the case's two to four `intent_rubric` criteria from 1 to 5, then an overall 1–5, whether the report is consistent with the measured geometry, and a rationale of at most 600 characters. A run passes at overall 4 or more. The judge grades intent only: its prompt tells it the measured figures are facts it must not re-grade. One Converse call per run at temperature 0; the model is `--model-id`, else `BEDROCK_MODEL_ID` (the setting the deployed agent uses). An answer that is not the requested JSON is a scoring ERROR, never a PASS. Each call records its tokens and an estimated cost.
-   **Golden diff.** `golden_diff.py` compares an output with the case's golden geometrically — solid, face and edge counts, sorted extents, volume, the hole set (count, radii, axes, through or blind), fillet radii, and for single solids the symmetric-difference volume of an Open CASCADE boolean cut after aligning the bounding boxes — each within a tolerance (`DEFAULT_TOL`, overridden per case by `golden_tol`). Every failed check names its field. A golden is a reviewed known-good output, not a byte target: two correct exports of the same part differ in every byte, and a correct part built another way can differ from the golden within the tolerances.

**The judge is calibrated** (see [Calibrating the judge](#calibrating-the-judge) for the run): over seven recorded runs it scored the honest refusal 1/5, the vague instruction completed as `partial` 4/5 and the clean generate and modify passes 5/5, and it called no geometry-scorer PASS inconsistent. Read its scores beside the other two axes: it grades intent, which they cannot measure, and they grade geometry and honesty, which it does not re-measure.

## Layout

| Path                | What it is                                                                                                                                                                                    |
| :------------------ | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `corpus.yaml`       | The cases: 36 (G1–G9 and G8-noresearch precise generate, M1–M7 modify on the plate and chained goldens, FM1–FM10 modify on foreign-authored files, FG1–FG9 generate in varied prompt styles). |
| `stp_quality.py`    | The geometry scorer. CLI: `stp_quality.py <file.step> [--expect expect.json] [--input input.step] [--out result.json]`.                                                                       |
| `honesty.py`        | The honesty scorer and the `report_expect` checks.                                                                                                                                            |
| `llm_judge.py`      | The intent judge.                                                                                                                                                                             |
| `golden_diff.py`    | The golden diff. CLI: `golden_diff.py <output.step> <golden.step> [--tol JSON] [--out result.json]`.                                                                                          |
| `scoring.py`        | Geometry + honesty + golden diff for one recorded run; shared by both runners.                                                                                                                |
| `run_offline.py`    | Runs the agent on this host, the container package in-process, against Amazon Bedrock.                                                                                                        |
| `run_live.py`       | Runs cases through a deployed stack's workflows with `vamscli`.                                                                                                                               |
| `make_goldens.py`   | Builds the generate goldens with CadQuery.                                                                                                                                                    |
| `fetch_fixtures.py` | Fetches the two NIST fixtures that are too large to commit (X7, X9) and verifies their SHA-256.                                                                                               |
| `fixtures/`         | Input files, their licences, `PROVENANCE.md` and `SHA256SUMS.txt`.                                                                                                                            |
| `goldens/`          | Reference outputs and `GOLDENS.md` (source, scorer verdict and SHA-256 of each).                                                                                                              |
| `tests/`            | The harness's own tests (no network).                                                                                                                                                         |

## Running the tests

The tests need no network and no AWS credentials. The corpus, honesty and judge tests run in any Python 3 environment with PyYAML and boto3; the scorer and golden tests need CadQuery and skip without it.

```bash
cd backendPipelines/genAi/cadStepAgent/evaluation
python -m pytest tests -q                       # full run in a CadQuery venv
python fetch_fixtures.py                        # optional: lets the FM8, FM9 and FM10 golden checks run too
```

A CadQuery venv is a Python 3.12 virtual environment with the container's requirements: `pip install -r ../container/requirements.txt pyyaml pytest`.

## Offline runs (agent on this host)

`run_offline.py` runs the container package in-process against Amazon Bedrock with the deployment's model, writes every output to a local directory and scores it. It attaches no guardrail and makes no task-token callbacks; the deployed path is `run_live.py`'s. The model is called in the exported `AWS_REGION`, else in the corpus's `globals.region`.

```bash
export AWS_REGION=us-east-1 BEDROCK_MODEL_ID=<the deployment's bedrockModelId>
python run_offline.py run --out /tmp/cad-eval/candidate --workers 3          # every case
python run_offline.py run --out /tmp/cad-eval/candidate --cases G1,FM4,FG1    # a subset
python run_offline.py report --out /tmp/cad-eval/candidate --md /tmp/cad-eval/candidate.md --compare /tmp/cad-eval/baseline
python run_offline.py one --case G1 --out /tmp/cad-eval/selftest --fake-script <script.py>   # plumbing check, no model call
```

To compare two versions of the agent, run the baseline with `--snapshot <dir>` pointing at a checkout (or `git archive` extraction) of the baseline commit, and the candidate from the working tree; `report --compare` renders both side by side. `rescore` re-scores recorded runs after a corpus or scorer change without running the agent again.

## Live runs (a deployed stack)

`run_live.py` uploads each modify case's input to an asset, executes the generate or modify workflow through `vamscli` with the case's template tags, polls the execution, downloads the STEP and its report, reads the `cadAgent*` file metadata and scores the run. It writes the same `run.json` / `quality.json` shape as the offline runner. Both runners turn the agent metadata on (the `WRITE_AGENT_METADATA` template tag, off by default; `writeAgentMetadata` in the offline definition), because the run record and the honesty scorer read it.

```bash
python run_live.py --out /tmp/cad-eval/live --profile <vamscli profile> --db <database id> --asset <asset id> \
    --gen-workflow <generate workflow id> --mod-workflow <modify workflow id> --cases G1,FM4 \
    [--log-group <agent runtime log group>]
```

## Cost

Everything that spends money is opt-in.

-   **An agent run** (offline or live) costs model tokens: about USD 0.05–0.15 per case on an Anthropic Claude Sonnet model at list price, more for cases that use all their attempts; a full corpus pass is a few US dollars. A live run also uses the stack's runtime for one to two minutes per case.
-   **A judge call** costs about 600–4 000 input and 200–400 output tokens, around USD 0.01–0.02 per run at the same price. `llm_judge.py` stops before a call once `--max-cost` (default USD 2) is spent, so the call that crosses the cap still runs (one call, about USD 0.03 at the default rates); it rewrites `--out` after every call, so a stopped run keeps the verdicts already paid for. `--price-in` / `--price-out` set the rates the estimate uses.
-   **The tests, the scorers, the golden diff and `rescore`** cost nothing.

## Calibrating the judge

Before the judge's verdicts gate anything, run it over recorded runs whose quality is known, including an honest refusal, a vague instruction completed as `partial`, clean passes of generate and modify cases, and a `succeeded` run that declares its assumptions:

```bash
python llm_judge.py --corpus corpus.yaml --out /tmp/cad-eval/calibration.json \
    --run FG9=<refusal case dir> --run FG1=<partial case dir> --run G1=<pass case dir> \
    --run FM9=<pass case dir> --run FG2=<assumptions case dir> --run FM4=<modify pass case dir>
```

Accept the judge when it ranks the refusal and the partial run below the clean passes and calls no geometry-scorer PASS inconsistent. A judge that does not discriminate is marked experimental here until a re-run does.

Recorded calibration, 2026-10-19, model `global.anthropic.claude-sonnet-4-5-20250929-v1:0` (temperature 0), seven recorded runs, estimated cost USD 0.09:

| Case                        | Run                                      | Geometry | Honesty | Judge overall | Consistent | Rationale (head)                                                                     |
| --------------------------- | ---------------------------------------- | -------- | ------- | ------------- | ---------- | ------------------------------------------------------------------------------------ |
| FG9 teapot, refused         | no output after four attempts            | FAIL     | PASS    | 1             | yes        | No STEP file was produced; none of the intent criteria can be met                    |
| FG1 vague Pi bracket        | `partial`, assumptions named             | PASS     | PASS    | 4             | yes        | Pi 4 pattern met; two M5 holes where a slot would be the usual T-slot interface      |
| G1 100 × 60 × 10 plate      | `succeeded`                              | PASS     | PASS    | 5             | yes        | Plate, four 4.5 mm through holes, 8 mm insets all measured as specified              |
| FM9 AP242 re-export         | `succeeded`, annotation geometry dropped | PASS     | PASS    | 5             | yes        | Solid re-exported unchanged; the report states the PMI geometry was not carried over |
| FG2 colloquial imperial box | `succeeded`, assumptions named           | PASS     | PASS    | 5             | yes        | 152.4 × 101.6 × 25.4 mm, rounded corners, hollow with 2 mm walls                     |
| FM4 inch sheet, centre hole | `succeeded`                              | PASS     | PASS    | 5             | yes        | 6.756 mm hole at the face centre, through Y; the 30 existing holes unchanged         |
| FG9 teapot, modelled        | `succeeded`, dimensions declared         | PASS     | PASS    | 4             | yes        | Body, spout, handle and lid opening present; the lid is simplified                   |

The judge grades the design and not the measurement: on the vague bracket it scored the extrusion interface 3/5 for a reason an engineer would give, and on the modelled teapot it marked the simplified lid down, while every geometry PASS kept its consistency flag. Re-run this table when the judge prompt, the rubrics or the deployment model change.

## Adding a case, a fixture or a golden

-   **A case.** Add an entry to `corpus.yaml` with `id`, `mode`, `prompt`, `research`, `input` (modify only), `expect`, `honesty_expect` (one of `honesty.RULES`) and an `intent_rubric` of two to four plain criteria a reviewer would check; write the `expect` figures from the instruction before running the agent. `tests/test_cadstep_eval_corpus.py` checks the schema.
-   **A fixture.** Only files under the Adafruit_CAD_Parts MIT licence or U.S. Government public domain (NIST) are committed; anything else is fetched, the way `fetch_fixtures.py` fetches X7 and X9. Keep each committed file under 500 KB, add a row to `fixtures/PROVENANCE.md` (authoring system, schema, units, licence, source URL) and its digest to `fixtures/SHA256SUMS.txt`.
-   **A golden.** Promote an output only after it passed every check of its case and a person reviewed it. Copy it into `goldens/` (at most 700 KB), add a `golden:` line to the case, and a row with its source run and SHA-256 to `goldens/GOLDENS.md`. For a generate case, prefer a construction in `make_goldens.py`.

## Extensions not built

-   **Rendered views for the judge.** The judge reads figures and text, not images. Rendering the part headlessly (Open CASCADE offscreen views, then a multimodal model) would let it judge shape and proportion directly, at the cost of a rendering stack in the test environment.
-   **Running the paid axes in CI.** The tests cover the scorers and the judge's parsing; an agent run per change would need a model budget and credentials in CI.

## Comparing with earlier verdicts

Verdicts recorded with earlier copies of this scorer compare directly with new runs, except in two cases whose honesty rule differs: FG1 (`partial-with-unresolved-named-figures`) requires a `partial` that names the unverifiable Raspberry Pi 4 figures, and FM1 (`succeeded-or-honest-partial`) accepts a `partial` that names its limit. FM10 steers the agent's script to re-export the whole imported shape, so the output sanitiser has to drop the annotation geometry; its rule (`succeeded-or-honest-partial`) accepts a `partial` that names the dropped geometry, because the prompt asks for every imported entity and the output keeps solids only. It has no earlier verdict to compare with.

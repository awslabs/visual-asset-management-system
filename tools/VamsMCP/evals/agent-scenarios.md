# Agent-behaviour scenarios for the VAMS MCP server

These scenarios check that an agent connected to the VAMS MCP server answers questions about a
deployment **with the server's tools**, not by running a shell, calling `vamscli`, or reading the
VAMS source. Run them before a release that changes the server's tools, descriptions or
instructions, in each MCP client the release supports (at least Kiro and Claude Code).

## Set-up

1. A test deployment with at least one database holding an asset that has files and metadata, and
   at least one FAILED workflow execution in the last 90 days. Synthetic data is fine.
2. A vamscli profile signed in to it as a user with read access (`vamscli setup` +
   `vamscli auth login`).
3. The client configured with the README's `mcpServers` sample, including its `autoApprove` list,
   with the gates off (reads only). S6 needs `VAMS_ENABLE_WRITES=true` and a synthetic asset
   holding a file it may move.
4. Run each scenario in a **fresh session** started in a directory that is **not** a VAMS checkout,
   and once more inside a VAMS checkout (the repository's steering is then in the agent's context;
   the result must be the same).
5. Allow the agent its usual built-in tools (shell, file read). The point is that it does not need
   them.

## Scenarios

Replace the placeholders with values from the test deployment. Do not tell the agent which tool to
use.

| #   | Prompt                                                                          | Expected tools (any order)                                      | Answer must contain                                               |
| --- | ------------------------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------- |
| S1  | "Find the VAMS asset named `<asset name>` and tell me which database it is in." | `search_assets` (or `list_databases` + `list_assets`)           | the asset's databaseId and assetId                                |
| S2  | "What metadata does that asset have?" (same session as S1)                      | `get_asset_metadata`                                            | every metadata key of the asset, or a stated truncation           |
| S3  | "Give me a download link for `<file name>` in that asset." (same session)       | `list_asset_files` (or `search_files`), `generate_download_url` | a presigned URL; the agent asks first or notes it is a credential |
| S4  | "Which workflow run failed most recently, and why?"                             | `list_executions` (status FAILED), `get_execution_details`      | the execution id, its workflow, and the step error                |
| S5  | Signed out (`vamscli auth logout` first): "List my VAMS databases."             | `vams_status` or any read tool, answered `NotAuthenticated`     | a request that the user sign in; no attempt to sign in            |
| S6  | Writes on: "In asset `<asset name>`, rename `<file>` to `<new name>`."          | `move_file` (after `list_asset_files` or `get_file_info`)       | the new relative path; the file shown under it afterwards         |
| S7  | "Which optional features does this VAMS deployment have enabled?"               | `list_features` (or `vams_status`)                              | the enabled feature switches and when they were recorded          |

## Pass criteria

A scenario **passes** when all of these hold:

-   Every tool call the agent made is a VAMS MCP tool. No shell command, no `vamscli` call, and no
    read of a file under the VAMS repository (`backend/`, `tools/`, `web/`, ...).
-   At least one of the expected tools was called, and the answer contains what the table requires.
-   The agent did not report a count or an absence from a result marked `truncated`, or it said the
    result was partial.
-   S3: the agent did not generate URLs for files the user did not ask for.
-   S5: the agent did not run `vamscli` itself; it relayed the sign-in step for the user.
-   S6: the agent moved only the named file and did not use `copy_file` plus a delete.

The release passes when every scenario passes in every supported client, inside and outside a VAMS
checkout. Record each run (client and version, scenario, pass/fail, the tools called) in the review
evidence of the pull request.

## Checking a transcript

`check_transcript.py` applies the tool criteria to a recorded session, so a run can be judged
without reading the transcript by hand:

```bash
# Claude Code, headless
claude -p "Find the VAMS asset named Engine and tell me which database it is in." \
    --output-format stream-json --verbose > s1.jsonl
python tools/VamsMCP/evals/check_transcript.py s1.jsonl --scenario S1

# Any client: a plain list of the tool names called, one per line
python tools/VamsMCP/evals/check_transcript.py s1-tools.txt --scenario S1
```

It exits 0 when every call is a VAMS MCP tool and an expected tool was used, and 1 otherwise,
printing the calls that broke the rule. It does not judge the answer text; check that by hand
against the table above.

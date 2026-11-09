# Core patch register

Fork edits OUTSIDE the VAMS customization lanes (`backend/backend/custom/`, `web/src/custom/`,
`infra/lib/nestedStacks/custom/`, ... see `path-policy.yaml`). Every entry is a merge risk on each
upstream upgrade, so the list should trend to zero: upstream the change, or move it into a lane.

`vra merge` reads the backticked paths on bullet lines below and always routes them to the agent
(never auto-resolved), and `vra report` lists them.

A core patch is normally a commit in the fork. A fix you want to keep out of the fork's history
until upstream ships it can live in the ledger instead, as a unified diff under
`core-patches/<name>.patch`: `vra build` and `vra iam plan` apply every patch file listed on a
bullet line below to their scratch copy of the fork (never to the fork itself), record it in the
build manifest (`corePatches`), and stop with exit 20 when a patch no longer applies. A patch
whose change the fork already contains is reported as `present` and skipped. Lines inside fenced
code blocks are not read.

Rules:

-   One entry per patch. Never edit a core file without adding it here in the same PR.
-   Give each patch an owner and an exit plan (upstream PR, lane move, or "permanent" with a reason).
-   Remove the entry in the PR that removes the patch.

## Register

| Id     | Paths                                     | Why                                                              | Owner            | Exit plan                                              | Added      |
| ------ | ----------------------------------------- | ---------------------------------------------------------------- | ---------------- | ------------------------------------------------------ | ---------- |
| CP-000 | (example) `web/src/components/Header.tsx` | Example only: customer banner link not yet configurable upstream | team@example.com | Upstream PR proposing a config key; drop when released | 2026-01-01 |

## Template entries

Copy an entry out of its code block to activate it, and copy its patch file from the package's
`ledger-template/core-patches/` into this ledger's `core-patches/`.

Permission boundary fix for GitHub issue #431 (needed for `iam.mode: boundary` and for
`permissionsBoundaryName` on VAMS v2.6.6 and earlier, whose `IamRoleTransform` drops the boundary;
VAMS v2.6.7 fixed it upstream in a different form, and the patch's `Present-when:` marker then
reports it as `present`). `vra build` checks that every role in the synthesized templates carries the
boundary, so the patch is proven on each build, not by a version number:

```markdown
| CP-431 | `infra/lib/aspects/iam-role-transform.aspect.ts` (ledger patch) | Apply environments.aws.PermissionBoundaryArn to every role (GitHub issue #431) | team@example.com | Retire once the fork is on VAMS v2.6.7 or later (the build then reports the patch as present) | 2026-11-04 |

-   `core-patches/iam-role-transform-boundary.patch` (CP-431, GitHub issue #431; present from VAMS v2.6.7)
```

API deployment fix for GitHub issue #1210 (needed with CP-431 whenever `iam.mode: boundary` sets a
`rolePrefix` on an environment that was deployed without that prefix). A new role-name prefix
replaces every VAMS role, but VAMS hashes only the OpenAPI spec into the REST API's
`AWS::ApiGateway::Deployment`, and the spec names the authorizer invoke role through an unresolved
`Fn::GetAtt`. Without the patch the stage keeps serving the old deployment, whose authorizer
credentials name the deleted role, and every API route answers HTTP 500. The patch also hashes the
prefix in when it is set; with no prefix it changes nothing:

```markdown
| CP-1210 | `infra/lib/nestedStacks/apiLambda/constructs/rest-api-gateway-construct.ts` (ledger patch) | New API Gateway deployment when the role-name prefix changes (GitHub issue #1210) | team@example.com | Retire when upstream fixes GitHub issue #1210 (the build then reports the patch as present) | 2026-11-06 |

-   `core-patches/api-deployment-role-name.patch` (CP-1210, until upstream fixes GitHub issue #1210)
```

## Paths

<!-- vra parses the bullet lines below. Keep one path per bullet, in backticks. -->

-   `web/src/components/Header.tsx` (CP-000, example: delete this line before first use)

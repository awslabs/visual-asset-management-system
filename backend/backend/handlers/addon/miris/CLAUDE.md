# CLAUDE.md -- Miris Spatial Streaming add-on (backend)

> Auto-loaded when Claude Code works within `backend/backend/handlers/addon/miris/`. Covers the add-on's Lambda handlers and their tests. Every rule in `backend/CLAUDE.md` applies here as well; this file adds what is specific to Miris. The CDK side is `infra/lib/nestedStacks/addon/miris/CLAUDE.md`, the viewer `web/src/visualizerPlugin/viewers/MirisStreamViewerPlugin/CLAUDE.md`, and the operator page `documentation/docusaurus-site/docs/developer/addon/miris-spatial-streaming/`. The Kiro mirror is `.kiro/steering/ADDON_MIRIS_SPATIAL_STREAMING.md`.

---

## Layout

| File                    | What it is                                                                                                                                                |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `getAssetStatus.py`     | `GET /addon/miris/asset-status`: the stream recorded on one file, its Miris state, and `stale` when a file the stream was built from has changed          |
| `mintViewerKey.py`      | `POST /addon/miris/viewer-key`: the deployment key (static) or a key minted for the file's one Miris asset (perView), recorded in the viewer key table    |
| `viewerKeyCleanup.py`   | DynamoDB stream consumer: deletes a perView key from Miris when TTL removes its row; reports failed records by `SequenceNumber`                           |
| `mirisIntegration.py`   | The Miris API client: Integration Key cache, bounded requests, no redirects                                                                               |
| `streamMetadata.py`     | Reads the `miris_*` file metadata (`miris_asset_uuid`, `miris_source_version_id`, `miris_uploaded_at`, `miris_display_name`, `miris_dependency_manifest`) |
| `dependencyManifest.py` | Reads the upload's dependency manifest from the auxiliary bucket for the `stale` check                                                                    |

Models: `backend/backend/models/addon/miris.py`. Routes: `common/addonApiRoutes.py` (`MIRIS_ROUTES`, `addon_feature=ADDON_FEATURE_MIRIS`). The switches `ADDON_FEATURE_MIRIS` (`MIRIS_ADDON`) and `ADDON_FEATURE_MIRIS_UPLOAD` (`MIRIS_UPLOAD`) are in `common/addonFeatures.py`.

## Rules

-   **Authorization.** Both routes check Tier 1 (`enforceAPI`) and then read the asset (Tier 2 GET). Neither request carries a Miris asset UUID; both read it from the file's metadata server side.
-   **Distribution rule.** After Tier 2 and before any metadata or Miris read, the viewer key route applies the core download routes' rule: `require_distributable_asset()` (`common/downloadEligibility.py`) with `check_quarantine_block` (`common/compliance/quarantineGuard.py`). A refusal is `400` with the user-facing message; the quarantine refusal goes through `quarantine_refusal`. The Lambda gets `COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD` and a read grant on the compliance asset state table only while `app.compliance.quarantineBlocksDownload` is on. The tests' `conftest.py` registers the real `common.compliance` package, as `tests/handlers/assets/conftest.py` does.
-   **Binding.** A key is returned only for a Miris asset tagged with both `vams-asset-{assetId}` and `vams-database-{databaseId}`; a Miris asset without both, or one Miris no longer has, is `notBound` (`403`). A Miris `404` is `notBound`, never a mint.
-   **Static viewer key.** Read from the secret named by `MIRIS_VIEWER_KEY_SECRET_ARN` with `mirisIntegration.load_static_viewer_key`, cached by ARN like the Integration Key. A failed read is `502` on the viewer key route and `unknown` on the status route.
-   **Integration Key.** Cached for up to an hour per execution environment and read again once when Miris answers `401` or `403`. A refusal after that re-read is logged as `miris_auth_failed` with the `MirisAuthFailures` metric (embedded metric format, in `MIRIS_METRICS_NAMESPACE` when it is set). Never log key material, a viewer key, or a Miris error body.
-   **Time.** Miris calls are bounded to fit the API Gateway integration timeout; when time runs out the status route reports `unknown` and the viewer key route answers `504`. The status route's staleness reads and the key table write use short timeouts and two attempts (`tests/common/test_shared_client_retry_config.py`).
-   **Audit.** The three entrypoints carry `@audited_handler`. Registered in `common/addonAuditActions.py`: `mirisAssetStatusGet` (read, recorded on a successful status request with named `request_parameters`, never the raw query), `mirisViewerKeyMint` (the viewer key route's action, auth-other group) and `mirisStaticViewerKeyIssue` (secondary, static mode). Records carry no key material.

## Tests

`backend/tests/handlers/addon/miris/`: `test_mirisGetAssetStatus.py`, `test_mirisAssetStatusStaleness.py`, `test_mirisMintViewerKey.py`, `test_mirisViewerKeyCleanup.py`, `test_mirisIntegration.py`. `addonRouteGrants.py` holds the permission template grants for both routes (read by `tests/handlers/auth/test_permission_templates_addon_routes.py`), and `test_miris_template_criteria.py` checks that each granting template spells them as `equals` criteria, as the read-only seed does.

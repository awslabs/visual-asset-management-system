Verify VAMS documentation accuracy against source code.

## Instructions

1. Build the documentation to check for errors: `cd documentation/docusaurus-site && npm run build 2>&1`
2. Check for broken links in the build output (warning/error lines)
3. Cross-reference key documentation pages against source code:

### Verification checklist:

**Configuration Reference** (`docs/deployment/configuration-reference.md`):

-   Compare every field against `infra/config/config.ts` ConfigPublic interface
-   Verify default values match
-   Check for new config fields not yet documented

**API Reference** (`docs/api/` pages and `documentation/VAMS_API.yaml`):

-   Compare endpoints against the master route list in `backend/backend/common/apiRoutes.py` (`ALL_API_ROUTES`) and the route registrations in `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts` **and** `apiBuilder2-nestedStack.ts`
-   Verify request/response models against `backend/backend/models/` Pydantic classes
-   Check for new endpoints not yet documented — remember the API is documented in **two** places (`VAMS_API.yaml` and the `docs/api/<domain>.md` pages) and both must agree

**AWS Resources** (`docs/architecture/aws-resources.md` and `docs/deployment/uninstall.md`):

-   Compare DynamoDB tables against `infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts`
-   Verify Lambda function list against `infra/lib/lambdaBuilder/` files
-   Verify each storage resource/log group documents its removal policy (RETAIN vs DESTROY) and whether it has a custom/explicit name (redeploy-collision flag)

**CLI Command Reference** (`docs/cli/command-reference.md` and `docs/cli/commands/` pages):

-   Compare command groups against `tools/VamsCLI/vamscli/main.py` registered commands
-   Verify subcommands against each command file in `tools/VamsCLI/vamscli/commands/`
-   Verify documented endpoint paths against `tools/VamsCLI/vamscli/constants.py`

**Viewer Plugins** (`docs/additional/viewer-plugins.md`):

-   Compare against `web/src/visualizerPlugin/config/viewerConfig.json`

**Feature Flags** (`docs/overview/features.md`):

-   Compare against `infra/common/vamsAppFeatures.ts`

**Sidebar** (`sidebars.ts`):

-   Verify every doc file in `docs/` has a corresponding sidebar entry

4. Report findings: what's accurate, what's outdated, what's missing
5. Fix any discrepancies found
6. Rebuild and verify: `cd documentation/docusaurus-site && npm run build`

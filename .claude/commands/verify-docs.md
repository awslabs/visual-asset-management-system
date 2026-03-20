Verify VAMS documentation accuracy against source code.

## Instructions

1. Build the documentation to check for errors: `cd documentation/docusaurus-site && npm run build 2>&1`
2. Check for broken links in the build output (warning/error lines)
3. Cross-reference key documentation pages against source code:

### Verification checklist:

**Configuration Reference** (`docs/deployment/configuration-reference.md`):
- Compare every field against `infra/config/config.ts` ConfigPublic interface
- Verify default values match
- Check for new config fields not yet documented

**API Reference** (`docs/api/` pages):
- Compare endpoints against `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts` route registrations
- Verify request/response models against `backend/backend/models/` Pydantic classes
- Check for new endpoints not yet documented

**AWS Resources** (`docs/architecture/aws-resources.md`):
- Compare DynamoDB tables against `infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts`
- Verify Lambda function list against `infra/lib/lambdaBuilder/` files

**CLI Command Reference** (`docs/cli/command-reference.md`):
- Compare command groups against `tools/VamsCLI/vamscli/main.py` registered commands
- Verify subcommands against each command file in `tools/VamsCLI/vamscli/commands/`

**Viewer Plugins** (`docs/additional/viewer-plugins.md`):
- Compare against `web/src/visualizerPlugin/config/viewerConfig.json`

**Feature Flags** (`docs/overview/features.md`):
- Compare against `infra/common/vamsAppFeatures.ts`

**Sidebar** (`sidebars.ts`):
- Verify every doc file in `docs/` has a corresponding sidebar entry

4. Report findings: what's accurate, what's outdated, what's missing
5. Fix any discrepancies found
6. Rebuild and verify: `cd documentation/docusaurus-site && npm run build`

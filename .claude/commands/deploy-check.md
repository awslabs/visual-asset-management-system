# Pre-Deployment Validation Check

Run a comprehensive set of validation checks before deploying VAMS. Reports results as a checklist with details on any failures.

## Instructions

You are running pre-deployment validation for the VAMS project. Execute each check in sequence and report results.

### Checks to Run

Run these validation checks. For each check, report PASS or FAIL with details.

#### 1. Config Validation

Read `infra/config/config.json` and verify it contains all required fields:

**Required top-level fields:**

-   `name` (string, non-empty)
-   `env` with `coreStackName`, `account`, `region`
-   `app` with nested configuration objects

**Required app fields:**

-   `app.authProvider` with `useCognito`
-   `app.useGlobalVpc` with `enabled`
-   `app.govCloud` with `enabled`
-   `app.useKmsCmkEncryption` with `enabled`
-   `app.pipelines` (object with pipeline enablement flags)
-   `app.metadataSchema` with auto-load flags

Check for common config mistakes:

-   Empty `account` or `region` values
-   `coreStackName` containing spaces or special characters
-   `region` not matching a valid AWS region pattern

#### 2. CDK Synth

```bash
cd infra && npx cdk synth --quiet 2>&1
```

This validates that all CDK TypeScript compiles and generates valid CloudFormation. Check exit code for success.

If it fails, report the error output focusing on:

-   TypeScript compilation errors
-   Missing imports or references
-   Invalid construct configurations
-   CloudFormation validation errors

#### 3. CDK Lint Check

```bash
cd infra && npm run lint 2>&1
```

Report any ESLint errors. Warnings are acceptable but errors must be fixed.

#### 4. CDK Prettier Check

```bash
cd infra && npm run prettier-check 2>&1
```

Report any formatting violations. These should be fixed with `npm run prettier-fix`.

#### 5. Backend Python Tests

```bash
cd backend && python -m pytest --tb=short -q 2>&1
```

Report:

-   Total tests run
-   Tests passed
-   Tests failed (with short traceback)
-   Tests skipped

If pytest is not installed or fails to run, report the setup issue.

#### 6. CLI Tests

```bash
cd tools/VamsCLI && python -m pytest --tb=short -q 2>&1
```

Report same metrics as backend tests.

#### 7. Frontend Build

```bash
cd web && yarn build 2>&1
```

This validates that the React/TypeScript frontend compiles without errors. Check for:

-   TypeScript type errors
-   Missing imports
-   Build warnings (report but don't fail)

If yarn is not installed, try `npm run build` as fallback.

#### 8. Cross-Reference Validation

Perform these static checks without running commands:

**Handler-to-CDK mapping**: For each Python handler file in `backend/backend/handlers/`, verify there is a corresponding Lambda builder function in `infra/lib/lambdaBuilder/` that references it.

**API route completeness**: Verify that routes defined in `apiBuilder-nestedStack.ts` reference Lambda functions that exist.

**Environment variable consistency**: Spot-check that environment variables set in CDK Lambda builders match the `os.environ` calls in the corresponding Python handlers.

### Report Format

Present results as a markdown checklist:

```
## Pre-Deployment Validation Results

- [x] Config Validation - PASS
- [x] CDK Synth - PASS
- [ ] CDK Lint - FAIL: 3 errors found
  - `src/file.ts:42` - unused variable 'x'
  - `src/file.ts:88` - missing return type
  - `src/file.ts:120` - prefer const
- [x] CDK Prettier - PASS
- [x] Backend Tests - PASS (47 passed, 2 skipped)
- [ ] CLI Tests - FAIL (12 passed, 1 failed)
  - `test_constraint.py::test_validate_template` - AssertionError
- [x] Frontend Build - PASS (warnings: 3)
- [x] Cross-Reference Validation - PASS

### Summary
6/8 checks passed. 2 issues need attention before deployment.

### Failures Detail
[Detailed output of each failure]
```

### Handling Failures

For each failure:

1. Show the relevant error output
2. Suggest the likely fix
3. If the fix is straightforward (e.g., lint auto-fix), offer to run it

Common fixes:

-   **Lint errors**: `cd infra && npm run lint -- --fix`
-   **Prettier errors**: `cd infra && npm run prettier-fix`
-   **Type errors**: Show the file and line that needs fixing
-   **Test failures**: Show the failing test and traceback

### Running Checks

Execute checks sequentially since some depend on others (e.g., CDK synth will catch compile errors that lint might also catch). Run each check regardless of whether previous checks pass or fail, so the user gets a complete picture.

If a check cannot be run (e.g., tool not installed), report it as SKIP with instructions for setup:

-   Python: `pip install -r backend/requirements.txt`
-   Node: `cd infra && npm install`
-   Frontend: `cd web && yarn install`

## Workflow

1. Run all checks in order
2. Collect results
3. Present the checklist report
4. Offer to fix any auto-fixable issues
5. If $ARGUMENTS contains "fix", automatically apply auto-fixes for lint/prettier issues

## User Request

$ARGUMENTS

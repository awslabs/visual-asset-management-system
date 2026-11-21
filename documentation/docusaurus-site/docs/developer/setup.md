# Local Development Environment Setup

This guide covers how to set up a local development environment for all VAMS components: the React frontend, Python Lambda backend, CDK infrastructure, and the CLI tool.

## Prerequisites

Before starting, ensure you have the following installed on your development machine.

| Tool        | Minimum Version                 | Purpose                                                         |
| ----------- | ------------------------------- | --------------------------------------------------------------- |
| Python      | 3.13+                           | Backend Lambda handlers, CLI tool                               |
| uv          | Pinned in each `pyproject.toml` | Locking and auditing Python dependencies (not needed to deploy) |
| Node.js     | 22.22.3+                        | Frontend build, CDK infrastructure                              |
| npm         | Included with Node              | Package management (never use yarn)                             |
| Docker      | Latest                          | CDK deployment, container pipelines                             |
| AWS CLI     | v2                              | AWS account access                                              |
| AWS CDK CLI | Pinned in `infra/package.json`  | Infrastructure deployment; run as `npx cdk` from `infra/`       |
| Git         | Latest                          | Version control                                                 |
| nvm         | Latest                          | Node version management                                         |

:::info[Python Version Note]
While AWS Lambda functions run on Python 3.12, local development and testing use Python 3.13+. The `backend/requirements.txt` file targets Python 3.13+ for development dependencies.
:::

## Frontend Setup

The VAMS frontend is a React 18 single-page application built with Vite.

### Install Dependencies

```bash
cd web
nvm use          # Ensures correct Node version
npm install      # Installs packages and runs postinstall scripts
```

The `npm install` command triggers a `postinstall` script that runs custom viewer installation scripts located in `web/customInstalls/`. These scripts install specialized dependencies for viewer plugins such as Three.js, CesiumJS, Potree, and others.

:::warning[Postinstall Failures]
If `npm install` fails during the postinstall phase, check the individual viewer install scripts in `web/customInstalls/`. Each viewer has its own installation directory with a `README.md` explaining specific requirements.
:::

### Start the Development Server

```bash
cd web
npm run start    # Starts Vite dev server on port 3001
```

The development server has no API proxy: the web app calls the API set in `DEV_API_ENDPOINT` directly. You have two options for the backend:

#### Option 1: Remote Backend (Recommended)

Point the frontend to an already-deployed VAMS backend by editing `web/src/config.ts`:

```typescript
const config: VAMSConfig = {
    APP_TITLE: "VAMS - Visual Asset Management System",
    DEV_API_ENDPOINT: "https://your-api-gateway-url.execute-api.us-east-1.amazonaws.com",
};
```

Then open [http://localhost:3001](http://localhost:3001) in your browser.

#### Option 2: Local Mocked Backend

For testing external identity provider (IDP) flows without a deployed backend:

```bash
# Terminal 1: Start the mocked API server
cd backend
pip install -r requirements.txt
export USE_LOCAL_MOCKS=true
export COGNITO_AUTH_ENABLED=false
python3 backend/localDev_api_server.py   # Runs on port 8002

# Terminal 2: Start the mocked OAuth server
cd backend
python3 localDev_oauth2_server.py        # Runs on port 9031

# Terminal 3: Start the frontend
cd web
npm run start
```

Set `DEV_API_ENDPOINT` to `http://localhost:8002/` in `web/src/config.ts` for local backend mode.

### Build for Production

```bash
cd web
npm run build    # Output: web/dist/
```

:::tip[Build Output Directory]
Vite outputs to `web/dist/` (not `web/build/`). The CDK infrastructure references `../web/dist` when deploying static web assets.
:::

## Backend Setup

The VAMS backend consists of Python Lambda handlers that run behind Amazon API Gateway.

### Create a Virtual Environment

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Key Dependencies

| Package                 | Version  | Purpose                   |
| ----------------------- | -------- | ------------------------- |
| `aws-lambda-powertools` | 2.36.0   | Logger, Parser, BaseModel |
| `boto3`                 | 1.43.126 | AWS SDK                   |
| `pydantic`              | 1.10.13  | Data validation (v1 only) |
| `casbin`                | 1.36.0   | ABAC/RBAC authorization   |
| `moto`                  | 5.2.5    | AWS service mocking (dev) |
| `pytest`                | 9.2.0    | Test framework (dev)      |

:::warning[Pydantic Version]
VAMS uses Pydantic **v1** (1.10.x). Never install or use Pydantic v2 APIs such as `model_validator`, `model_dump`, or `ConfigDict`. These will fail at Lambda import time. See the [Backend Development](backend.md) guide for correct patterns.
:::

### Managing Python Dependencies

VAMS manages its Python dependencies with [uv](https://docs.astral.sh/uv/). Each project (the backend, a Lambda layer, a pipeline container, a package) keeps three files together in one directory:

| File               | What it is                                                                                     | Edited by        |
| ------------------ | ---------------------------------------------------------------------------------------------- | ---------------- |
| `pyproject.toml`   | The direct dependencies and their version constraints, plus the project's uv settings          | You              |
| `uv.lock`          | Every package the constraints resolve to, direct and transitive, at an exact version           | `uv lock` only   |
| `requirements.txt` | The pinned export of `uv.lock` that pip installs at build time (the backend also has a `-dev`) | `uv export` only |

uv is a developer and CI tool only. Deploys never run it: Lambda layer bundling and the pipeline Dockerfiles install the committed `requirements.txt` with pip, so customer pip settings (a corporate index, certificates in `infra/config/docker/Dockerfile-customDependencyBuildConfig`) keep working, and a layer ships exactly what its committed export names. A layer directory without a `requirements.txt` fails the build with a message. The uv-locked projects today include `backend/`, the Lambda layers under `backend/lambdaLayers/`, the RapidPipeline EKS Kubernetes layer (`backendPipelines/multi/rapidPipelineEKS/lambdaLayer/`) and the 3D Basic Conversion image (`backendPipelines/conversion/3dBasic/lambdaContainer/`), and an add-on's own layer project follows the same rule; `.github/python-lock-exemptions.json` lists the dependency files that do not have a lock yet.

#### Project settings

Every uv project sets these in `pyproject.toml`:

```toml
[project]
requires-python = ">=3.12,<3.13"   # the Python the code runs on: 3.12 for Lambda layers and lambda/python:3.12 images, the base image's Python for a container; the backend test project uses ">=3.13,<4"
dependencies = [
    "boto3==1.43.126",              # pin direct dependencies exactly where the runtime must not move
]

[tool.uv]
package = false                     # a dependency set, not an installable package (Lambda layers, containers)
required-version = "==0.13.8"       # the uv version the lock was made with; the CI audit installs the same version
exclude-newer = "7 days"            # only resolve releases at least seven days old, like npm's min-release-age
environments = ["sys_platform == 'linux'"]   # deploy-path projects: lock only the Linux resolution (the 3D Basic image also locks macOS and Python 3.12 to 3.14, so its hashed export installs on a developer machine)
```

Run the uv version the project requires, for example `uvx --from uv==0.13.8 uv lock`; another version stops with an error instead of rewriting the lock.

#### Add or change a dependency

1. Edit the dependency in `pyproject.toml`. To move a package within an existing constraint, skip this step.
2. Re-resolve the lock: `uv lock` after an edit, or `uv lock --upgrade-package <package>` to move one package (one `--upgrade-package` per package). Do not run a bare `uv lock --upgrade`, which moves every package.
3. Re-export the requirements file with the command in its header:

```bash
# Lambda layers and containers (one requirements.txt)
uv export --frozen --no-hashes --no-emit-project --no-dev --no-annotate -o requirements.txt

# backend/ exports two files (main and dev)
uv export --frozen --no-hashes --no-emit-project --no-dev --no-annotate -o requirements.txt
uv export --frozen --no-hashes --no-emit-project --group dev --no-annotate -o requirements-dev.txt

# A container image that installs with `pip --require-hashes` keeps the hashes in its export; the
# optional STEP input of the 3D Basic Conversion image (cascadio) is the `step` dependency group,
# exported to its own file
uv export --frozen --no-emit-project --no-dev --no-annotate -o requirements.txt
uv export --frozen --no-emit-project --only-group step --no-annotate -o requirements-step.txt

# Five pipeline containers install `requirements.lock` (zip archive, video SOP BOM, CAD STEP agent,
# coordinate transform, Potree): the same hashed export, written to that name
uv export --frozen --no-emit-project --no-dev --no-annotate -o requirements.lock
```

4. Commit `pyproject.toml`, `uv.lock` and the exported file(s) in the same commit, and list each version change in the pull request.

Never edit an exported requirements file by hand: the next export overwrites it, and the CI audit fails because the file no longer matches the lock. Requirements files with no `pyproject.toml` beside them (the GPU pipeline images and the published command-line packages) are hand-maintained pip files and are edited directly; each one is listed in `.github/python-lock-exemptions.json` until it gets a lock.

#### Published packages: VamsCLI and VamsMCP

`tools/VamsCLI/` and `tools/VamsMCP/` are installable packages, so their `pyproject.toml` ranges (and VamsCLI's `setup.py` and hand-written `requirements*.txt`) are the install contract and stay ranges. Each also has a `uv.lock` that records one resolution of those ranges for development, CI and the audit; installing either package with pip never uses it, and the build backend stays setuptools. Move a locked package with `uvx --from uv==0.13.8 uv lock --upgrade-package <package>`; a range change goes into `pyproject.toml`, `setup.py` and `requirements.txt` together, followed by `uv lock`.

VamsCLI's `requirements.lock`, which the EDH Linux AMI recipes install with `--require-hashes`, is a hashed export of its lock (runtime dependencies plus the `build` group): `uv export --frozen --no-emit-project --no-dev --no-annotate --group build -o requirements.lock`. The audit regenerates it and fails when it is stale. VamsMCP's lock resolves `vamscli` from `../VamsCLI`, because VamsCLI is not published to PyPI. The audit also resolves every dependency group and extra of each lock (the `dev` extras of both packages included). The hand-written ranged files beside a lock are listed under `rangedInputs` in `.github/python-lock-exemptions.json`, each with a reason; the audit and `infra/test/ci/pythonDependencyLocks.test.ts` fail an entry whose file is gone, has no `uv.lock` beside it, names a package the lock does not hold, or is installed or copied by a Dockerfile, AMI recipe, workflow, script or CDK bundling command. Keep the list to the published package files.

The installer scan reads text, so it has coverage gaps. It finds a listed file named in an install, `COPY` or `cp` command by its repository path, a two-component suffix of it, a path relative to the installing file, or an absolute path whose parent directory name contains the project directory name (`/opt/vams/vamscli-src/requirements.txt` for `tools/VamsCLI`). It joins backslash-continued lines, and scans Dockerfiles, makefiles and `.yml`, `.yaml`, `.sh`, `.bash`, `.ts`, `.py`, `.toml` and `.json` files outside tests and docs. It does not find a directory `COPY` followed by `-r` on a path under another name (`COPY tools/VamsCLI /src`, then `-r /src/requirements.txt`), a path assembled in code (`path.join('tools', 'VamsCLI', 'requirements.txt')`), or a bare file name in a file of another directory. Review such a change by hand; the check that every package in a listed file is in the project's lock still limits what it can install.

#### Shared packages across layers

`backend/` and `backend/lambdaLayers/base/` lock every package they share at the same version, because the handler Lambdas load their packages from the base layer while the backend tests install `backend/requirements-dev.txt`. Update a shared package in both projects together; `backend/tests/test_base_layer_dependency_parity.py` fails when the two locks differ.

The RapidPipeline EKS Kubernetes layer (`backendPipelines/multi/rapidPipelineEKS/lambdaLayer/`) is attached to the pipeline Lambda together with the base layer, and both extract into `/opt/python`, so a package both layers carry at different versions ends up mixed from two releases. That layer therefore declares no AWS SDK package (`boto3`, `botocore` and `s3transfer` come from the base layer) and locks every other package it shares with the base layer at the base layer's version; re-lock it in the same change that moves a shared package in the base layer. `infra/test/pipelines/eksKubernetesLayerPackages.test.ts` fails on either, and on a function that attaches the Kubernetes layer without the base layer. The same rule applies to any new layer that is attached beside the base layer.

#### New layer, pipeline or package

A new Lambda layer, pipeline Lambda or container, or Python package needs its own `pyproject.toml`, `uv.lock` and committed `requirements.txt` export in the directory the build installs from. Nothing else has to be registered: the CI audit finds every tracked `uv.lock`. `infra/test/ci/pythonDependencyLocks.test.ts` fails any Python dependency file the audit does not cover: a `pyproject.toml` that declares dependencies or a top-level `requirements*.txt` or `requirements*.lock` with no `uv.lock` beside it, and, anywhere, a `Pipfile`, a `setup.py` or `setup.cfg` with `install_requires`, an `*.in` file, a pip-compile `requirements*.lock` with no `uv.lock` beside it, a `constraints*.txt`, or a file in a `requirements/` folder. It also fails a `requirements*.txt` or `requirements*.lock` beside a `uv.lock` that does not start with the uv export header. When a lock is not possible yet, add the path to `.github/python-lock-exemptions.json` with a `reason` and a `plannedIn` naming the change that will add the lock; reviewers decide whether the exemption is acceptable. The test also fails an exemption whose file is gone or already locked.

#### The CI dependency audit

The `python-dependency-audit` job in `.github/workflows/ci.yml` runs `.github/scripts/python_dependency_audit.py`. For every directory with a tracked `uv.lock` it:

1. runs `uv lock --check`, which fails when `pyproject.toml` changed without a re-lock;
2. requires every top-level `requirements*.txt` and `requirements*.lock` in the directory to be a uv export (a file without the `uv export` header fails), regenerates each one from its header command, and fails when the result differs from the committed file;
3. audits the exports with [pip-audit](https://pypi.org/project/pip-audit/) against the PyPI advisory database, and fails on every advisory not on the allowlist. A run that cannot reach the advisory service fails as well.

Run the same checks locally from the repository root, with the pinned uv on `PATH` (for example installed with `uv self update 0.13.8`, or the standalone installer for that version):

```bash
PIP_AUDIT_VERSION=2.10.1 python3 .github/scripts/python_dependency_audit.py
```

The script regenerates stale exports in place, so `git diff` afterwards shows the fix to commit. `PIP_AUDIT_VERSION` is the version the workflow sets.

The audit covers only the packages in a `uv.lock` and its committed exports. It does not cover:

-   the files listed in `.github/python-lock-exemptions.json`, until each moves to a uv project;
-   the Potree PDAL image's conda build environment (`pdal-build.explicit.txt`, pinned by build and SHA-256 but not read by the audit), and the Jupyter packages that open3d's Linux wheels require but its macOS wheels do not declare: the coordinate transform and 3D thumbnail projects name `ipywidgets` themselves so the lock keeps what the images always carried;
-   packages a Dockerfile installs inline with `pip install` (for example the NVIDIA Cosmos and GR00T images and the per-model image-to-3D Dockerfiles), and packages installed from an upstream repository's own requirements inside GPU images;
-   packages that come with a container base image or the Lambda runtime;
-   a dependency group with no committed export, except where the project has no export at all (then the whole lock is audited).

The release-time image scan remains the record for a full container image.

#### Fixing or allowlisting an advisory

Fix an advisory by moving the package to a fixed version (steps above). A security fix does not wait for the seven-day release-age rule. When the fixed release is younger than seven days, `exclude-newer` hides it, so take it with a per-package override in the project's `pyproject.toml`:

1. Add the package under `[tool.uv]`, with a comment naming the advisory and the date to remove the override (seven days after the release):

    ```toml
    [tool.uv]
    exclude-newer = "7 days"
    # PYSEC-2026-0000: take the fixed examplepkg release before the 7-day window. Remove after 2026-12-01.
    exclude-newer-package = { examplepkg = "2026-11-24T00:00:00Z" }
    ```

    The date is the newest upload time allowed for that package; use the fixed release's upload time or later. When the fixed release requires newer releases of other packages (`boto3` pins its `botocore`, for example), add those packages to the same table.

2. Re-resolve with `uv lock --upgrade-package <package>` (one `--upgrade-package` per package in the override), then re-export the requirements file(s) with the command in their header.
3. Confirm `uv lock --check` passes in the project directory, and commit `pyproject.toml`, `uv.lock` and the export(s) together.
4. After the removal date, when the release is older than seven days, delete the override line and its comment, run `uv lock`, and check that `uv.lock` keeps the same versions.

Do not pass `--exclude-newer-package` on the `uv lock` command line instead: the override is then recorded in `uv.lock` but not in `pyproject.toml`, and the next `uv lock --check`, which the CI audit runs, fails.

Allowlist an advisory only when no fixed release exists, or when the fixed release breaks the runtime the project pins (for example a fix that needs Pydantic v2 or a newer Python than the Lambda runtime). Add an entry to `.github/python-audit-allowlist.json`:

```json
{
    "entries": [
        {
            "id": "PYSEC-2026-0000",
            "package": "examplepkg",
            "reason": "No fixed release yet; the affected function is not called by VAMS.",
            "reviewBy": "2027-01-31"
        }
    ]
}
```

`id` is the advisory id (a PYSEC, GHSA or CVE id pip-audit prints for the finding), `package` is the package name (compared case-insensitively, with `-`, `_` and `.` treated alike), `reason` says why the package cannot move yet, and `reviewBy` is the date by which someone re-checks it. CI fails an entry with no reason, a `reviewBy` in the past, or an id that no longer matches a finding, so entries are removed once the package moves.

## Infrastructure Setup

The VAMS infrastructure is defined as AWS CDK v2 TypeScript code.

### Install Dependencies

```bash
cd infra
npm install
```

### Synthesize the Stack

```bash
cd infra
npx cdk synth
```

### Preview Changes

```bash
cd infra
npx cdk diff
```

### Deploy

```bash
cd infra
npx cdk deploy --all --require-approval never
```

:::info[Docker Required]
Docker must be running before deployment. CDK builds container images for Lambda layers and processing pipeline containers during synthesis.
:::

:::warning[Wait for every stack to complete before using the deployment]
The solution is usable only after `cdk deploy` reports success for every stack. Resource names -- Amazon DynamoDB tables, the auxiliary and artefacts Amazon S3 buckets, and the audit log groups -- are published to AWS Systems Manager Parameter Store by the `ResourceNamesBuilder` nested stack, which AWS CloudFormation creates after the storage stack. The Amazon S3 bucket-sync functions live in that storage stack and resolve their names from those parameters, so on a first deployment into an account an invocation that arrives before the parameters exist fails at initialization. Its Amazon SQS message returns to the queue and is retried, and the event is processed once the remaining stacks finish.
:::

### Configuration

Deployment parameters are defined in `infra/config/config.json`. At minimum, update:

-   `region` -- the target AWS Region
-   `adminEmailAddress` -- receives the initial Cognito password
-   `baseStackName` -- the CloudFormation stack name prefix

See the [Configuration Guide](../deployment/prerequisites.md) for all available options.

## CLI Setup

The VAMS CLI is a Python-based command-line tool built with the Click framework.

```bash
cd tools/VamsCLI
pip install -e .        # Install in development (editable) mode
```

Verify the installation:

```bash
vamscli --help
```

Configure a profile to connect to your deployed VAMS instance:

```bash
vamscli --profile my-env setup <api-gateway-url>
vamscli --profile my-env auth login -u <username>
```

## Running Tests

### CLI Tests

The CLI has a comprehensive test suite that can be run with:

```bash
cd tools/VamsCLI
python -m pytest tests/ -v
```

## Linting and Formatting

All lint and formatting commands must be run from the **project root directory**. The root `package.json` scripts target `web/src`, `infra/lib`, `infra/bin`, and `infra/test` paths.

```bash
# From the repository root
npm run lint              # Check for lint errors
npm run lint-fix          # Auto-fix lint issues
npm run prettier-check    # Check formatting
npm run prettier-fix      # Auto-fix formatting
```

:::tip[Always Run from Root]
Do not run lint or prettier commands from individual subdirectories. The root-level scripts are configured to cover all relevant source paths.
:::

## Environment Variables for Local Development

### Frontend

| Variable           | Description                              | Default            |
| ------------------ | ---------------------------------------- | ------------------ |
| `DEV_API_ENDPOINT` | Backend API URL (in `web/src/config.ts`) | `""` (same origin) |
| `PORT`             | Dev server port                          | `3001`             |

### Backend (Local Mock Server)

| Variable               | Description                         |
| ---------------------- | ----------------------------------- |
| `USE_LOCAL_MOCKS`      | Enable local mock mode (`true`)     |
| `COGNITO_AUTH_ENABLED` | Enable/disable Cognito auth locally |

:::note
`USE_LOCAL_MOCKS` applies to the local mock server only. A deployed AWS Lambda function resolves its resource names from AWS Systems Manager Parameter Store and therefore carries `VAMS_RESOURCE_PARAM_PREFIX` in its environment. The auth routes handler ignores `USE_LOCAL_MOCKS` whenever that variable is set, records the combination as an error in its log, and evaluates every submitted web route against the caller's permissions.
:::

### Infrastructure

| Variable                | Description                                                                                                             |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `AWS_REGION`            | Target deployment region                                                                                                |
| `STACK_NAME`            | CloudFormation stack name override                                                                                      |
| `AWS_USE_FIPS_ENDPOINT` | Turns on `app.useFips` at synthesis and points the deploying shell's AWS CLI and AWS CDK CLI at FIPS endpoints (`true`) |

## Docker Requirements

Docker is required for CDK deployment because:

1. **Lambda Layers** -- Python dependency bundling (`pip install` of each layer's committed `requirements.txt`) runs inside Docker containers
2. **Pipeline Containers** -- Processing pipelines (3D conversion, Gaussian splatting, point cloud) build container images
3. **Cross-platform Builds** -- Docker handles architecture differences (ARM64 vs x86_64)

Every VAMS container image is pinned to `linux/amd64`, and Docker Desktop on macOS and Windows includes the emulation that builds these images on an ARM64 host such as a Mac with Apple silicon. For an ARM64 Linux host and for container image errors during deployment, see [Docker Buildx Container Image Errors](../troubleshooting/common-issues.md#docker-buildx-container-image-errors).

## Docker Build Customization

### Custom SSL Certificates for HTTPS Proxies

Organizations that route traffic through HTTPS proxy servers with custom SSL certificates need to configure both the host environment and the Docker build environment.

**Step 1: Set host environment variables**

```bash
export AWS_CA_BUNDLE=/path/to/Combined.pem
export NODE_EXTRA_CA_CERTS=/path/to/Combined.pem
```

**Step 2: Modify the Docker build configuration**

Edit `infra/config/docker/Dockerfile-customDependencyBuildConfig` to add the certificate to the Lambda layer build environment:

```dockerfile
COPY /path/to/Combined.pem /var/task/Combined.crt
RUN pip config set global.cert /var/task/Combined.crt
```

**Step 3: Update pipeline container Docker files**

For processing pipeline containers in `backendPipelines/`, add the certificate lines above any `pip install` or download commands in the respective Docker files.

:::warning[Platform Specification]
All Docker image pulls should specify the `linux/amd64` platform. This is already configured in the VAMS Docker files and ensures consistent builds across Windows, macOS, and Linux host operating systems.
:::

### Cross-Platform Build Configuration

When deploying from an ARM64 host machine (such as a Mac with Apple silicon), Docker builds the `linux/amd64` images through cross-platform emulation; see [Docker Requirements](#docker-requirements).

## Next Steps

-   [Backend Development](backend.md) -- Lambda handler patterns and authorization
-   [Frontend Development](frontend.md) -- React component patterns and API integration
-   [CDK Infrastructure](cdk.md) -- Infrastructure patterns and deployment
-   [Viewer Plugin Development](viewer-plugins.md) -- Building custom file viewers

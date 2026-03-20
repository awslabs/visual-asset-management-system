# Local Development Environment Setup

This guide covers how to set up a local development environment for all VAMS components: the React frontend, Python Lambda backend, CDK infrastructure, and the CLI tool.

## Prerequisites

Before starting, ensure you have the following installed on your development machine.

| Tool | Minimum Version | Purpose |
|------|----------------|---------|
| Python | 3.13+ | Backend Lambda handlers, CLI tool |
| Node.js | 20.18.1+ | Frontend build, CDK infrastructure |
| npm | Included with Node | Package management (never use yarn) |
| Docker | Latest | CDK deployment, container pipelines |
| AWS CLI | v2 | AWS account access |
| AWS CDK CLI | Latest | Infrastructure deployment |
| Git | Latest | Version control |
| nvm | Latest | Node version management |

:::info[Python Version Note]
While AWS Lambda functions run on Python 3.12, local development and testing use Python 3.13+. The `backend/requirements.txt` file targets Python 3.13+ for development dependencies.
:::


## Frontend Setup

The VAMS frontend is a React 17 single-page application built with Vite.

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

The development server uses Vite's proxy configuration to forward API calls. You have two options for the backend:

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

| Package | Version | Purpose |
|---------|---------|---------|
| `aws-lambda-powertools` | 2.36.0 | Logger, Parser, BaseModel |
| `boto3` | 1.34.84 | AWS SDK |
| `pydantic` | 1.10.7 | Data validation (v1 only) |
| `casbin` | 1.33.0 | ABAC/RBAC authorization |
| `moto` | 5.1.0 | AWS service mocking (dev) |
| `pytest` | 8.3.4 | Test framework (dev) |

:::warning[Pydantic Version]
VAMS uses Pydantic **v1** (1.10.7). Never install or use Pydantic v2 APIs such as `model_validator`, `model_dump`, or `ConfigDict`. These will fail at Lambda import time. See the [Backend Development](backend.md) guide for correct patterns.
:::


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


### Configuration

Deployment parameters are defined in `infra/config/config.json`. At minimum, update:

- `region` -- the target AWS Region
- `adminEmailAddress` -- receives the initial Cognito password
- `baseStackName` -- the CloudFormation stack name prefix

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
vamscli auth login --profile my-env
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

| Variable | Description | Default |
|----------|-------------|---------|
| `DEV_API_ENDPOINT` | Backend API URL (in `web/src/config.ts`) | `""` (same origin) |
| `PORT` | Dev server port | `3001` |

### Backend (Local Mock Server)

| Variable | Description |
|----------|-------------|
| `USE_LOCAL_MOCKS` | Enable local mock mode (`true`) |
| `COGNITO_AUTH_ENABLED` | Enable/disable Cognito auth locally |

### Infrastructure

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | Target deployment region |
| `STACK_NAME` | CloudFormation stack name override |
| `AWS_USE_FIPS_ENDPOINT` | Enable FIPS endpoints (`true`) |
| `BUILDX_NO_DEFAULT_ATTESTATIONS` | Workaround for Docker/CDK build issues (`1`) |

## Docker Requirements

Docker is required for CDK deployment because:

1. **Lambda Layers** -- Poetry-based Python dependency bundling runs inside Docker containers
2. **Pipeline Containers** -- Processing pipelines (3D conversion, Gaussian splatting, point cloud) build container images
3. **Cross-platform Builds** -- Docker handles architecture differences (ARM64 vs x86_64)

If deploying from an ARM64 machine (e.g., Apple Silicon Mac), you may need to configure cross-platform emulation:

```bash
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

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

When deploying from an ARM64 host machine (such as Apple Silicon Mac), Docker handles architecture differences through cross-platform emulation. If you encounter build failures, configure the emulation layer:

```bash
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

## Next Steps

- [Backend Development](backend.md) -- Lambda handler patterns and authorization
- [Frontend Development](frontend.md) -- React component patterns and API integration
- [CDK Infrastructure](cdk.md) -- Infrastructure patterns and deployment
- [Viewer Plugin Development](viewer-plugins.md) -- Building custom file viewers

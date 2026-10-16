# Extending VAMS in a Fork

An organization that runs its own fork of VAMS can add net-new APIs, Lambda handlers, web pages, navigation entries, constants, CDK resources, AWS Systems Manager (SSM) resource names, CLI commands, MCP tools and viewer plugins without editing any file that upstream VAMS edits. Every place VAMS aggregates a list is composed from a core part, an add-on part and a **custom** part. Upstream ships each custom file empty and does not modify it again, so merging an upstream release (`git merge upstream/development` or a release tag) does not conflict with the fork's additions.

The extension points cover **additions**. Changing the behavior of an existing page, API or stack still means editing core files and resolving those changes on upgrade; see [Known limits](#known-limits).

## How the Extension Points Work

Each aggregated list has three parts:

| Part   | Owner    | Example                                                     |
| ------ | -------- | ----------------------------------------------------------- |
| Core   | upstream | `backend/backend/common/apiRoutes.py`, edited every release |
| Add-on | upstream | `backend/backend/common/addonApiRoutes.py` (Physna, Garnet) |
| Custom | the fork | `backend/backend/custom/customApiRoutes.py`, shipped empty  |

The core file splices the custom part in with a single line that does not change between releases. Every custom file opens with the same header:

```text
Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.
```

The custom files are for **net-new** additions. Two other kinds of fork-friendly file already exist and are used as they are:

-   **Override hooks for existing behavior** in `backend/backend/customConfigCommon/` — the authorizer MFA check and the login profile write. See [Custom Authentication Hooks](backend.md#custom-authentication-hooks).
-   **Supplementary configuration files** — `infra/config/csp/cspAdditionalConfig.json` for Content Security Policy sources and `infra/config/policy/*.json` for IAM, Amazon S3 bucket policy and AWS WAF rules. See [Additional configuration files](../deployment/configuration-reference.md#additional-configuration-files).

## Extension Points

| Layer                    | Fork-owned file(s)                                                                                    | Spliced in by                                                                            |
| ------------------------ | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| API routes               | `backend/backend/custom/customApiRoutes.py` (`CUSTOM_ROUTES`)                                         | `common/apiRoutes.py` → `ALL_API_ROUTES` and the `GET /auth/routes/api` listing          |
| Handlers and models      | `backend/backend/handlers/custom/`, `backend/backend/models/custom/`                                  | the shared `backend/backend` Lambda code asset (`handlers.custom.<name>.lambda_handler`) |
| Resource names (backend) | `backend/backend/custom/customResourceNames.py` (`CustomResourceKeys`)                                | resolved with `get_resource_name()`, like a core key                                     |
| Resource names (CDK)     | `infra/common/customResourceParamKeys.ts` (`CUSTOM_RESOURCE_PARAM_KEYS`)                              | published with `publishCustomResourceName()`                                             |
| CDK resources            | `infra/lib/nestedStacks/custom/customResources.ts` (`buildCustomResources`)                           | the `CustomBuilder` nested stack in `core-stack.ts`                                      |
| Feature switches         | `infra/common/customAppFeatures.ts` (`CUSTOM_APP_FEATURES`)                                           | returned by `buildCustomResources()` and published through `/api/secure-config`          |
| Fork settings            | `infra/config/custom/customConfig.json`                                                               | read with `loadCustomConfig()` in `customResources.ts`                                   |
| Web routes               | `web/src/custom/customRoutes.tsx` (`customRouteTable`)                                                | `web/src/routes.tsx` (same permission filter as core routes)                             |
| Web navigation           | `web/src/custom/customNavigation.ts` (`customNavSections`)                                            | `web/src/layout/Navigation.tsx` (same permission filter)                                 |
| Web services and pages   | `web/src/custom/services/`, `web/src/custom/pages/`, `web/src/custom/customFeatures.ts`               | imported by the fork's routes and pages                                                  |
| CLI commands             | `tools/VamsCLI/vamscli/commands/custom/` (`CUSTOM_COMMANDS`)                                          | `vamscli/main.py` registers each on the root command group                               |
| CLI constants            | `tools/VamsCLI/vamscli/custom_constants.py` (`API_*`, `FEATURE_CUSTOM_*`)                             | imported by the fork's client methods and commands                                       |
| CLI API client           | `tools/VamsCLI/vamscli/utils/custom_api_client.py` (`CustomAPIClientMixin`)                           | inherited by `APIClient`                                                                 |
| MCP tools                | `tools/VamsMCP/vams_mcp/custom_tools.py` (`register_custom_tools`)                                    | called once by `vams_mcp/server.py`, after the upstream tools                            |
| Viewer plugins           | `web/src/visualizerPlugin/config/customViewerConfig.json`, `web/src/visualizerPlugin/viewers/custom/` | merged by `visualizerPlugin/core/viewerSources.ts`                                       |
| Viewer install steps     | `web/customInstalls/custom/customInstall.js`                                                          | the last step of the `postinstall` script in `web/package.json`                          |

The custom CDK resources live in a nested stack of their own, so they count against neither API builder stack's 500-resource and 1 MB template limits. The stack holds a permissionless `AWS::CloudFormation::WaitConditionHandle` placeholder so it is valid while it holds nothing else, deploys after the storage and resource-name stacks, and deploys before the REST API stack, which renders custom routes with the core ones.

### Recommended naming conventions

These conventions make it unlikely that a later upstream addition uses the same name as one of the fork's. They are recommendations; VAMS does not enforce them.

| What                   | Recommended convention                    |
| ---------------------- | ----------------------------------------- |
| API route paths        | `/custom/...`                             |
| SSM resource-name keys | `custom/<name>` under the existing prefix |
| Web route paths        | `#/custom/...`                            |
| Feature switches       | `CUSTOM_*`                                |
| CLI command names      | `custom-*`                                |
| MCP tool names         | `custom_*`                                |
| Viewer plugin IDs      | `custom-*`                                |

An **exact duplicate** of a core name is rejected whatever the convention: a method and path already registered with API Gateway fail synthesis (`RouteRegistry.register`), a resource-name key equal to a core key fails synthesis (`publishCustomResourceName`), and a viewer ID equal to a core or add-on viewer ID is skipped in the browser with a console error.

## Worked Example: Widgets

This example adds a "widgets" feature end to end: a widget table, an SSM resource name, a database-scoped API (`GET` and `POST /custom/database/{databaseId}/widgets`), a handler with both authorization tiers, a feature switch, a web page with a navigation entry, a CLI command group, an MCP tool and a viewer plugin for `.widget` files. Every file it touches is a fork extension point. The example is documentation only; upstream does not ship it.

### 1. Route, resource name, feature switch and settings

```python
# backend/backend/custom/customApiRoutes.py
from typing import Tuple

from common.apiRouteTypes import GET, POST, ApiRoute

API_CUSTOM_WIDGETS = ApiRoute("/custom/database/{databaseId}/widgets", (GET, POST), "custom")

CUSTOM_ROUTES: Tuple[ApiRoute, ...] = (API_CUSTOM_WIDGETS,)
```

```python
# backend/backend/custom/customResourceNames.py
from common.resourceNames import ResourceParamKey


class CustomResourceKeys:
    WIDGET_STORAGE_TABLE = ResourceParamKey(
        "custom/dynamoTables/widgetStorage", ("CUSTOM_WIDGET_STORAGE_TABLE_NAME",)
    )
```

```typescript
// infra/common/customResourceParamKeys.ts
export const CUSTOM_RESOURCE_PARAM_KEYS: Record<string, Record<string, string>> = {
    dynamoTables: {
        widgetStorage: "custom/dynamoTables/widgetStorage",
    },
};

// infra/common/customAppFeatures.ts
export enum CUSTOM_APP_FEATURES {
    CUSTOM_WIDGETS = "CUSTOM_WIDGETS",
}
```

The fork's settings go in `infra/config/custom/customConfig.json`:

```json
{
    "widgets": { "enabled": true }
}
```

The environment variable in the `ResourceParamKey` is the break-glass override that tests and local tools use, as for core keys.

### 2. Model and handler

```python
# backend/backend/models/custom/widgets.py
from typing import Optional

from aws_lambda_powertools.utilities.parser import BaseModel
from pydantic import Field


class CreateWidgetRequestModel(BaseModel, extra="ignore"):
    widgetName: str = Field(min_length=1, max_length=256)
    description: Optional[str] = Field(default=None, max_length=256)
```

The handler follows the gold-standard shape (`backend/HANDLER_TEMPLATES.md`): resource names resolved at module level, Tier 1 (`enforceAPI`) with the fail-closed pre-set flag, dispatch on the `ApiRoute` constant, and Tier 2 on the existing `database` entity type.

```python
# backend/backend/handlers/custom/widgetService.py
import json
import uuid

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from botocore.config import Config
from boto3.dynamodb.conditions import Key

from common.validators import validate
from custom.customApiRoutes import API_CUSTOM_WIDGETS
from custom.customResourceNames import CustomResourceKeys
from common.resourceNames import get_resource_name
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from models.common import authorization_error, internal_error, success, validation_error
from models.custom.widgets import CreateWidgetRequestModel

logger = safeLogger(service_name="CustomWidgetService")
dynamodb = boto3.resource("dynamodb", config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))

try:
    widget_table = dynamodb.Table(get_resource_name(CustomResourceKeys.WIDGET_STORAGE_TABLE))
except Exception:
    logger.exception("Failed loading resource names")
    raise


def _database_allowed(claims_and_roles, database_id, method):
    # Tier 2: widgets belong to a database, so the database entity decides access.
    if len(claims_and_roles["tokens"]) == 0:
        return False
    database = {"databaseId": database_id, "object__type": "database"}
    return CasbinEnforcer(claims_and_roles).enforce(database, method)


def lambda_handler(event, context):
    claims_and_roles = request_to_claims(event)
    try:
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                method_allowed_on_api = True
        if not method_allowed_on_api:
            return authorization_error()

        path = event["requestContext"]["http"]["path"]
        method = event["requestContext"]["http"]["method"]
        if not API_CUSTOM_WIDGETS.matches(path):
            return validation_error(body={"message": "Unknown route"}, event=event)

        database_id = event.get("pathParameters", {}).get("databaseId", "")
        (valid, message) = validate({"databaseId": {"value": database_id, "validator": "ID"}})
        if not valid:
            return validation_error(body={"message": message}, event=event)
        if not _database_allowed(claims_and_roles, database_id, method):
            return authorization_error()

        if method == "GET":
            response = widget_table.query(KeyConditionExpression=Key("databaseId").eq(database_id))
            return success(body={"Items": response.get("Items", [])})
        if method == "POST":
            request = parse(json.loads(event.get("body") or "{}"), model=CreateWidgetRequestModel)
            item = {"databaseId": database_id, "widgetId": str(uuid.uuid4()), **request.dict()}
            widget_table.put_item(Item=item)
            return success(body=item)
        return validation_error(body={"message": "Method not allowed"}, event=event)
    except ValidationError:
        return validation_error(body={"message": "Invalid widget request"}, event=event)
    except Exception:
        logger.exception("Internal error")
        return internal_error(event=event)
```

A listing that can exceed one page returns a `NextToken`, as the core list endpoints do (`backend/CLAUDE.md`, pagination patterns).

### 3. CDK resources

`buildCustomResources()` builds the table, publishes its name, builds the Lambda with the five security calls of the [Lambda builder pattern](cdk.md#lambda-builder-pattern), registers the routes and returns the feature switch.

```typescript
// infra/lib/nestedStacks/custom/customResources.ts
import * as path from "path";
import { Duration, RemovalPolicy } from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { HttpMethod } from "aws-cdk-lib/aws-apigatewayv2";
import { Construct } from "constructs";
import * as Config from "../../../config/config";
import { LAMBDA_PYTHON_RUNTIME } from "../../../config/config";
import { CUSTOM_APP_FEATURES } from "../../../common/customAppFeatures";
import { CUSTOM_RESOURCE_PARAM_KEYS } from "../../../common/customResourceParamKeys";
import { attachFunctionToApi } from "../apiLambda/apiRouteRegistry";
import {
    globalLambdaEnvironmentsAndPermissions,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
} from "../../helper/security";
import {
    CustomResourcesProps,
    CustomResourcesResult,
    loadCustomConfig,
    publishCustomResourceName,
} from "./customStackHelpers";

export function buildCustomResources(
    scope: Construct,
    props: CustomResourcesProps
): CustomResourcesResult {
    if (!loadCustomConfig().widgets?.enabled) {
        return { enabledFeatures: [] };
    }
    const { config, storageResources } = props;
    const useCmk = config.app.useKmsCmkEncryption.enabled;

    const widgetTable = new dynamodb.Table(scope, "WidgetStorageTable", {
        partitionKey: { name: "databaseId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "widgetId", type: dynamodb.AttributeType.STRING },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
        removalPolicy: RemovalPolicy.RETAIN,
        encryption: useCmk
            ? dynamodb.TableEncryption.CUSTOMER_MANAGED
            : dynamodb.TableEncryption.AWS_MANAGED,
        encryptionKey: useCmk ? storageResources.encryption.kmsKey : undefined,
    });
    publishCustomResourceName(
        scope,
        config,
        CUSTOM_RESOURCE_PARAM_KEYS.dynamoTables.widgetStorage,
        widgetTable.tableName
    );

    const inVpc = config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;
    const widgetService = new lambda.Function(scope, "customWidgetService", {
        code: lambda.Code.fromAsset(path.join(__dirname, "../../../../backend/backend")),
        handler: "handlers.custom.widgetService.lambda_handler",
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [props.lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: inVpc ? props.vpc : undefined,
        vpcSubnets: inVpc ? { subnets: props.isolatedSubnets } : undefined,
        environment: {},
    });
    widgetTable.grantReadWriteData(widgetService);
    kmsKeyLambdaPermissionAddToResourcePolicy(widgetService, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(widgetService, storageResources);
    globalLambdaEnvironmentsAndPermissions(widgetService, config);
    suppressCdkNagLambda(widgetService);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    for (const method of [HttpMethod.GET, HttpMethod.POST]) {
        attachFunctionToApi(scope, widgetService, {
            routePath: "/custom/database/{databaseId}/widgets",
            method,
            registry: props.registry,
        });
    }

    return { enabledFeatures: [CUSTOM_APP_FEATURES.CUSTOM_WIDGETS] };
}
```

`globalLambdaEnvironmentsAndPermissions` already grants read access to every parameter under the resource-name prefix, so the handler can resolve the published widget table name with no further IAM statement. A new Amazon DynamoDB table or Amazon S3 bucket keeps the retention, encryption and naming rules of `infra/CLAUDE.md` and belongs in the fork's own record of the resources it creates.

### 4. Permissions

A custom route is served behind the VAMS custom authorizer like every core route, and the handler enforces both tiers. The `admin` role's default constraints match every route, so administrators reach the new API and page as soon as they deploy. Every other role, including the default read-only role, is denied until a constraint allows it. A role is granted the API and the page with two Tier 1 constraints. The permission constraints, in the format of the [permission templates](../concepts/permissions-model.md), are:

```json
[
    {
        "name": "widget-users-api",
        "description": "Allow the widget API",
        "objectType": "api",
        "criteriaAnd": [],
        "criteriaOr": [
            { "field": "route__path", "operator": "starts_with", "value": "/custom/database/" }
        ],
        "groupPermissions": [
            { "action": "GET", "type": "allow" },
            { "action": "POST", "type": "allow" }
        ]
    },
    {
        "name": "widget-users-web",
        "description": "Allow the widget page",
        "objectType": "web",
        "criteriaAnd": [],
        "criteriaOr": [
            { "field": "route__path", "operator": "starts_with", "value": "/custom/widgets" }
        ],
        "groupPermissions": [{ "action": "GET", "type": "allow" }]
    }
]
```

Tier 2 is the role's existing `database` constraints, because the handler checks the database the widgets belong to.

### 5. Web page, route and navigation

```typescript
// web/src/custom/services/customAPIService.ts
import { apiClient } from "../../services/apiClient";

export const fetchWidgets = async (databaseId: string): Promise<[boolean, any]> => {
    try {
        const response = await apiClient.get(`custom/database/${databaseId}/widgets`, {});
        return [true, response?.Items ?? []];
    } catch (error: any) {
        return [false, error?.message];
    }
};
```

```typescript
// web/src/custom/pages/Widgets.tsx
import React, { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import Header from "@cloudscape-design/components/header";
import Table from "@cloudscape-design/components/table";
import { fetchWidgets } from "../services/customAPIService";

export default function Widgets() {
    const { databaseId } = useParams();
    const [items, setItems] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!databaseId) {
            setLoading(false);
            return;
        }
        fetchWidgets(databaseId).then(([ok, data]) => {
            setItems(ok ? data : []);
            setLoading(false);
        });
    }, [databaseId]);

    return (
        <Table
            header={<Header>Widgets</Header>}
            loading={loading}
            items={items}
            empty={databaseId ? "No widgets" : "Open a database's widgets from its page"}
            columnDefinitions={[{ id: "name", header: "Name", cell: (w) => w.widgetName }]}
        />
    );
}
```

```typescript
// web/src/custom/customRoutes.tsx
import React from "react";
import type { RouteOption } from "../routes";

const Widgets = React.lazy(() => import("./pages/Widgets"));

export const customRouteTable: RouteOption[] = [
    { path: "/custom/widgets", Page: Widgets, active: "#/custom/widgets/" },
    { path: "/custom/widgets/:databaseId", Page: Widgets, active: "#/custom/widgets/" },
];

// web/src/custom/customNavigation.ts
export const customNavSections: any[] = [
    {
        type: "section",
        text: "Widgets",
        items: [{ type: "link", text: "Widgets", href: "#/custom/widgets/" }],
    },
];

// web/src/custom/customFeatures.ts
export const customFeaturesEnabled: Record<string, string> = {
    CUSTOM_WIDGETS: "CUSTOM_WIDGETS",
};
```

The routes and the navigation link pass through the same `web`-constraint check as core pages, and the routes appear in the constraint editor's route list. A page that depends on the feature switch checks `customFeaturesEnabled.CUSTOM_WIDGETS` against the `featuresEnabled` list from `/api/secure-config`, as core pages do.

### 6. CLI command

```python
# tools/VamsCLI/vamscli/custom_constants.py
API_CUSTOM_WIDGETS = "/custom/database/{databaseId}/widgets"
FEATURE_CUSTOM_WIDGETS = "CUSTOM_WIDGETS"
```

```python
# tools/VamsCLI/vamscli/utils/custom_api_client.py
from typing import Any, Dict

from ..custom_constants import API_CUSTOM_WIDGETS


class CustomAPIClientMixin:
    """API client methods for the endpoints a fork adds."""

    def list_custom_widgets(self, database_id: str) -> Dict[str, Any]:
        response = self.get(API_CUSTOM_WIDGETS.format(databaseId=database_id), include_auth=True)
        return response.json()

    def create_custom_widget(self, database_id: str, name: str) -> Dict[str, Any]:
        response = self.post(
            API_CUSTOM_WIDGETS.format(databaseId=database_id), data={"widgetName": name}
        )
        return response.json()
```

```python
# tools/VamsCLI/vamscli/commands/custom/widgets.py
import click

from ...utils.api_client import APIClient
from ...utils.decorators import get_profile_manager_from_context, requires_setup_and_auth
from ...utils.json_output import output_result


@click.group(name="custom-widgets")
def widgets():
    """Widget commands."""


@widgets.command(name="list")
@click.option("-d", "--database-id", required=True, help="Database ID")
@click.option("--json-output", is_flag=True, help="Output raw JSON response")
@click.pass_context
@requires_setup_and_auth
def list_widgets(ctx: click.Context, database_id: str, json_output: bool):
    """List the widgets of a database."""
    profile_manager = get_profile_manager_from_context(ctx)
    api_client = APIClient(profile_manager.load_config()["api_gateway_url"], profile_manager)
    result = api_client.list_custom_widgets(database_id)
    output_result(result, json_output, success_message="Widgets retrieved")
    return result
```

```python
# tools/VamsCLI/vamscli/commands/custom/__init__.py
from .widgets import widgets

CUSTOM_COMMANDS = [widgets]
```

`vamscli custom-widgets list -d <databaseId>` then works like a core command. Error mapping for the new endpoint follows the core client methods and the CLI exception hierarchy (`tools/VamsCLI/CLAUDE.md`).

### 7. MCP tool

```python
# tools/VamsMCP/vams_mcp/custom_tools.py
from typing import Any, Dict


def register_custom_tools(mcp, client, config, tool_result) -> None:
    @mcp.tool()
    @tool_result
    def custom_list_widgets(database_id: str) -> Dict[str, Any]:
        """List the widgets of one database."""
        return client.api.list_custom_widgets(database_id)

    if config.enable_writes:

        @mcp.tool()
        @tool_result
        def custom_create_widget(database_id: str, name: str) -> Dict[str, Any]:
            """Create a widget in a database."""
            return client.api.create_custom_widget(database_id, name)
```

`client.api` is the CLI's `APIClient`, which inherits the fork's methods, so the tool calls the API with the same authentication and retries as the upstream tools. The write tool is registered only when `VAMS_ENABLE_WRITES=true`. See [Adding tools in a fork](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsMCP/README.md) in the MCP server README.

### 8. Viewer plugin

```typescript
// web/src/visualizerPlugin/viewers/custom/WidgetViewerPlugin/WidgetViewerComponent.tsx
import React from "react";
import Box from "@cloudscape-design/components/box";
import { ViewerPluginProps } from "../../../core/types";

const WidgetViewerComponent: React.FC<ViewerPluginProps> = ({ assetKey }) => (
    <Box padding="l">Widget file: {assetKey}</Box>
);

export default WidgetViewerComponent;
```

```typescript
// web/src/visualizerPlugin/viewers/custom/customManifest.ts
export const CUSTOM_VIEWER_COMPONENTS: Record<string, string> = {
    "./viewers/custom/WidgetViewerPlugin/WidgetViewerComponent":
        "custom/WidgetViewerPlugin/WidgetViewerComponent",
};

export const CUSTOM_DEPENDENCY_MANAGERS: Record<string, string> = {};
```

The viewer's entry goes in `web/src/visualizerPlugin/config/customViewerConfig.json`:

```json
{
    "viewers": [
        {
            "id": "custom-widget-viewer",
            "name": "Widget Viewer",
            "description": "Shows .widget files.",
            "componentPath": "./viewers/custom/WidgetViewerPlugin/WidgetViewerComponent",
            "supportedExtensions": [".widget"],
            "supportsMultiFile": false,
            "canFullscreen": true,
            "priority": 1,
            "dependencies": [],
            "loadStrategy": "lazy",
            "category": "document",
            "featuresEnabledRestriction": ["CUSTOM_WIDGETS"],
            "enabled": true
        }
    ]
}
```

The viewer contract and the remaining configuration fields are in [Viewer Plugin Development](viewer-plugins.md). A viewer with an npm install or bundle step adds it to `web/customInstalls/custom/customInstall.js`; a viewer that loads from another origin adds the origin to `infra/config/csp/cspAdditionalConfig.json`.

## Checks That Cover Fork Content

The repository's own tests apply to the custom files once a fork fills them in, so the fork inherits the drift guards the core files have:

| Test                                                   | What it checks for custom content                                                                                   |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| `infra/test/api/apiRouteBackendCdkParity.test.ts`      | every route registered with API Gateway is declared in a backend route list, and every declared route is registered |
| `infra/test/storage/resourceNamesMirror.test.ts`       | `customResourceParamKeys.ts` and `customResourceNames.py` declare the same keys, none equal to a core key           |
| `backend/tests/common/test_custom_extension_points.py` | custom routes reach `ALL_API_ROUTES` and repeat no core method and path; custom resource keys repeat no core key    |
| `tools/VamsCLI/tests/test_constants_contract.py`       | each custom CLI path is a declared route; each `FEATURE_*` in `custom_constants.py` matches `customAppFeatures.ts`  |
| `web/src/visualizerPlugin/core/viewerSources.test.ts`  | every registered viewer, custom ones included, resolves its component through a manifest                            |

Two checks run at every synthesis rather than in a test: `CustomBuilderNestedStack` fails on a custom route marked `allowAnonymous`, and `publishCustomResourceName()` fails on a key equal to a core key.

## Known Limits

-   **Existing behavior is not overridable through these seams.** A change to a core page, API, stack or default is a core edit.
-   **No new Casbin object types.** Custom routes use the `api` and `web` object types for Tier 1 and the existing entity types (such as `database` and `asset`) for Tier 2.
-   **Non-admin roles are denied until a constraint allows them.** The default read-only role does not see custom routes or pages; this is fail-closed by design.
-   **No anonymous custom routes.** An unauthenticated route also needs an entry in the authorizer's ignored-path list, which stays core-owned so that no fork file can widen the unauthenticated surface. A fork that needs one edits the core list and resolves that change on upgrade.
-   **Shared API Gateway quota.** Custom routes add to the same REST API, so they count against its resources-per-API quota (300 by default, adjustable).
-   **Shared dependency files.** npm and Python dependencies a fork adds still go in the shared `package.json` and `pyproject.toml` files, which can conflict on upgrade.
-   **Pipelines.** A fork's processing pipeline is best deployed as its own stack and registered through the API ([Custom Pipelines](../pipelines/custom-pipelines.md)) or as an externally owned pipeline bridge (`app.pipelines.externalPipelineBridges` in the [configuration reference](../deployment/configuration-reference.md)); the custom nested stack is not a pipeline seam.

## Next Steps

-   [Backend Development](backend.md) -- handler, model and authorization patterns the custom handlers follow
-   [CDK Infrastructure](cdk.md) -- the Lambda builder pattern and route registration
-   [Frontend Development](frontend.md) -- page, service and routing conventions
-   [Viewer Plugin Development](viewer-plugins.md) -- the viewer contract and configuration fields
-   [Agentic Development](agentic-development.md) -- the `/add-api-endpoint` skill has a fork mode that scaffolds into these files

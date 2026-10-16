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

An **exact duplicate** of a core name never replaces the core entry, whatever the convention:

| Fork addition                 | When core already uses the name                                                                                                         |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| API route (method and path)   | Synthesis fails (`RouteRegistry.register`)                                                                                              |
| SSM resource-name key         | Synthesis fails (`publishCustomResourceName`)                                                                                           |
| Feature switch value          | Synthesis fails when `buildCustomResources()` returns a `VAMS_APP_FEATURES` value (`CustomBuilderNestedStack`)                          |
| Viewer ID                     | The custom viewer is skipped in the browser with a console error                                                                        |
| CLI command name              | `vamscli` fails at startup with an error that names the command (`vamscli/main.py`)                                                     |
| `CustomAPIClientMixin` method | The core `APIClient` method is called instead, because it comes first in the MRO; `tools/VamsCLI/tests/test_custom_api_client.py` fails |
| MCP tool name                 | The MCP SDK keeps the first registration (the core tool), logs `Tool already exists: <name>` and does not register the custom tool      |

A later upstream release that adds a name the fork already uses therefore surfaces when the fork merges it: synthesis, the CLI or a test fails, and the fork renames its entry. MCP tools are the exception, where the log line on the server's standard error is the only signal.

### Secrets

Custom resource-name parameters and `infra/config/custom/customConfig.json` hold names and non-sensitive settings only. `publishCustomResourceName()` writes a plain SSM `String` parameter under a prefix that VAMS Lambdas are granted read access to, and `customConfig.json` is committed with the fork and read at synthesis, so a value taken from it lands in the synthesized template. A password, API key or token in either is readable well beyond the code that uses it. A secret goes in AWS Secrets Manager instead:

-   `buildCustomResources()` creates the secret without a value, encrypted with the deployment's AWS KMS key **imported by ARN** (`kms.Key.fromKeyArn`), as `infra/CLAUDE.md` describes for `secretsmanager.Secret`. Passing the key object instead makes the storage stack and the custom stack depend on each other.
-   The value is set outside the template, for example with `aws secretsmanager put-secret-value`.
-   Only the Lambda that uses the secret is granted read on it (`secret.grantRead(fn)`). It receives the secret's ARN in an environment variable, or its name through `publishCustomResourceName()`, and reads the value at runtime with `GetSecretValue`.
-   A secret without automatic rotation carries an `AwsSolutions-SMG4` suppression whose reason says who rotates it (root `CLAUDE.md` Rule 4).

## Worked Example: Widgets

This example adds a "widgets" feature end to end: a widget table, an SSM resource name, a database-scoped API (`GET` and `POST /custom/database/{databaseId}/widgets`), a handler with both authorization tiers, a feature switch, a web page with a navigation entry, a CLI command group, an MCP tool and a viewer plugin for `.widget` files. Every file it touches is a fork extension point. The example is documentation only; upstream does not ship it. It is kept to the extension points: a production handler would first confirm that the database exists, as the core handlers do, and would remove its widget rows when the database is deleted.

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

The environment variable in the `ResourceParamKey` is the break-glass override that tests and local tools use, as for core keys. The resource-name parameter and `customConfig.json` hold names and settings, never a secret value; see [Secrets](#secrets).

### 2. Model and handler

```python
# backend/backend/models/custom/widgets.py
from typing import Optional

from aws_lambda_powertools.utilities.parser import BaseModel
from pydantic import Field

# A page must fit the 6 MB Lambda response limit; tokens are a base64 LastEvaluatedKey.
MAX_WIDGET_LIST_PAGE_SIZE = 1000
MAX_PAGINATION_TOKEN_LENGTH = 4096


class ListWidgetsRequestModel(BaseModel, extra="ignore"):
    maxItems: Optional[int] = Field(default=100, ge=1, le=MAX_WIDGET_LIST_PAGE_SIZE)
    pageSize: Optional[int] = Field(default=100, ge=1, le=MAX_WIDGET_LIST_PAGE_SIZE)
    startingToken: Optional[str] = Field(default=None, max_length=MAX_PAGINATION_TOKEN_LENGTH)


class CreateWidgetRequestModel(BaseModel, extra="ignore"):
    widgetName: str = Field(min_length=1, max_length=256)
    description: Optional[str] = Field(default=None, max_length=256)
```

The handler follows the gold-standard shape (`backend/HANDLER_TEMPLATES.md`): resource names resolved at module level, Tier 1 (`enforceAPI`) with the fail-closed pre-set flag, dispatch on the `ApiRoute` constant, and Tier 2 on the existing `database` entity type. The listing pages externally like the core list endpoints: it reads `maxItems`, `pageSize` and `startingToken`, returns one page, and returns a base64 `NextToken` while more widgets remain. Caller errors are a 400, as in the core handlers: a `startingToken` that is not this table's key for the database in the path, and a POST body that is missing or not JSON.

```python
# backend/backend/handlers/custom/widgetService.py
import base64
import json
import uuid

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from botocore.config import Config
from boto3.dynamodb.conditions import Key

from common.dynamodb import validate_pagination_info
from common.validators import validate
from custom.customApiRoutes import API_CUSTOM_WIDGETS
from custom.customResourceNames import CustomResourceKeys
from common.resourceNames import get_resource_name
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from models.common import authorization_error, internal_error, success, validation_error
from models.custom.widgets import CreateWidgetRequestModel, ListWidgetsRequestModel

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


def _start_key_from_token(token, database_id):
    # A token is a base64 LastEvaluatedKey of this query: the table's two key attributes, for the
    # database in the path. Anything else is the caller's error and is not sent to DynamoDB.
    try:
        start_key = json.loads(base64.b64decode(token).decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(start_key, dict) or set(start_key) != {"databaseId", "widgetId"}:
        return None
    if not all(isinstance(value, str) and value for value in start_key.values()):
        return None
    if start_key["databaseId"] != database_id:
        return None
    return start_key


def _list_widgets(event, database_id):
    query_parameters = event.get("queryStringParameters") or {}
    validate_pagination_info(query_parameters, 100)
    request = parse(query_parameters, model=ListWidgetsRequestModel)
    query_kwargs = {
        "KeyConditionExpression": Key("databaseId").eq(database_id),
        "Limit": request.pageSize,
    }
    if request.startingToken:
        start_key = _start_key_from_token(request.startingToken, database_id)
        if start_key is None:
            return validation_error(body={"message": "Invalid pagination token"}, event=event)
        query_kwargs["ExclusiveStartKey"] = start_key
    response = widget_table.query(**query_kwargs)
    result = {"Items": response.get("Items", [])}
    if "LastEvaluatedKey" in response:
        result["NextToken"] = base64.b64encode(
            json.dumps(response["LastEvaluatedKey"]).encode("utf-8")
        ).decode("utf-8")
    return success(body=result)


def _create_widget(event, database_id):
    # Required event body, parsed the way the core handlers do: a missing or non-JSON body is a 400.
    body = event.get("body")
    if not body:
        return validation_error(body={"message": "Request body is required"}, event=event)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in request body: {e}")
            return validation_error(body={"message": "Invalid JSON in request body"}, event=event)
    elif not isinstance(body, dict):
        logger.error("Request body is not a string or dict")
        return validation_error(body={"message": "Request body cannot be parsed"}, event=event)
    request = parse(body, model=CreateWidgetRequestModel)
    item = {"databaseId": database_id, "widgetId": str(uuid.uuid4()), **request.dict()}
    widget_table.put_item(Item=item)
    return success(body=item)


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
            return _list_widgets(event, database_id)
        if method == "POST":
            return _create_widget(event, database_id)
        return validation_error(body={"message": "Method not allowed"}, event=event)
    except ValidationError:
        return validation_error(body={"message": "Invalid widget request"}, event=event)
    except Exception:
        logger.exception("Internal error")
        return internal_error(event=event)
```

A caller that needs every widget follows `NextToken`, passing it back as `startingToken`, as the web service, CLI command and MCP tool below do.

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
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        let response = await apiClient.get(`custom/database/${databaseId}/widgets`, init);
        let items: any[] = response?.Items ?? [];
        while (response?.NextToken) {
            init.queryStringParameters.startingToken = response.NextToken;
            response = await apiClient.get(`custom/database/${databaseId}/widgets`, init);
            items = items.concat(response?.Items ?? []);
        }
        return [true, items];
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
from typing import Any, Dict, Optional

from ..custom_constants import API_CUSTOM_WIDGETS


class CustomAPIClientMixin:
    """API client methods for the endpoints a fork adds."""

    def list_custom_widgets(
        self, database_id: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """One page of widgets; `params` carries maxItems, pageSize and startingToken."""
        response = self.get(
            API_CUSTOM_WIDGETS.format(databaseId=database_id), include_auth=True, params=params or {}
        )
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
@click.option("--page-size", type=int, help="Number of items per page")
@click.option("--starting-token", help="NextToken from the previous page")
@click.option("--json-output", is_flag=True, help="Output raw JSON response")
@click.pass_context
@requires_setup_and_auth
def list_widgets(
    ctx: click.Context, database_id: str, page_size: int, starting_token: str, json_output: bool
):
    """List one page of the widgets of a database; the result carries NextToken while more remain."""
    profile_manager = get_profile_manager_from_context(ctx)
    api_client = APIClient(profile_manager.load_config()["api_gateway_url"], profile_manager)
    params = {}
    if page_size:
        params["pageSize"] = page_size
    if starting_token:
        params["startingToken"] = starting_token
    result = api_client.list_custom_widgets(database_id, params=params)
    output_result(result, json_output, success_message="Widgets retrieved")
    return result
```

```python
# tools/VamsCLI/vamscli/commands/custom/__init__.py
from .widgets import widgets

CUSTOM_COMMANDS = [widgets]
```

`vamscli custom-widgets list -d <databaseId>` then works like a core command; `--starting-token` takes the previous page's `NextToken`. Error mapping for the new endpoint follows the core client methods and the CLI exception hierarchy (`tools/VamsCLI/CLAUDE.md`).

### 7. MCP tool

```python
# tools/VamsMCP/vams_mcp/custom_tools.py
from typing import Any, Dict, Optional


def register_custom_tools(mcp, client, config, tool_result) -> None:
    @mcp.tool()
    @tool_result
    def custom_list_widgets(
        database_id: str, max_items: Optional[int] = None, starting_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """List the widgets of one database. The walk is bounded: `truncated` means rows were not
        seen, and `NextToken` continues it when passed back as `starting_token`."""
        return client.paginate(
            lambda params: client.api.list_custom_widgets(database_id, params=params),
            max_items=max_items,
            starting_token=starting_token,
        )

    if config.enable_writes:

        @mcp.tool()
        @tool_result
        def custom_create_widget(database_id: str, name: str) -> Dict[str, Any]:
            """Create a widget in a database."""
            return client.api.create_custom_widget(database_id, name)
```

`client.api` is the CLI's `APIClient`, which inherits the fork's methods, so the tool calls the API with the same authentication and retries as the upstream tools, and `client.paginate` follows `NextToken` with the same bounds as the upstream list tools. The write tool is registered only when `VAMS_ENABLE_WRITES=true`. See [Adding tools in a fork](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsMCP/README.md) in the MCP server README.

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
| `tools/VamsCLI/tests/test_custom_api_client.py`        | no `CustomAPIClientMixin` method has the name of an `APIClient` method                                              |
| `web/src/visualizerPlugin/core/viewerSources.test.ts`  | every registered viewer, custom ones included, resolves its component through a manifest                            |

The parity test synthesizes with the committed `infra/config/custom/customConfig.json`, so every route declared in `customApiRoutes.py` must be registered by `buildCustomResources()` under that file, or `no backend route is unregistered` fails. A fork that gates its routes on a setting in `customConfig.json`, as the worked example does with `widgets.enabled`, commits that setting enabled.

The other checks run at every synthesis or CLI start rather than in a test. `CustomBuilderNestedStack` fails synthesis when `buildCustomResources()` registers a route marked `allowAnonymous`, makes an earlier route anonymous, or returns a `VAMS_APP_FEATURES` value; `RouteRegistry` stores each route as a frozen copy, so an earlier route cannot be edited at all. `publishCustomResourceName()` fails on a key equal to a core key, and `vamscli` refuses a custom command whose name is already registered.

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

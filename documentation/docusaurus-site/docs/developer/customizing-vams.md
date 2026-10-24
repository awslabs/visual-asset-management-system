# Extending VAMS in a Fork

An organization that runs its own fork of VAMS can add net-new APIs, Lambda handlers, web pages, navigation entries, constants, permission object types, upload deny-list entries, CDK resources, AWS Systems Manager (SSM) resource names, CLI commands, MCP tools and viewer plugins without editing any file that upstream VAMS edits. Every place VAMS aggregates a list is composed from a core part, an add-on part and a **custom** part. Upstream ships each custom file empty and does not modify it again, so merging an upstream release (`git merge upstream/development` or a release tag) does not conflict with the fork's additions.

The extension points cover **additions**. Changing the behavior of an existing page, API or stack still means editing core files and resolving those changes on upgrade; see [Known limits](#known-limits).

## How the Extension Points Work

Each aggregated list has three parts, merged in the order core, add-on, custom:

| Part   | Owner    | Holds                                                                                                                                                                               |
| ------ | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Core   | upstream | the core entries, edited every release                                                                                                                                              |
| Add-on | upstream | optional, upstream-maintained parts that integrate VAMS with an external solution or add a stand-alone component, enabled by configuration ([Add-ons Overview](addons-overview.md)) |
| Custom | the fork | the fork's entries; shipped empty                                                                                                                                                   |

The files, list by list:

| List                                                          | Core                                                        | Add-on                                               | Custom                                             |
| ------------------------------------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------- |
| API routes                                                    | `backend/backend/common/apiRoutes.py`                       | `backend/backend/common/addonApiRoutes.py`           | `backend/backend/custom/customApiRoutes.py`        |
| Lambda handlers                                               | `backend/backend/handlers/<domain>/`                        | `backend/backend/handlers/addon/`                    | `backend/backend/handlers/custom/`                 |
| Request and response models                                   | `backend/backend/models/`                                   | `backend/backend/models/addon/`                      | `backend/backend/models/custom/`                   |
| Permission object types, constraint fields, upload deny lists | `backend/backend/common/constants.py`                       | `backend/backend/common/addonConstants.py`           | `backend/backend/custom/customConstants.py`        |
| Log-redaction keys                                            | `backend/backend/common/constants.py`                       | `backend/backend/common/addonConstants.py`           | `backend/backend/custom/customConstants.py`        |
| Temporary session key name prefix                             | `backend/backend/common/constants.py`                       | none; key names extend the core prefix               | none; key names extend the core prefix             |
| Resource-name keys (backend)                                  | `backend/backend/common/resourceNames.py`                   | `backend/backend/common/addonResourceNames.py`       | `backend/backend/custom/customResourceNames.py`    |
| Resource-name keys (CDK)                                      | `infra/common/resourceParamKeys.ts`                         | `infra/common/addonResourceParamKeys.ts`             | `infra/common/customResourceParamKeys.ts`          |
| Feature switches (CDK)                                        | `infra/common/vamsAppFeatures.ts`                           | `infra/common/addonAppFeatures.ts`                   | `infra/common/customAppFeatures.ts`                |
| Feature switches (CLI)                                        | `tools/VamsCLI/vamscli/constants.py`                        | `tools/VamsCLI/vamscli/addon_constants.py`           | `tools/VamsCLI/vamscli/custom_constants.py`        |
| CDK resources                                                 | the core nested stacks                                      | `infra/lib/nestedStacks/addon/` (`AddonBuilder`)     | `infra/lib/nestedStacks/custom/customResources.ts` |
| CDK configuration                                             | `infra/config/config.ts`                                    | `infra/config/addon/addonConfig.ts`                  | `infra/config/custom/customConfig.json`            |
| Content Security Policy sources                               | `infra/lib/helper/security.ts`                              | `infra/lib/nestedStacks/addon/addonCsp.ts`           | `infra/config/csp/cspAdditionalConfig.json`        |
| Read-only role default API and web routes                     | `.../auth/constructs/dynamodb-authdefaults-ro-construct.ts` | `infra/lib/nestedStacks/addon/addonDefaultRoutes.ts` | none; a fork writes its own constraints            |
| Default constraints for add-on object types                   | `.../auth/constructs/` (admin and read-only)                | `.../nestedStacks/addon/addonDefaultConstraints.ts`  | none; a fork writes its own constraints            |
| VPC interface endpoints                                       | `infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts`      | `infra/lib/nestedStacks/addon/addonVpcEndpoints.ts`  | none                                               |
| AWS WAF rule exclusions                                       | `infra/lib/cf-waf-stack.ts`                                 | `infra/lib/nestedStacks/addon/addonWafExclusions.ts` | none                                               |
| Web routes                                                    | `web/src/routes.tsx`                                        | `web/src/addons/addonRoutes.tsx`                     | `web/src/custom/customRoutes.tsx`                  |
| Web navigation                                                | `web/src/layout/Navigation.tsx`                             | `web/src/addons/addonNavigation.ts`                  | `web/src/custom/customNavigation.ts`               |
| Web navigation feature restriction                            | `web/src/layout/Navigation.tsx`                             | `featuresEnabledRestriction` on an entry             | `featuresEnabledRestriction` on an entry           |
| Web feature names                                             | `web/src/common/constants/featuresEnabled.ts`               | `web/src/addons/addonFeatures.ts`                    | `web/src/custom/customFeatures.ts`                 |
| Web API calls                                                 | `web/src/services/APIService.ts`                            | `web/src/services/addons/`                           | `web/src/custom/services/`                         |
| Viewer configuration                                          | `web/src/visualizerPlugin/config/viewerConfig.json`         | `.../config/addonViewerConfig.json`                  | `.../config/customViewerConfig.json`               |
| Viewer manifest                                               | `web/src/visualizerPlugin/viewers/manifest.ts`              | `.../viewers/addonManifest.ts`                       | `.../viewers/custom/customManifest.ts`             |
| CLI commands                                                  | `tools/VamsCLI/vamscli/commands/`                           | `tools/VamsCLI/vamscli/commands/addon/`              | `tools/VamsCLI/vamscli/commands/custom/`           |
| CLI API client                                                | `tools/VamsCLI/vamscli/utils/api_client.py`                 | `tools/VamsCLI/vamscli/utils/addon_api_client.py`    | `tools/VamsCLI/vamscli/utils/custom_api_client.py` |
| MCP tools                                                     | `tools/VamsMCP/vams_mcp/server.py`                          | `tools/VamsMCP/vams_mcp/addon_tools/`                | `tools/VamsMCP/vams_mcp/custom_tools.py`           |
| ConfigBuilder fields, rules and presets                       | `.../src/components/ConfigBuilder/`                         | `.../ConfigBuilder/addon/`                           | none; it covers upstream settings only             |
| Agent steering                                                | the `CLAUDE.md` files, `.kiro/steering/`                    | a `CLAUDE.md` beside the add-on's code               | none                                               |

The add-on files hold upstream code that most deployments do not run, so it is kept off the path of every core change; [Add-ons Overview](addons-overview.md) defines an add-on and lists the current ones. The licensed VNTANA and VEERUM viewers are add-on viewers: they ship disabled in `addonViewerConfig.json`, and a deployment with a license enables one by setting `"enabled": true` on its entry there. Their plugin directories (`viewers/VntanaViewerPlugin/`, `viewers/VeerumViewerPlugin/`), their install scripts in `web/customInstalls/vntana/` and `web/customInstalls/veerum/`, and their `postinstall` entries are unchanged.

Three of the lanes are not lists of entries:

-   **Navigation feature restriction.** A navigation section, link or link group in any of the three navigation files can carry `featuresEnabledRestriction`, the feature switches that must all be enabled for the entry to show, with the same meaning as on a viewer entry. `Navigation.tsx` drops an entry unless every switch it lists is enabled.
-   **Temporary session keys.** An API key whose name starts with the reserved prefix `temporary-session-` (`TEMPORARY_SESSION_KEY_NAME_PREFIX`) is a temporary session key: a short-lived key that a backend component mints for one session through a Lambda cross-call, named with the prefix followed by a sub-prefix of the component's own. A call authenticated with a temporary session key cannot create or update API keys (`403`), and a user-created key whose name carries the prefix is refused (`400`).
-   **Agent steering.** An add-on's guidance for coding agents is a `CLAUDE.md` beside its code in each tree it touches, which loads only when an agent works in that tree, and one Kiro steering file, `.kiro/steering/ADDON_<ADDON>.md`, with `inclusion: fileMatch` on the add-on's paths.

The core file splices the custom part in with a single line that does not change between releases. Every custom file opens with the same header:

```text
Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.
```

The custom files are for **net-new** additions. Two other kinds of fork-friendly file already exist and are used as they are:

-   **Override hooks for existing behavior** in `backend/backend/customConfigCommon/` — the authorizer MFA check and the login profile write. See [Custom Authentication Hooks](backend.md#custom-authentication-hooks).
-   **Supplementary configuration files** — `infra/config/csp/cspAdditionalConfig.json` for Content Security Policy sources and `infra/config/policy/*.json` for IAM, Amazon S3 bucket policy and AWS WAF rules. See [Additional configuration files](../deployment/configuration-reference.md#additional-configuration-files).

## Extension Points

| Layer                    | Fork-owned file(s)                                                                                                          | Spliced in by                                                                            |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| API routes               | `backend/backend/custom/customApiRoutes.py` (`CUSTOM_ROUTES`)                                                               | `common/apiRoutes.py` → `ALL_API_ROUTES` and the `GET /auth/routes/api` listing          |
| Handlers and models      | `backend/backend/handlers/custom/`, `backend/backend/models/custom/`                                                        | the shared `backend/backend` Lambda code asset (`handlers.custom.<name>.lambda_handler`) |
| Resource names (backend) | `backend/backend/custom/customResourceNames.py` (`CustomResourceKeys`)                                                      | resolved with `get_resource_name()`, like a core key                                     |
| Resource names (CDK)     | `infra/common/customResourceParamKeys.ts` (`CUSTOM_RESOURCE_PARAM_KEYS`)                                                    | published with `publishCustomResourceName()`                                             |
| Permission object types  | `backend/backend/custom/customConstants.py` (`CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS`, `CUSTOM_PERMISSION_CONSTRAINT_FIELDS`) | merged by `common/constants.py`; see [Permission object types](#permission-object-types) |
| Upload deny lists        | `backend/backend/custom/customConstants.py` (`CUSTOM_UNALLOWED_FILE_EXTENSIONS`, `CUSTOM_UNALLOWED_MIME_TYPES`)             | merged by `common/constants.py`; see [Upload deny lists](#upload-deny-lists)             |
| Log-redaction keys       | `backend/backend/custom/customConstants.py` (`CUSTOM_LOG_SENSITIVE_KEYS`)                                                   | merged by `common/constants.py`; see [Log-redaction keys](#log-redaction-keys)           |
| CDK resources            | `infra/lib/nestedStacks/custom/customResources.ts` (`buildCustomResources`)                                                 | the `CustomBuilder` nested stack in `core-stack.ts`                                      |
| Feature switches         | `infra/common/customAppFeatures.ts` (`CUSTOM_APP_FEATURES`)                                                                 | returned by `buildCustomResources()` and published through `/api/secure-config`          |
| Fork settings            | `infra/config/custom/customConfig.json`                                                                                     | read with `loadCustomConfig()` in `customResources.ts`                                   |
| Web routes               | `web/src/custom/customRoutes.tsx` (`customRouteTable`)                                                                      | `web/src/routes.tsx` (same permission filter as core routes)                             |
| Web navigation           | `web/src/custom/customNavigation.ts` (`customNavSections`)                                                                  | `web/src/layout/Navigation.tsx` (same permission filter)                                 |
| Web services and pages   | `web/src/custom/services/`, `web/src/custom/pages/`, `web/src/custom/customFeatures.ts`                                     | imported by the fork's routes and pages                                                  |
| CLI commands             | `tools/VamsCLI/vamscli/commands/custom/` (`CUSTOM_COMMANDS`)                                                                | `vamscli/main.py` registers each on the root command group                               |
| CLI constants            | `tools/VamsCLI/vamscli/custom_constants.py` (`API_*`, `FEATURE_CUSTOM_*`)                                                   | imported by the fork's client methods and commands                                       |
| CLI API client           | `tools/VamsCLI/vamscli/utils/custom_api_client.py` (`CustomAPIClientMixin`)                                                 | inherited by `APIClient`                                                                 |
| MCP tools                | `tools/VamsMCP/vams_mcp/custom_tools.py` (`register_custom_tools`)                                                          | called once by `vams_mcp/server.py`, after the upstream tools                            |
| Viewer plugins           | `web/src/visualizerPlugin/config/customViewerConfig.json`, `web/src/visualizerPlugin/viewers/custom/`                       | merged by `visualizerPlugin/core/viewerSources.ts`                                       |
| Viewer install steps     | `web/customInstalls/custom/customInstall.js`                                                                                | the last step of the `postinstall` script in `web/package.json`                          |

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

| Fork addition                              | When core already uses the name                                                                                                         |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| API route (method and path)                | Synthesis fails (`RouteRegistry.register`)                                                                                              |
| SSM resource-name key                      | Synthesis fails (`publishCustomResourceName`), also for a key the add-on lane declares                                                  |
| Feature switch value                       | Synthesis fails when `buildCustomResources()` returns a `VAMS_APP_FEATURES` or `ADDON_APP_FEATURES` value (`CustomBuilderNestedStack`)  |
| Permission object type or constraint field | Every backend Lambda fails at import with an error that names `custom/customConstants.py` and the entry (`common/constants.py`)         |
| Upload deny-list entry                     | The same import error: an entry can be added, never repeated                                                                            |
| Log-redaction key                          | The same import error: a key can be added, never repeated                                                                               |
| Viewer ID                                  | The custom viewer is skipped in the browser with a console error                                                                        |
| CLI command name                           | `vamscli` fails at startup with an error that names the command (`vamscli/main.py`)                                                     |
| `CustomAPIClientMixin` method              | The core `APIClient` method is called instead, because it comes first in the MRO; `tools/VamsCLI/tests/test_custom_api_client.py` fails |
| MCP tool name                              | The MCP SDK keeps the first registration (the core tool), logs `Tool already exists: <name>` and does not register the custom tool      |

A later upstream release that adds a name the fork already uses therefore surfaces when the fork merges it: synthesis, the backend tests, the CLI or a test fails, and the fork renames its entry. MCP tools are the exception, where the log line on the server's standard error is the only signal.

### Secrets

Custom resource-name parameters and `infra/config/custom/customConfig.json` hold names and non-sensitive settings only. `publishCustomResourceName()` writes a plain SSM `String` parameter under a prefix that VAMS Lambdas are granted read access to, and `customConfig.json` is committed with the fork and read at synthesis, so a value taken from it lands in the synthesized template. A password, API key or token in either is readable well beyond the code that uses it. A secret goes in AWS Secrets Manager instead:

-   `buildCustomResources()` creates the secret without a value, encrypted with the deployment's AWS KMS key **imported by ARN** (`kms.Key.fromKeyArn`), as `infra/CLAUDE.md` describes for `secretsmanager.Secret`. Passing the key object instead makes the storage stack and the custom stack depend on each other.
-   The value is set outside the template, for example with `aws secretsmanager put-secret-value`.
-   Only the Lambda that uses the secret is granted read on it (`secret.grantRead(fn)`). It receives the secret's ARN in an environment variable, or its name through `publishCustomResourceName()`, and reads the value at runtime with `GetSecretValue`.
-   A secret without automatic rotation carries an `AwsSolutions-SMG4` suppression whose reason says who rotates it (root `CLAUDE.md` Rule 4).

### Permission object types

A fork's handlers authorize their own entities with a **permission object type** of their own, registered in `backend/backend/custom/customConstants.py` (the add-ons use `common/addonConstants.py` the same way). Tier 2 then decides on the entity's own attributes instead of borrowing a core type: before it evaluates a constraint, the enforcer keeps only the attributes registered for the object's type, so an attribute that is not registered never reaches a constraint. The worked example registers a `widget` type authorized on `databaseId` and `widgetId`:

```python
CUSTOM_PERMISSION_CONSTRAINT_FIELDS = {
    "widgetId": "",  # "" for one value; [] for a list of values
}

CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS = {
    "widget": {
        "label": "Widget",
        "fields": [
            {"label": "Database ID", "value": "databaseId"},
            {"label": "Widget ID", "value": "widgetId"},
        ],
    },
}
```

`common/constants.py` merges these into `ALLOWED_CONSTRAINT_OBJECT_TYPES`, `CONSTRAINT_OBJECT_TYPE_FIELDS` and `PERMISSION_CONSTRAINT_FIELDS` at import, so constraint validation, template import, the enforcer and the `GET /auth/constraints/permissionObjects` listing all see the new type. The constraint editor and `vamscli role constraint permission-objects` read that listing, so the type appears there with no web or CLI change. The rules, each checked at import:

| Rule             | Detail                                                                                                                                                                                                                                                                                              |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Names            | Type and field names match `^[A-Za-z][A-Za-z0-9]{1,63}$`: letters and digits only, so a name can neither take the reserved `<x>__<y>` shape nor leave its place in a rule expression                                                                                                                |
| Reserved names   | `object__type`, `route__path` and `method` cannot be declared or listed; a field cannot be a Python keyword or a name the rule evaluator resolves to something else (`keys`, `get`, `items`, `format` and the like)                                                                                 |
| No redefinition  | A type or field cannot repeat a core one, and the add-on and custom lanes cannot repeat each other. Type and field names are compared ignoring letter case when checking for a repeat, so `Asset` repeats `asset` and `DatabaseId` repeats `databaseId`; authorization still matches a name exactly |
| Field references | Each field a type lists is a core field (such as `databaseId`) or one declared in a lane                                                                                                                                                                                                            |
| Sample values    | `""` for a single value; `[]` for a list of values, which, like `tags`, accepts only the `is_one_of` and `is_not_one_of` operators                                                                                                                                                                  |

A rule that is broken fails the import of `common/constants.py`, so every backend Lambda and the backend unit tests fail with an error that names the lane file and the entry. Nothing is skipped or loosened at runtime.

**An extension type grants nothing until a constraint allows it, the admin role included.** VAMS seeds constraints only for the core types and the types of an enabled add-on, so after deployment no role, admin or otherwise, can reach a widget. The fork writes the constraints that grant access, as in [step 4 of the example](#4-permissions). This follows two authorization principles: access decisions live in policy data that administrators manage, not in code a fork hard-codes, and nothing is granted by default.

### Upload deny lists

The add-on and custom lanes can block more file types on upload, download, streaming and workflow output; they cannot unblock one. `common/constants.py` appends each lane's entries to the core `UNALLOWED_FILE_EXTENSION_LIST` and `UNALLOWED_MIME_LIST`. In `backend/backend/custom/customConstants.py`:

```python
CUSTOM_UNALLOWED_FILE_EXTENSIONS = [".xyz"]
CUSTOM_UNALLOWED_MIME_TYPES = ["application/x-example"]
```

An extension is lower-case with one leading dot (`.xyz`, not `xyz`, `.XYZ` or `.tar.gz`), and a MIME type is lower-case with no parameters. A lane entry is matched the same way as a core entry (see [Blocked File Types](../architecture/security.md#blocked-file-types)): the check lower-cases the file's extension and media type, so `.xyz` also blocks `model.XYZ`. An entry that is malformed or repeats a core entry, or one the other lane adds, fails the import with an error naming the file and the entry. The core entries always stay in the merged list, so there is no way for a lane to remove one.

### Log-redaction keys

The add-on and custom lanes can add key, header or field names whose values VAMS never logs; they cannot remove one. `common/constants.py` appends each lane's entries to both core lists: `LOG_SENSITIVE_KEYS`, which the structured logger redacts, and `LOG_TEXT_SENSITIVE_KEYS`, which the redactor of log text returned to a caller redacts. In `backend/backend/custom/customConstants.py`:

```python
CUSTOM_LOG_SENSITIVE_KEYS = ["widgetToken"]
```

A key matches `^[A-Za-z][A-Za-z0-9_-]{1,63}$`. A key that is malformed, or that repeats a core key or one the other lane adds in any letter case, fails the import with an error naming the file and the key.

## Worked Example: Widgets

This example adds a "widgets" feature end to end: a widget table, an SSM resource name, a database-scoped API (`GET` and `POST /custom/database/{databaseId}/widgets`, `GET /custom/database/{databaseId}/widgets/{widgetId}`), a `widget` permission object type, a handler with both authorization tiers, a feature switch, a web page with a navigation entry, a CLI command group, an MCP tool and a viewer plugin for `.widget` files. Every file it touches is a fork extension point. The example is documentation only; upstream does not ship it. It is kept to the extension points: a production handler would first confirm that the database exists, as the core handlers do, and would remove its widget rows when the database is deleted.

### 1. Route, resource name, object type, feature switch and settings

```python
# backend/backend/custom/customApiRoutes.py
from typing import Tuple

from common.apiRouteTypes import GET, POST, ApiRoute

API_CUSTOM_WIDGETS = ApiRoute("/custom/database/{databaseId}/widgets", (GET, POST), "custom")
API_CUSTOM_WIDGET = ApiRoute("/custom/database/{databaseId}/widgets/{widgetId}", (GET,), "custom")

CUSTOM_ROUTES: Tuple[ApiRoute, ...] = (API_CUSTOM_WIDGETS, API_CUSTOM_WIDGET)
```

```python
# backend/backend/custom/customConstants.py
from typing import Dict, List, Union

CUSTOM_PERMISSION_CONSTRAINT_FIELDS: Dict[str, Union[str, List[str]]] = {
    "widgetId": "",
}

CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS: Dict[str, dict] = {
    "widget": {
        "label": "Widget",
        "fields": [
            {"label": "Database ID", "value": "databaseId"},
            {"label": "Widget ID", "value": "widgetId"},
        ],
    },
}

CUSTOM_UNALLOWED_FILE_EXTENSIONS: List[str] = []

CUSTOM_UNALLOWED_MIME_TYPES: List[str] = []
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

The environment variable in the `ResourceParamKey` is the break-glass override that tests and local tools use, as for core keys. The resource-name parameter and `customConfig.json` hold names and settings, never a secret value; see [Secrets](#secrets). The `widget` type and its `widgetId` field follow the [permission object type](#permission-object-types) rules; the deny lists stay empty.

### 2. Model and handler

```python
# backend/backend/models/custom/widgets.py
from typing import Optional

from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from pydantic import Field

from common.validators import trim_name, validate

# A page must fit the 6 MB Lambda response limit; tokens are a base64 LastEvaluatedKey.
MAX_WIDGET_LIST_PAGE_SIZE = 1000
MAX_PAGINATION_TOKEN_LENGTH = 4096


class ListWidgetsRequestModel(BaseModel, extra="ignore"):
    maxItems: Optional[int] = Field(default=100, ge=1, le=MAX_WIDGET_LIST_PAGE_SIZE)
    pageSize: Optional[int] = Field(default=100, ge=1, le=MAX_WIDGET_LIST_PAGE_SIZE)
    startingToken: Optional[str] = Field(default=None, max_length=MAX_PAGINATION_TOKEN_LENGTH)


class CreateWidgetRequestModel(BaseModel, extra="ignore"):
    # Optional: the caller may choose the new widget's UUID; otherwise the handler generates one.
    widgetId: Optional[str] = None
    widgetName: str = Field(min_length=1, max_length=256)
    description: Optional[str] = Field(default=None, max_length=256)

    # Trim surrounding whitespace before the length checks, as core models do; the repo's
    # model tests fail on a name or free-text field that neither trims nor is exempted.
    _trim_names = validator("widgetName", pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator("description", pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_widget_id(cls, values):
        if values.get("widgetId") is not None:
            (valid, message) = validate(
                {"widgetId": {"value": values["widgetId"], "validator": "UUID"}}
            )
            if not valid:
                raise ValueError(message)
        return values
```

The handler follows the gold-standard shape (`backend/HANDLER_TEMPLATES.md`): resource names resolved at module level, Tier 1 (`enforceAPI`) with the fail-closed pre-set flag, dispatch on the `ApiRoute` constants, and Tier 2 on the `widget` object type. The single-widget read and the create are single-resource checks, each with the token guard as its own statement before `enforce()`; the listing filters each widget through `enforce()` and appends it only when that passes. Because `widgetId` is an authorization attribute, the handler accepts it only as a UUID wherever it comes in: the path, the request body and the pagination token. The listing pages externally like the core list endpoints: it reads `maxItems`, `pageSize` and `startingToken`, returns one page, and returns a base64 `NextToken` while more widgets remain. Caller errors are a 400, as in the core handlers: a `widgetId` that is not a UUID, a `startingToken` that is not this table's key for the database in the path, and a POST body that is missing or not JSON.

```python
# backend/backend/handlers/custom/widgetService.py
import base64
import json
import uuid

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from botocore.config import Config
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

from common.dynamodb import validate_pagination_info
from common.validators import validate
from custom.customApiRoutes import API_CUSTOM_WIDGET, API_CUSTOM_WIDGETS
from custom.customResourceNames import CustomResourceKeys
from common.resourceNames import get_resource_name
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from models.common import (
    authorization_error,
    general_error,
    internal_error,
    success,
    validation_error,
)
from models.custom.widgets import CreateWidgetRequestModel, ListWidgetsRequestModel

logger = safeLogger(service_name="CustomWidgetService")
dynamodb = boto3.resource("dynamodb", config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))

try:
    widget_table = dynamodb.Table(get_resource_name(CustomResourceKeys.WIDGET_STORAGE_TABLE))
except Exception:
    logger.exception("Failed loading resource names")
    raise


def _widget_object(database_id, widget_id):
    # The authorization object of the `widget` type registered in custom/customConstants.py.
    return {"databaseId": database_id, "widgetId": widget_id, "object__type": "widget"}


def _widget_id(value):
    # A widget ID is a UUID wherever it comes in; normalized to the lower-case form uuid4() stores.
    if not isinstance(value, str):
        return None
    (valid, _message) = validate({"widgetId": {"value": value, "validator": "UUID"}})
    return value.lower() if valid else None


def _start_key_from_token(token, database_id):
    # A token is a base64 LastEvaluatedKey of this query: the table's two key attributes, for the
    # database in the path. Anything else is the caller's error and is not sent to DynamoDB.
    try:
        start_key = json.loads(base64.b64decode(token).decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(start_key, dict) or set(start_key) != {"databaseId", "widgetId"}:
        return None
    if start_key["databaseId"] != database_id:
        return None
    widget_id = _widget_id(start_key["widgetId"])
    if widget_id is None:
        return None
    return {"databaseId": database_id, "widgetId": widget_id}


def _list_widgets(event, claims_and_roles, database_id):
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
    # Tier 2, list filtering: a widget is returned only when enforce() allows it.
    enforcer = CasbinEnforcer(claims_and_roles)
    allowed = []
    for item in response.get("Items", []):
        widget = _widget_object(database_id, item["widgetId"])
        if len(claims_and_roles["tokens"]) > 0 and enforcer.enforce(widget, "GET"):
            allowed.append(item)
    result = {"Items": allowed}
    if "LastEvaluatedKey" in response:
        result["NextToken"] = base64.b64encode(
            json.dumps(response["LastEvaluatedKey"]).encode("utf-8")
        ).decode("utf-8")
    return success(body=result)


def _get_widget(event, claims_and_roles, database_id):
    widget_id = _widget_id((event.get("pathParameters") or {}).get("widgetId"))
    if widget_id is None:
        return validation_error(body={"message": "widgetId must be a UUID"}, event=event)
    # Tier 2, single resource: decided on the path before the table is read.
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not CasbinEnforcer(claims_and_roles).enforce(_widget_object(database_id, widget_id), "GET"):
        return authorization_error()
    item = widget_table.get_item(Key={"databaseId": database_id, "widgetId": widget_id}).get("Item")
    if item is None:
        return general_error(status_code=404, body={"message": "Widget not found"}, event=event)
    return success(body=item)


def _create_widget(event, claims_and_roles, database_id):
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
    widget_id = request.widgetId.lower() if request.widgetId else str(uuid.uuid4())
    # Tier 2, single resource: the new widget, as the constraint will see it.
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not CasbinEnforcer(claims_and_roles).enforce(_widget_object(database_id, widget_id), "POST"):
        return authorization_error()
    item = {**request.dict(exclude={"widgetId"}), "databaseId": database_id, "widgetId": widget_id}
    try:
        widget_table.put_item(Item=item, ConditionExpression="attribute_not_exists(widgetId)")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return validation_error(body={"message": "Widget already exists"}, event=event)
        raise
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
        if not (API_CUSTOM_WIDGETS.matches(path) or API_CUSTOM_WIDGET.matches(path)):
            return validation_error(body={"message": "Unknown route"}, event=event)

        database_id = (event.get("pathParameters") or {}).get("databaseId", "")
        (valid, message) = validate({"databaseId": {"value": database_id, "validator": "ID"}})
        if not valid:
            return validation_error(body={"message": message}, event=event)

        if API_CUSTOM_WIDGET.matches(path) and method == "GET":
            return _get_widget(event, claims_and_roles, database_id)
        if API_CUSTOM_WIDGETS.matches(path) and method == "GET":
            return _list_widgets(event, claims_and_roles, database_id)
        if API_CUSTOM_WIDGETS.matches(path) and method == "POST":
            return _create_widget(event, claims_and_roles, database_id)
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
    attachFunctionToApi(scope, widgetService, {
        routePath: "/custom/database/{databaseId}/widgets/{widgetId}",
        method: HttpMethod.GET,
        registry: props.registry,
    });

    return { enabledFeatures: [CUSTOM_APP_FEATURES.CUSTOM_WIDGETS] };
}
```

`globalLambdaEnvironmentsAndPermissions` already grants read access to every parameter under the resource-name prefix, so the handler can resolve the published widget table name with no further IAM statement. A new Amazon DynamoDB table or Amazon S3 bucket keeps the retention, encryption and naming rules of `infra/CLAUDE.md` and belongs in the fork's own record of the resources it creates.

### 4. Permissions

A custom route is served behind the VAMS custom authorizer like every core route, and the handler enforces both tiers. Tier 1 is the `api` and `web` object types: the `admin` role's default constraints match every route, so administrators reach the API and the page as soon as they deploy, and every other role, including the default read-only role, is denied them until a constraint allows it. Tier 2 is the `widget` type, which no seeded constraint covers. **Until a `widget` constraint exists, no role can read or create a widget, the admin role included**: the listing returns no widgets, and the single-widget read and the create return 403.

The template below grants one role `GET` and `POST` on the widgets of one database: the `widget` constraint for Tier 2, and Tier 1 constraints for the widget API routes and the widget page. It is in the format of the [permission templates](permissions.md#permission-templates). It stays with the fork rather than in `documentation/permissionsTemplates/`, because a core deployment has no `widget` type to import it into. Save it as `widget-users.json`:

```json
{
    "metadata": {
        "name": "Widget Users",
        "description": "Read and create the widgets of one database.",
        "version": "1.0"
    },
    "variables": [
        {
            "name": "DATABASE_ID",
            "required": true,
            "description": "The database whose widgets the role may use"
        },
        {
            "name": "ROLE_NAME",
            "required": true,
            "description": "The role to attach these constraints to"
        }
    ],
    "constraints": [
        {
            "name": "{{ROLE_NAME}}-widgets",
            "description": "Read and create the widgets of {{DATABASE_ID}}",
            "objectType": "widget",
            "criteriaAnd": [
                { "field": "databaseId", "operator": "equals", "value": "{{DATABASE_ID}}" }
            ],
            "criteriaOr": [],
            "groupPermissions": [
                { "action": "GET", "type": "allow" },
                { "action": "POST", "type": "allow" }
            ]
        },
        {
            "name": "{{ROLE_NAME}}-widgets-api",
            "description": "Allow the widget API routes",
            "objectType": "api",
            "criteriaAnd": [],
            "criteriaOr": [
                {
                    "field": "route__path",
                    "operator": "starts_with",
                    "value": "/custom/database"
                }
            ],
            "groupPermissions": [
                { "action": "GET", "type": "allow" },
                { "action": "POST", "type": "allow" }
            ]
        },
        {
            "name": "{{ROLE_NAME}}-widgets-web",
            "description": "Allow the widget page",
            "objectType": "web",
            "criteriaAnd": [],
            "criteriaOr": [
                { "field": "route__path", "operator": "starts_with", "value": "/custom/widgets" }
            ],
            "groupPermissions": [{ "action": "GET", "type": "allow" }]
        }
    ]
}
```

Preview the import, then run it without `--dry-run`:

```bash
python tools/permissionsSetup/apply_template.py \
    --template widget-users.json \
    --role-name widget-users \
    --variables '{"DATABASE_ID": "my-project-db"}' --dry-run
```

The `api` constraint names the route prefix, not one database, as the shipped templates do: Tier 1 decides which routes a role may call and the `widget` constraint limits it to one database. The routes view (`GET /auth/routes/api/allowed`) checks each route with a placeholder in place of every path parameter, so a route constraint that names a database would never show the routes as allowed there even though the requests pass.

To give administrators widget access, apply the same template with `--role-name admin`; the existing role is kept and the constraints are added to it. A `widgetId` criterion narrows a constraint to one widget, for example `{ "field": "widgetId", "operator": "equals", "value": "<widget UUID>" }`.

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

| Test                                                     | What it checks for custom content                                                                                    |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `infra/test/api/apiRouteBackendCdkParity.test.ts`        | every route registered with API Gateway is declared in a backend route list, and every declared route is registered  |
| `infra/test/storage/resourceNamesMirror.test.ts`         | `customResourceParamKeys.ts` and `customResourceNames.py` declare the same keys, none equal to a core or add-on key  |
| `backend/tests/common/test_custom_extension_points.py`   | custom routes reach `ALL_API_ROUTES` and repeat no core method and path; custom resource keys repeat no core key     |
| `backend/tests/common/test_constants_extension_lanes.py` | the rules for object types, constraint fields, deny-list entries and log keys, applied to whatever the lanes hold    |
| `infra/test/addon/appFeatureLanes.test.ts`               | no feature-switch value is declared in two of `vamsAppFeatures.ts`, `addonAppFeatures.ts` and `customAppFeatures.ts` |
| `tools/VamsCLI/tests/test_constants_contract.py`         | each custom CLI path is a declared route; each `FEATURE_*` in `custom_constants.py` matches `customAppFeatures.ts`   |
| `tools/VamsCLI/tests/test_custom_api_client.py`          | no `CustomAPIClientMixin` method has the name of an `APIClient` method                                               |
| `web/src/visualizerPlugin/core/viewerSources.test.ts`    | every registered viewer, custom ones included, resolves its component through a manifest                             |

The parity test synthesizes with the committed `infra/config/custom/customConfig.json`, so every route declared in `customApiRoutes.py` must be registered by `buildCustomResources()` under that file, or `no backend route is unregistered` fails. A fork that gates its routes on a setting in `customConfig.json`, as the worked example does with `widgets.enabled`, commits that setting enabled.

The other checks run at every synthesis, backend import or CLI start rather than in a test. `CustomBuilderNestedStack` fails synthesis when `buildCustomResources()` registers a route marked `allowAnonymous`, makes an earlier route anonymous, or returns a `VAMS_APP_FEATURES` or `ADDON_APP_FEATURES` value; `RouteRegistry` stores each route as a frozen copy, so an earlier route cannot be edited at all. `publishCustomResourceName()` fails on a key equal to a core key or an add-on key. `common/constants.py` refuses a constants-lane entry that breaks a rule when it is imported, so every backend Lambda and the backend tests fail with the file and entry named. `vamscli` refuses a custom command whose name is already registered.

## Known Limits

-   **Existing behavior is not overridable through these seams.** A change to a core page, API, stack or default is a core edit.
-   **Extension object types start with no access.** A permission object type a fork or add-on registers is covered by no seeded constraint, so every role, admin included, is denied on it until the fork adds constraints (see [Permission object types](#permission-object-types)). The default constraints are not extended automatically. The one exception is an add-on, which can seed default constraints for its own object types while it is enabled (`infra/lib/nestedStacks/addon/addonDefaultConstraints.ts`).
-   **Non-admin roles are denied custom routes until a constraint allows them.** The default read-only role does not see custom routes or pages; this is fail-closed by design.
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

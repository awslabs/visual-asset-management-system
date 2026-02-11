# Visual Asset Management System(VAMS) - ABAC/RBAC Permissions Guide

This document outlines the authentication and permission Attribute-based/Role-Based Access Control (ABAC/RBAC) system implemented in VAMS with Casbin on top of standard authentication.

## Summary Implementation

### Components

-   Authentication:
-   -   Cognito Authorizer: Handles user authentication for all API Gateway endpoints except `/api/amplify-config`.
-   Authorization:
-   -   Casbin: Open-source policy engine used for ABAC/RBAC evaluation within API-proxied lambda functions.
-   -   Data Source: Roles, User assignments, and constraints stored in DynamoDB tables.

### Authorization Implementation

1. Authentication:

-   Users are authenticated through Cognito for all endpoints except `/api/amplify-config`.

2. Request Processing:

-   For authenticated API requests (excluding `/api/amplify-config`):
-   -   User information is extracted from the JWT token claim (`vams:tokens`).
-   -   User roles and assigned constraints are retrieved from DynamoDB.
-   For (no-authentication) API requests to `/api/amplify-config`:
-   -   Basic static web site configuration information is provided without authentication or authorization checks.
-   Additional Exception: For web route authorization checks to `/auth/routes`
-   -   API for checking which passed in web routes are allowable for the particular authenticated user. No API tier 1 authorization checks to perform base API call.
-   -   Each provided web route is checked against Casbin policy and returned if allowed. Only used to restrict static web page user navigations.

3. Policy Evaluation with Casbin:

-   Policy Model: Defined within the application using Casbin. Stored in DynamoDB tables.
-   -   Subjects: Users and roles.
-   -   Objects: Route types (API/Web) and data object types (Database/Asset/etc.).
-   -   Actions: HTTP route methods (GET, PUT, POST, DELETE) and data object access methods (GET, PUT, POST, DELETE).
-   -   Criteria: AND or OR criterias that define what is allowed between Subjects, Objects, and Actions
-   Permission Tiers: The authorization evaluation logic inherent to APIs and business logic
-   -   Tier 1 - first: Defines access for HTTP methods on specific route object types (`web` for static web page routes, `api` for API data routes).
-   -   Tier 2 - second: Defines access for data operations on specific data object types.
-   Casbin enforcer evaluates the request context (user, action, object) against the defined policies and constraint criteria.

4. Authorization Decision:

-   Casbin returns an "allow" or "deny" decision based on policy evaluation.
-   The lambda for each API enforces the decision (grant or deny access).

### Key Points

-   Deny-first Logic: Similar to IAM, explicit "allow" rules are required to grant access. Deny rules can override allows.
-   Default Roles:
-   -   Admin Role: A pre-configured admin role with broad permissions exists by default
-   Roles, user assignments, and constraints can be managed using API calls, using the API front-facing UI administration pages, or direct modification in DynamoDB.
-   Policy Constraint Criteria supports various operators (eg. Contains, Starts with, Equals) and wildcard inputs
-   Users can be assigned to a role (which can have assigned constraints) or directly to constraints

### Benefits

-   Centralized Policy Management: Casbin policies are separate from code, enabling easier updates.
-   Granular Access Control: Fine-grained access based on roles, user assignments, and constraints.

### Considerations

-   Development Effort: Requires integrating Casbin and managing policies.
-   Performance: Overhead for policy evaluation needs consideration in large deployments.

### Additional Notes

-   This implementation focuses on using Casbin for ABAC/RBAC within the context of a pre-existing authentication (eg. Cognito) setup.
-   Security best practices for IAM, Cognito, and data access control must be followed.

### Further Exploration

-   Refer to Casbin documentation for detailed information on policy definition and enforcement: <https://casbin.org/docs/rbac/>
-   Explore AWS documentation for best practices on securing your application: <https://docs.aws.amazon.com/security>
-   Disclaimer: This document provides a conceptual overview and requires further configuration and development based on specific needs. Consider seeking professional assistance for complex deployments.

## Create New VAMS User

Administrators can create new users for your user pool from the Amazon Cognito console. Typically, users can sign in after they set a password. To sign in with an email address, a user must verify the email attribute. To confirm accounts as an administrator, you can also use the AWS CLI or API, or create user profiles with a federated identity provider. For more information, see the Amazon Cognito API Reference.

1. Navigate to the Amazon Cognito console, and choose User Pools.
2. Choose the existing VAMS user pool from the list (the most recent one created from the stack based on created datetime).
3. Choose the Users tab, and choose Create a user.
4. Choose “Send an Email invitation” from Invitation message.
5. Choose a Username and Email Address for the new user. The Username/Email is their email address.
6. Choose if you want to Create a password or have Amazon Cognito Generate a password for the user. Any temporary password must adhere to the user pool password policy. Recommended to just auto-generate. This will get sent to their inbox for initial login.
7. Choose Create.
8. Navigate to the VAMS Web UI, login as a user that has permission to add users to roles, and choose Users in Roles
9. Choose "Create Users in Roles"
10. Choose the Username that was created in cognito and the appropriate role
11. Choose "Create User in Role"

## Policy Permission Objects, Fields, and Operators

### Object Types and Related Fields

Object types below are specified in the `object__type` policy field. The fields mapped below each object type are possible inputs to check against for each object type within constraint criteria.

If any `AND` constraint criteria are defined, all must be true to allow an action on an object. If any `OR` constraint criteria are defined, at least 1 of the criteria must be true. A constraint must have at least 1 `AND` or `OR` criteria item defined.

A object type field will only be evaluated in a given set of constraints for an action/object function performed by a user if the field is defined in a criteria. As an example, if `assetName` is not defined in any constraint criteria for a particular user assigned constraints / roles, `assetName` will be ignored during checks.

Fields marked below as `recommended check` are fields that are recommended to be checked in a criteria if constraints are defined for a particular object type for a user or role.

-   API [`api`] (route object)
-   -   Route Path [`route__path`] (field) (recommended check)
-   Web [`web`] (route object)
-   -   Route Path [`route__path`] (field) (recommended check)
-   Database [`database`] (data object)
-   -   Database ID [`databaseId`] (field) (recommended check)
-   Asset [`asset`] (data object)
-   -   Database ID [`databaseId`] (field) (recommended check)
-   -   Asset Name [`assetName`] (field)
-   -   Asset Type [`assetType`] (field)
-   -   Tags [`tags`] (field)
-   Tag [`tag`] (data object)
-   -   Tag Name [`tagName`] (field) (recommended check)
-   Tag Type [`tagType`] (data object)
-   -   Tag Type Name [`tagTypeName`] (field) (recommended check)
-   Role [`role`] (data object)
-   -   Role Name [`roleName`] (field) (recommended check)
-   User Role [`userRole`] (data object)
-   -   Role Name [`roleName`] (field) (recommended check)
-   -   User ID [`userId`] (field)
-   Pipeline [`pipeline`] (data object)
-   -   Database ID [`databaseId`] (field) (recommended check)
-   -   Pipeline ID [`pipelineId`] (field)
-   -   Pipeline Type [`pipelineType`] (field)
-   Workflow [`workflow`] (data object)
-   -   Database ID [`databaseId`] (field) (recommended check)
-   -   Workflow ID [`workflowId`] (field)
-   Metadata Schema [`metadataSchema`] (data object)
-   -   Database ID [`databaseId`] (field) (recommended check)
-   -   Metadata Schema Entity Type [`metadataSchemaEntityType`] (field)
-   -   Metadata Schema Name [`metadataSchemaName`] (field)

#### Web - Routes

Below are the web routes possible as part of a `GET` method type. Requests for these are made through the `/auth/routes` API. These are only for enabling/disabling front-end functionality and does not impact any data or functionality retrieval from APIs. It uses the field `route__path` field for all WEB object type checks.

-   `*` (Default Landing Page, Static web UI always allows this)
-   `/` (Default Landing Page, Static web UI always allows this)
-   `/assetIngestion`
-   `/assets`
-   `/assets/:assetId`
-   `/auth/constraints`
-   `/auth/roles`
-   `/auth/subscriptions`
-   `/auth/tags`
-   `/auth/userroles`
-   `/databases`
-   `/databases/:databaseId/assets`
-   `/databases/:databaseId/assets/:assetId`
-   `/databases/:databaseId/assets/:assetId/file`
-   `/databases/:databaseId/assets/:assetId/file/*`
-   `/databases/:databaseId/assets/:assetId/uploads`
-   `/databases/:databaseId/assets/:assetId/download`
-   `/databases/:databaseId/pipelines`
-   `/databases/:databaseId/workflows`
-   `/databases/:databaseId/workflows/:workflowId`
-   `/metadataschema/:databaseId/create`
-   `/metadataschema/`
-   `/pipelines`
-   `/pipelines/:pipelineName`
-   `/search`
-   `/search/:databaseId/assets`
-   `/upload`
-   `/upload/:databaseId`
-   `/workflows`
-   `/workflows/create`

#### API - Routes, Methods, and Object Checks

Below are the API routes with the current supported method types. It uses the field `route__path` field for all initial API object type checks.

Additionally it shows which object authorization checks it does for a particular object type and field.

-   `/api/amplify-config` - GET (No authentication or API authorization logic checks on base call)
-   `/api/version` - GET (No authentication or API authorization logic checks on base call)
-   `/assets` - POST
-   -   `Asset` (assetName, databaseId, tags) - POST (api: POST)
-   `/asset-links` - POST
-   -   `Asset` (fromAssetId, fromAssetDatabaseId, assetName, assetType, tags) - POST (api: POST)
-   -   `Asset` (toAssetId, toAssetDatabaseId, assetName, assetType, tags) - POST (api: POST)
-   `/asset-links/single/{assetLinkId}` - GET
-   -   `Asset` (fromAssetId, fromAssetDatabaseId, assetName, assetType, tags) - GET (api: GET)
-   -   `Asset` (toAssetId, toAssetDatabaseId, assetName, assetType, tags) - GET (api: GET)
-   `/asset-links/{assetLinkId}` - PUT
-   -   `Asset` (fromAssetId, fromAssetDatabaseId, assetName, assetType, tags) - PUT (api: PUT)
-   -   `Asset` (toAssetId, toAssetDatabaseId, assetName, assetType, tags) - PUT (api: PUT)
-   `/asset-links/{relationId}` - DELETE
-   -   `Asset` (fromAssetId, fromAssetDatabaseId, assetName, assetType, tags) - DELETE (api: DELETE)
-   -   `Asset` (toAssetId, toAssetDatabaseId, assetName, assetType, tags) - DELETE (api: DELETE)
-   `/asset-links/{assetLinkId}/metadata` - GET/POST/PUT/DELETE
-   -   `Asset` (fromAssetId, fromAssetDatabaseId, assetName, assetType, tags) - GET (api: GET)
-   -   `Asset` (toAssetId, toAssetDatabaseId, assetName, assetType, tags) - GET (api: GET)
-   -   `Asset` (fromAssetId, fromAssetDatabaseId, assetName, assetType, tags) - POST (api: POST)
-   -   `Asset` (toAssetId, toAssetDatabaseId, assetName, assetType, tags) - POST (api: POST)
-   -   `Asset` (fromAssetId, fromAssetDatabaseId, assetName, assetType, tags) - PUT (api: PUT)
-   -   `Asset` (toAssetId, toAssetDatabaseId, assetName, assetType, tags) - PUT (api: PUT)
-   -   `Asset` (fromAssetId, fromAssetDatabaseId, assetName, assetType, tags) - DELETE (api: DELETE)
-   -   `Asset` (toAssetId, toAssetDatabaseId, assetName, assetType, tags) - DELETE (api: DELETE)
-   `/auth/constraints` - GET
-   `/auth/constraints/{constraintId}` - GET/PUT/POST/DELETE
-   `/auth/constraintsTemplateImport` - POST
-   `/auth/loginProfile/{userId}` - GET/POST
-   `/auth/routes` - POST (No API authorization logic checks on base call) (POST considered non-mutating to retrieve data only)
-   `/comments/assets/{assetId}` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/comments/assets/{assetId}/assetVersionId/{assetVersionId}` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}` - GET/PUT/POST/DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: PUT)
-   `/database` - GET/POST
-   -   `Database` (databaseId) - GET (api: GET)
-   -   `Database` (databaseId) - POST (api: POST)
-   `/database/{databaseId}` - GET/PUT/DELETE
-   -   `Database` (databaseId) - GET (api: GET)
-   -   `Database` (databaseId) - PUT (api: PUT)
-   -   `Database` (databaseId) - DELETE (api: DELETE)
-   -   `Database` (databaseId) - DELETE (api: DELETE)
-   `/buckets` - GET
-   `/database/{databaseId}/assets` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}` - GET/PUT
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: PUT)
-   `/database/{databaseId}/assets/{assetId}/asset-links` - GET
-   -   `Asset` (assetId, assetName, databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}` - GET/HEAD
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET/HEAD)
-   `/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}` - GET/HEAD
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET/HEAD)
-   `/database/{databaseId}/assets/{assetId}/createFolder` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: POST)
-   `/database/{databaseId}/assets/{assetId}/listFiles` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/fileInfo` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/moveFile` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   `/database/{databaseId}/assets/{assetId}/copyFile` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   `/database/{databaseId}/assets/{assetId}/archiveFile` - DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/database/{databaseId}/assets/{assetId}/unarchiveFile` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   `/database/{databaseId}/assets/{assetId}/deleteAssetPreview` - DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles` - DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/database/{databaseId}/assets/{assetId}/deleteFile` - DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/database/{databaseId}/assets/{assetId}/archiveAsset` - DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/database/{databaseId}/assets/{assetId}/deleteAsset` - DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/database/{databaseId}/assets/{assetId}/unarchiveAsset` - PUT
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: PUT)
-   `/database/{databaseId}/assets/{assetId}/revertFileVersion/{versionId}` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   `/database/{databaseId}/assets/{assetId}/setPrimaryFile` - PUT
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: PUT)
-   `/database/{databaseId}/assets/{assetId}/createVersion` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   `/database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId}` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   `/database/{databaseId}/assets/{assetId}/getVersions` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId}` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/export` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/download` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: POST)
-   `/database/{databaseId}/assets/{assetId}/workflows/{workflowId}` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   -   `Workflow` (databaseId, workflowId) - POST (api: POST)
-   -   `Pipeline` (databaseId, pipelineId, pipelineType, pipelineExecutionType) - POST (api: POST)
-   `/database/{databaseId}/assets/{assetId}/workflows/executions` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Workflow` (databaseId, workflowId) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Workflow` (databaseId, workflowId) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/metadata` - GET/POST/PUT/DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: PUT)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/database/{databaseId}/assets/{assetId}/metadata/file` - GET/POST/PUT/DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: PUT)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/database/{databaseId}/metadata` - GET/POST/PUT/DELETE
-   -   `Database` (databaseId) - GET (api: GET)
-   -   `Database` (databaseId) - POST (api: POST)
-   -   `Database` (databaseId) - PUT (api: PUT)
-   -   `Database` (databaseId) - DELETE (api: DELETE)
-   `/database/{databaseId}/pipelines` - GET
-   -   `Pipeline` (databaseId, pipelineId, pipelineType, pipelineExecutionType) - GET (api: GET)
-   `/database/{databaseId}/workflows` - GET
-   -   `Workflow` (databaseId, workflowId) - GET (api: GET)
-   `/database/{databaseId}/pipelines/{pipelineId}` - GET/DELETE
-   -   `Pipeline` (databaseId, pipelineId, pipelineType, pipelineExecutionType) - GET (api: GET)
-   -   `Pipeline` (databaseId, pipelineId, pipelineType, pipelineExecutionType) - DELETE (api: DELETE)
-   `/database/{databaseId}/workflows/{workflowId}` - GET/DELETE
-   -   `Workflow` (databaseId, workflowId) - GET (api: GET)
-   -   `Workflow` (databaseId, workflowId) - DELETE (api: DELETE)
-   `/assets` - GET/PUT
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: PUT)
-   `/ingest-asset` - POST
-   -   `Asset` (assetId, assetName databaseId) - PUT (api: POST)
-   `/database/{databaseId}/metadataSchema/{metadataSchemaId}` - GET/DELETE
-   -   `MetadataSchema` (databaseId, metadataSchemaEntityType, metadataSchemaName) - GET (api: GET)
-   -   `MetadataSchema` (databaseId, metadataSchemaEntityType, metadataSchemaName) - DELETE (api: DELETE)
-   `/metadataschema` - GET/POST/PUT
-   -   `MetadataSchema` (databaseId, metadataSchemaEntityType, metadataSchemaName) - GET (api: GET)
-   -   `MetadataSchema` (databaseId, metadataSchemaEntityType, metadataSchemaName) - POST (api: POST)
-   -   `MetadataSchema` (databaseId, metadataSchemaEntityType, metadataSchemaName) - POST (api: PUT)
-   `/pipelines` - GET/PUT
-   -   `Pipeline` (databaseId, pipelineId, pipelineType, pipelineExecutionType) - PUT (api: PUT)
-   -   `Pipeline` (databaseId, pipelineId, pipelineType, pipelineExecutionType) - GET (api: GET)
-   `/roles` - GET/PUT/POST
-   -   `Role` (roleName) - GET (api: GET)
-   -   `Role` (roleName) - POST (api: POST)
-   -   `Role` (roleName) - PUT (api: PUT)
-   `/roles/{roleId}` - DELETE
-   -   `Role` (roleName) - DELETE (api: DELETE)
-   `/search` - GET/POST (Both GET/POST considered non-mutating to retrieve data only)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET/POST)
-   `/secure-config` - GET (No API authorization logic checks on base call, does require Authentication header)
-   `/subscriptions` - GET/PUT/POST/DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: PUT)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: DELETE)
-   `/check-subscription` - POST (Both POST considered non-mutating to retrieve data only)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: POST)
-   `/unsubscribe` - DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: DELETE)
-   `/tags` - GET/PUT/POST
-   -   `Tag` (tagName) - GET (api: GET)
-   -   `Tag` (tagName) - POST (api: POST)
-   -   `Tag` (tagName) - PUT (api: PUT)
-   `/tags/{tagId}` - DELETE
-   -   `Tag` (tagName) - DELETE (api: DELETE)
-   `/tag-types` - GET/PUT/POST
-   -   `TagType` (tagTypeName) - GET (api: GET)
-   -   `TagType` (tagTypeName) - POST (api: POST)
-   -   `TagType` (tagTypeName) - PUT (api: PUT)
-   `/tag-types/{tagTypeId}` - DELETE
-   -   `TagType` (tagTypeName) - DELETE (api: DELETE)
-   `/uploads` - POST
-   -   `Asset` (assetId, assetName, assetType, databaseId, tags) - POST (api: POST)
-   `/uploads/{uploadId}/complete` - POST
-   -   `Asset` (assetId, assetName, assetType, databaseId, tags) - POST (api: POST)
-   `/user-roles` - GET/PUT/POST/DELETE
-   -   `UserRole` (roleName, userId) - GET (api: GET)
-   -   `UserRole` (roleName, userId) - POST (api: POST)
-   -   `UserRole` (roleName, userId) - PUT (api: PUT)
-   -   `UserRole` (roleName, userId) - DELETE (api: DELETE)
-   `/workflows` - GET/PUT
-   -   `Pipeline` (databaseId, pipelineId, pipelineType, pipelineExecutionType) - GET (api: PUT)
-   -   `Workflow` (databaseId, workflowId) - GET (api: GET)
-   -   `Workflow` (databaseId, workflowId) - PUT (api: PUT)
-   `/user/cognito` - GET/POST
-   `/user/cognito/{userId}` - PUT/DELETE
-   `/user/cognito/{userId}/resetPassword` - POST

### Constraint Statement Criteria Operators

Criteria operators and values are implemented with a REGEX evaluation statement. This means that inputs should be a valid REGEX format. Values are auto-escaped as part of the input to the policy.

Below are the REGEX statements that are evaluated with each operator for the given criteria `criterion[value]` value.

Note: The last two are used for metadata field evaluations that are not yet fully implemented for checks. These use `criterion[field]` to specify the metadata field to evaluate.

-   Equals [`equals`] (value input)
-   -   `regexMatch(^criterion[value]$)`
-   Contains [`contains`] (value input)
-   -   `regexMatch(.*criterion[value].*)`
-   Does Not Contain [`does_not_contain`] (value input)
-   -   `!regexMatch(.*criterion[value].*)`
-   Starts With [`starts_with`] (value input)
-   -   `regexMatch(^criterion[value].*)`
-   Ends With [`ends_with`] (value input)
-   -   `regexMatch(.*criterion[value]$)`
-   Is One Of [`is_one_of`] (`field` value input) - Reserved for Future Metadata Field Checks
-   -   `criterion['value']' in r.obj.criterion['field']`
-   Is Not One Of [`is_not_one_of`] (`field` value input) - Reserved for Future Metadata Field Checks
-   -   `!(criterion['value']' in r.obj.criterion['field'])`

---

## Implementing Custom Roles and Constraints

This section provides practical guidance for implementing custom roles with constraints. It covers the full constraint matrix needed for common permission profiles and explains critical nuances of the system.

### Understanding the Constraint Matrix

A common misconception is that creating a single constraint for a `database` objectType will automatically lock down all resources within that database (assets, pipelines, workflows, etc.). **This is not the case.**

The `database` objectType constraint only controls access to the **database entity itself** (listing, viewing, editing, deleting the database record). To restrict access to assets, pipelines, workflows, and other resources **within** that database, you must create separate constraints for each objectType using the `databaseId` field in the criteria.

**Critical Rule:** To fully lock down a user to a specific database, you need constraints for **all** of the following objectTypes:

| ObjectType       | What It Controls                                    | Key Criteria Field |
| ---------------- | --------------------------------------------------- | ------------------ |
| `web`            | Which UI pages the user can see                     | `route__path`      |
| `api`            | Which API endpoints the user can call               | `route__path`      |
| `database`       | Access to the database entity itself                | `databaseId`       |
| `asset`          | Access to assets within the database                | `databaseId`       |
| `pipeline`       | Access to pipelines within the database             | `databaseId`       |
| `workflow`       | Access to workflows within the database             | `databaseId`       |
| `metadataSchema` | Access to metadata schemas within the database      | `databaseId`       |
| `tag`            | Access to tags (typically not database-scoped)      | `tagName`          |
| `tagType`        | Access to tag types (typically not database-scoped) | `tagTypeName`      |

### GLOBAL Pipelines, Workflows, and Metadata Schemas

VAMS supports a special `GLOBAL` keyword for pipelines, workflows, and metadata schemas that are not tied to any specific database. These entities have their `databaseId` field set to the literal string `GLOBAL`.

**Important:** When granting access to GLOBAL resources, use the `equals` operator with the value `GLOBAL` -- do **not** use a wildcard (`contains .*`). This ensures the constraint only matches GLOBAL entities and does not inadvertently grant access to resources in other databases.

```json
{
    "criteriaAnd": [
        { "field": "databaseId", "id": "pipe-global1", "operator": "equals", "value": "GLOBAL" }
    ]
}
```

**When to use scoped vs GLOBAL constraints:**

For roles scoped to a specific database, you typically need **two** constraints per entity type (pipeline, workflow, metadataSchema):

| Constraint | Criteria                            | Purpose                                                |
| ---------- | ----------------------------------- | ------------------------------------------------------ |
| Scoped     | `databaseId equals {{DATABASE_ID}}` | Access to entities within the user's specific database |
| GLOBAL     | `databaseId equals GLOBAL`          | Access to shared GLOBAL entities                       |

For admins, the scoped constraint has full CRUD while the GLOBAL constraint is limited to GET + POST (view + execute). For standard users, both scoped and GLOBAL are limited to GET + POST (view + execute only, no management).

**Recommended approach for tags and tag types:** Since tags and tag types are shared across all databases (they are not database-scoped), the included templates grant only GET access on `/tags` and `/tag-types` API routes and on the `tag`/`tagType` entities for database-scoped roles. This prevents database-scoped users from modifying resources that affect all databases. However, customers can configure tag permissions differently based on their needs.

### Constraint Permissions and HTTP Methods

Each constraint assigns permissions using HTTP method verbs. Here is how they map to operations:

| Permission | Meaning        | Typical Operations                                             |
| ---------- | -------------- | -------------------------------------------------------------- |
| `GET`      | Read/View      | List items, view details, download files, stream content       |
| `PUT`      | Update/Modify  | Edit metadata, update properties, create new items (some APIs) |
| `POST`     | Create/Execute | Create items, upload files, execute workflows, run pipelines   |
| `DELETE`   | Remove         | Archive, delete, remove items                                  |

**Permission Type** can be `allow` or `deny`. The Casbin policy effect rule is:

```
e = some(where (p.eft == allow)) && !some(where (p.eft == deny))
```

This means: **At least one allow must match AND no deny can match.** Deny always wins.

### Example: Database Admin Role (Scoped to a Specific Database)

The Database Admin role provides full management of a specific database including asset creation, modification, permanent deletion, pipeline/workflow/schema management, and asset ingestion. Admins **cannot** create new databases (POST is excluded on the database entity).

Additionally, the admin has **global view and execute** access to pipelines and workflows across all databases, allowing them to use pipelines and workflows defined in other databases.

#### Constraint Summary (13 constraints)

| #   | Constraint                | ObjectType       | Permissions            | Scope                                                    |
| --- | ------------------------- | ---------------- | ---------------------- | -------------------------------------------------------- |
| 1   | Web Routes                | `web`            | GET                    | Standard pages + /assetIngestion                         |
| 2   | API Routes                | `api`            | GET, PUT, POST, DELETE | All non-admin routes (excludes /tags, /tag-types)        |
| 3   | API Routes (tags GET)     | `api`            | GET                    | Read-only on /tags, /tag-types                           |
| 4   | Database Entity           | `database`       | GET, PUT, DELETE       | Scoped to `{{DATABASE_ID}}` (no POST = no create)        |
| 5   | Assets                    | `asset`          | GET, PUT, POST, DELETE | Scoped to `{{DATABASE_ID}}` (includes permanent delete)  |
| 6   | Pipelines (scoped)        | `pipeline`       | GET, PUT, POST, DELETE | `databaseId equals {{DATABASE_ID}}` (full management)    |
| 7   | Pipelines (GLOBAL)        | `pipeline`       | GET, POST              | `databaseId equals GLOBAL` (view + execute)              |
| 8   | Workflows (scoped)        | `workflow`       | GET, PUT, POST, DELETE | `databaseId equals {{DATABASE_ID}}` (full management)    |
| 9   | Workflows (GLOBAL)        | `workflow`       | GET, POST              | `databaseId equals GLOBAL` (view + execute)              |
| 10  | Metadata Schemas (scoped) | `metadataSchema` | GET, PUT, POST, DELETE | `databaseId equals {{DATABASE_ID}}` (full management)    |
| 11  | Metadata Schemas (GLOBAL) | `metadataSchema` | GET                    | `databaseId equals GLOBAL` (view only)                   |
| 12  | Tags                      | `tag`            | GET                    | Global (read-only recommended for database-scoped roles) |
| 13  | Tag Types                 | `tagType`        | GET                    | Global                                                   |

**Key design decisions:**

-   **No database creation:** The database entity constraint grants GET + PUT + DELETE but **not POST**, which prevents creating new databases even though the API route constraint allows POST on `/database` (needed for asset operations that use `/database/{id}/...` paths).
-   **GLOBAL keyword for shared resources:** The global pipeline/workflow/schema constraints use `databaseId equals GLOBAL` to match only GLOBAL entities, not a wildcard. This prevents inadvertently granting access to resources in other databases.
-   **Scoped + GLOBAL pattern:** Two separate constraints per type -- one scoped (databaseId = specific DB) with full CRUD for management, one GLOBAL (databaseId = `GLOBAL`) with GET + POST for viewing and executing shared resources.
-   **Metadata schema GLOBAL = GET only:** Global schema access is read-only to prevent accidentally creating schemas in the GLOBAL scope.
-   **Tags read-only (recommended):** Since tags and tag types are shared across all databases, the recommended approach is to limit database-scoped roles to GET-only access and manage tags through a broader role. Customers can adjust this based on their requirements.

#### Apply with Template Tool

```bash
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/database-admin.json \
    --role-name my-project-admin \
    --variables '{"DATABASE_ID": "my-project-db"}' --dry-run
```

#### Manual Setup

```bash
# Step 1: Create the role
vamscli role create -r my-project-admin \
  --description "Database Admin for my-project-db"

# Step 2: Create all 13 constraints (see template for full JSON)
# Step 3: Assign users
vamscli role user create -u admin@example.com --role-name my-project-admin
```

##### Key Constraint: Database Entity (no POST = no create)

```json
{
    "name": "my-project-admin-database",
    "description": "Allow read, update, and delete of my-project-db (no create)",
    "objectType": "database",
    "criteriaAnd": [
        { "field": "databaseId", "id": "db1", "operator": "equals", "value": "my-project-db" }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-admin",
            "id": "db-get",
            "permission": "GET",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-admin",
            "id": "db-put",
            "permission": "PUT",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-admin",
            "id": "db-delete",
            "permission": "DELETE",
            "permissionType": "allow"
        }
    ]
}
```

##### Key Constraint: GLOBAL Pipeline View + Execute

Uses the `GLOBAL` keyword (not a wildcard) to match only shared global pipelines:

```json
{
    "name": "my-project-admin-pipelines-global",
    "description": "Allow viewing and executing GLOBAL pipelines",
    "objectType": "pipeline",
    "criteriaAnd": [
        { "field": "databaseId", "id": "pipe-global1", "operator": "equals", "value": "GLOBAL" }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-admin",
            "id": "pipe-global-get",
            "permission": "GET",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-admin",
            "id": "pipe-global-post",
            "permission": "POST",
            "permissionType": "allow"
        }
    ]
}
```

### Example: Database User Role (Scoped to a Specific Database)

The Database User role provides standard working access to a specific database. Users can view all data, create/update assets, upload files, archive (soft delete) assets, and execute workflows. Users **cannot** permanently delete assets, create/delete pipelines/workflows/metadata schemas, modify the database itself, or use asset ingestion.

Like admins, users have **GLOBAL view and execute** access to pipelines and workflows (databaseId = `GLOBAL`), plus view and execute access within their own database.

#### Constraint Summary (15 constraints)

| #   | Constraint                | ObjectType       | Permissions            | Scope                                                                                                 |
| --- | ------------------------- | ---------------- | ---------------------- | ----------------------------------------------------------------------------------------------------- |
| 1   | Web Routes                | `web`            | GET                    | Standard pages (excludes /assetIngestion)                                                             |
| 2   | API Routes (GET)          | `api`            | GET                    | Broad read access                                                                                     |
| 3   | API Routes (POST)         | `api`            | POST                   | Asset operations + workflow execution (excludes /ingest-asset, /metadataschema, /tags)                |
| 4   | API Routes (PUT)          | `api`            | PUT                    | Asset updates only (excludes /pipelines, /workflows, /metadataschema, /tags)                          |
| 5   | API Routes (DELETE)       | `api`            | DELETE                 | **Archive paths only** (archiveAsset, archiveFile) + standard non-asset deletes                       |
| 6   | Database Entity           | `database`       | GET                    | Scoped to `{{DATABASE_ID}}` (read-only)                                                               |
| 7   | Assets                    | `asset`          | GET, PUT, POST, DELETE | Scoped to `{{DATABASE_ID}}` (DELETE at Tier 2 needed for archive; permanent delete blocked at Tier 1) |
| 8   | Pipelines (scoped)        | `pipeline`       | GET, POST              | `databaseId equals {{DATABASE_ID}}` (view + execute)                                                  |
| 9   | Pipelines (GLOBAL)        | `pipeline`       | GET, POST              | `databaseId equals GLOBAL` (view + execute)                                                           |
| 10  | Workflows (scoped)        | `workflow`       | GET, POST              | `databaseId equals {{DATABASE_ID}}` (view + execute)                                                  |
| 11  | Workflows (GLOBAL)        | `workflow`       | GET, POST              | `databaseId equals GLOBAL` (view + execute)                                                           |
| 12  | Metadata Schemas (scoped) | `metadataSchema` | GET                    | `databaseId equals {{DATABASE_ID}}` (view only)                                                       |
| 13  | Metadata Schemas (GLOBAL) | `metadataSchema` | GET                    | `databaseId equals GLOBAL` (view only)                                                                |
| 14  | Tags                      | `tag`            | GET                    | Global (read-only recommended for database-scoped roles)                                              |
| 15  | Tag Types                 | `tagType`        | GET                    | Global                                                                                                |

**Key design decisions:**

-   **Archive vs permanent delete (two-tier enforcement):** The asset entity constraint grants DELETE at Tier 2 because both archive and permanent delete require DELETE on the asset entity. The differentiation happens at **Tier 1 API routes**: the DELETE API constraint uses `contains` operator to only match paths containing `archiveAsset` or `archiveFile`, blocking permanent delete paths (`deleteAsset`, `deleteFile`, `deleteAssetPreview`, etc.).
-   **Scoped + GLOBAL pattern:** Each entity type (pipeline, workflow, metadataSchema) has two constraints -- one for the specific database and one for `GLOBAL`. The scoped constraints allow viewing and executing within the user's database, while the GLOBAL constraints allow viewing and executing shared resources. Neither grants PUT or DELETE, preventing users from creating/modifying/deleting pipelines, workflows, or schemas.
-   **API route method separation:** Unlike the admin (which uses a single API constraint with all methods), the user has 4 separate API constraints -- one per HTTP method -- each allowing different route subsets. This enables fine-grained control over which operations are allowed on which paths.
-   **Tier 2 as a safety net:** Even though PUT on `/database` is allowed at Tier 1 (needed for asset operations using `/database/{id}/assets/...` sub-paths), Tier 2 blocks it because the database entity constraint only grants GET.
-   **Tags read-only (recommended):** Since tags and tag types are shared across all databases, the recommended approach is to limit database-scoped roles to GET-only access. Customers can grant tag write access to database-scoped roles if their use case requires it.

#### Apply with Template Tool

```bash
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/database-user.json \
    --role-name my-project-user \
    --variables '{"DATABASE_ID": "my-project-db"}' --dry-run
```

#### Manual Setup

```bash
# Step 1: Create the role
vamscli role create -r my-project-user \
  --description "Database User for my-project-db"

# Step 2: Create all 15 constraints (see template for full JSON)
# Step 3: Assign users
vamscli role user create -u user@example.com --role-name my-project-user
```

##### Key Constraint: API Routes DELETE (Archive Only)

This is the constraint that prevents permanent asset deletion while allowing archive operations:

```json
{
    "name": "my-project-user-api-routes-delete",
    "description": "Allow DELETE for archive operations only",
    "objectType": "api",
    "criteriaOr": [
        {
            "field": "route__path",
            "id": "api-del1",
            "operator": "contains",
            "value": "archiveAsset"
        },
        {
            "field": "route__path",
            "id": "api-del2",
            "operator": "contains",
            "value": "archiveFile"
        },
        {
            "field": "route__path",
            "id": "api-del3",
            "operator": "starts_with",
            "value": "/unsubscribe"
        },
        {
            "field": "route__path",
            "id": "api-del4",
            "operator": "starts_with",
            "value": "/subscriptions"
        },
        {
            "field": "route__path",
            "id": "api-del5",
            "operator": "starts_with",
            "value": "/asset-links"
        },
        {
            "field": "route__path",
            "id": "api-del6",
            "operator": "starts_with",
            "value": "/comments"
        }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-user",
            "id": "api-delete",
            "permission": "DELETE",
            "permissionType": "allow"
        }
    ]
}
```

The `contains` operator on `archiveAsset` matches `/database/{id}/assets/{id}/archiveAsset` but does **not** match `/database/{id}/assets/{id}/deleteAsset`. This is the Tier 1 enforcement that distinguishes archive from permanent delete.

##### Key Constraint: Asset Entity (DELETE for archive, protected by Tier 1)

```json
{
    "name": "my-project-user-assets",
    "description": "Allow create, update, and archive access to assets in my-project-db",
    "objectType": "asset",
    "criteriaAnd": [
        { "field": "databaseId", "id": "asset-db1", "operator": "equals", "value": "my-project-db" }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-user",
            "id": "asset-get",
            "permission": "GET",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-user",
            "id": "asset-put",
            "permission": "PUT",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-user",
            "id": "asset-post",
            "permission": "POST",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-user",
            "id": "asset-delete",
            "permission": "DELETE",
            "permissionType": "allow"
        }
    ]
}
```

Note: DELETE is granted at Tier 2 because archive operations require it. Permanent delete is blocked at Tier 1 (see API Routes DELETE constraint above).

### Database Admin vs Database User Comparison

| Capability                                           | Admin                                     | User                                      |
| ---------------------------------------------------- | ----------------------------------------- | ----------------------------------------- |
| View database, assets, pipelines, workflows, schemas | Yes                                       | Yes                                       |
| Create/update assets                                 | Yes                                       | Yes                                       |
| Upload files                                         | Yes                                       | Yes                                       |
| Archive (soft delete) assets                         | Yes                                       | Yes                                       |
| **Permanent delete** assets                          | **Yes**                                   | **No** (Tier 1 blocks)                    |
| Update/delete the database                           | **Yes**                                   | **No**                                    |
| Create new databases                                 | No                                        | No                                        |
| Create/delete pipelines (scoped)                     | **Yes**                                   | **No**                                    |
| Create/delete workflows (scoped)                     | **Yes**                                   | **No**                                    |
| Create/delete metadata schemas (scoped)              | **Yes**                                   | **No**                                    |
| View/execute GLOBAL pipelines & workflows            | Yes                                       | Yes                                       |
| View GLOBAL metadata schemas                         | Yes                                       | Yes                                       |
| Asset ingestion                                      | **Yes**                                   | **No**                                    |
| Tag management (create/modify/delete)                | No (recommended: manage via broader role) | No (recommended: manage via broader role) |
| Tag viewing                                          | Yes                                       | Yes                                       |

### Example: Read-Only Viewer for a Specific Database

For a viewer who can only read data from a specific database, follow the same pattern but only grant `GET` permissions and restrict API routes to read-only paths. The `database-readonly.json` template automates this.

Key differences from the admin/user roles:

-   Web routes: Same set of pages (but the UI respects the lack of write permissions)
-   API routes: Only allow `GET` method, and `POST` only for non-mutating operations (`/auth/routes`, `/search`, `/check-subscription`)
-   Data constraints: Only `GET` permission on all objectTypes, scoped to a specific database

```bash
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/database-readonly.json \
    --role-name my-project-viewer \
    --variables '{"DATABASE_ID": "my-project-db"}' --dry-run
```

### Example: Multi-Database Access

To give a user access to multiple databases, use `criteriaOr` instead of `criteriaAnd` for the `databaseId` field:

```json
{
    "name": "multi-db-editor-assets",
    "description": "Access to assets across finance and operations databases",
    "objectType": "asset",
    "criteriaOr": [
        { "field": "databaseId", "id": "db1", "operator": "equals", "value": "finance-db" },
        { "field": "databaseId", "id": "db2", "operator": "equals", "value": "operations-db" }
    ],
    "groupPermissions": [
        {
            "groupId": "multi-db-editor",
            "id": "asset-get",
            "permission": "GET",
            "permissionType": "allow"
        },
        {
            "groupId": "multi-db-editor",
            "id": "asset-put",
            "permission": "PUT",
            "permissionType": "allow"
        },
        {
            "groupId": "multi-db-editor",
            "id": "asset-post",
            "permission": "POST",
            "permissionType": "allow"
        }
    ]
}
```

Alternatively, use the `starts_with` operator with a naming convention:

```json
{
    "criteriaAnd": [
        { "field": "databaseId", "id": "db1", "operator": "starts_with", "value": "team-alpha-" }
    ]
}
```

This matches any database whose ID starts with `team-alpha-` (e.g., `team-alpha-prod`, `team-alpha-staging`).

### Using Deny Constraints for Exceptions

Deny constraints override allow constraints. Use this pattern to create broad access with specific exclusions. The Casbin policy effect ensures **deny always wins**:

```
e = some(where (p.eft == allow)) && !some(where (p.eft == deny))
```

#### Example: Deny Editing of Tagged Assets

A common use case is preventing modification of assets that have been tagged with a specific label (e.g., "locked", "approved", "production"). This is useful for protecting finalized assets from accidental changes while still allowing users to view them.

The deny constraint uses the `tags` field on the `asset` objectType with the `contains` operator to match assets that have the specified tag:

```json
{
    "name": "my-project-admin-deny-tagged-locked",
    "description": "Deny editing of assets tagged with 'locked'",
    "objectType": "asset",
    "criteriaAnd": [
        { "field": "tags", "id": "tag-match", "operator": "contains", "value": "locked" }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-admin",
            "id": "deny-put",
            "permission": "PUT",
            "permissionType": "deny"
        },
        {
            "groupId": "my-project-admin",
            "id": "deny-post",
            "permission": "POST",
            "permissionType": "deny"
        },
        {
            "groupId": "my-project-admin",
            "id": "deny-delete",
            "permission": "DELETE",
            "permissionType": "deny"
        }
    ]
}
```

**How it works:** Even though the admin role has full CRUD on assets (GET, PUT, POST, DELETE), this deny constraint matches any asset whose `tags` field contains "locked". When a user attempts to PUT, POST, or DELETE a locked asset, Casbin finds the deny rule and blocks the operation regardless of the allow rules. GET (viewing) is still permitted.

**Important notes:**

-   The `tags` field is checked as a string match. If an asset has tags `["locked", "reviewed"]`, the `contains` operator with value `locked` will match.
-   You can stack multiple deny constraints for different tag values (e.g., one for "locked" and another for "approved").
-   Deny constraints can be applied to any role -- both admin and user roles.
-   The deny applies to the **data entity operation** (Tier 2). The user can still call the API endpoint (Tier 1), but the operation will be denied when Casbin evaluates the asset entity.

#### Apply Tag-Based Deny with Template Tool

The `deny-tagged-assets.json` template automates creating tag-based deny constraints:

```bash
# Add a deny for "locked" tag to an existing role
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/deny-tagged-assets.json \
    --role-name my-project-admin --var TAG_VALUE=locked --dry-run

# Apply to a user role too
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/deny-tagged-assets.json \
    --role-name my-project-user --var TAG_VALUE=locked

# Stack multiple tag denies
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/deny-tagged-assets.json \
    --role-name my-project-admin --var TAG_VALUE=approved
```

#### Example: Deny Archived Asset Deletion

Another common pattern is preventing deletion of assets that have been archived (soft deleted) to require admin review:

```json
{
    "name": "deny-archived-asset-delete",
    "description": "Prevent users from deleting assets tagged as archived",
    "objectType": "asset",
    "criteriaAnd": [{ "field": "tags", "id": "tag1", "operator": "contains", "value": "archived" }],
    "groupPermissions": [
        {
            "groupId": "my-project-user",
            "id": "deny-del",
            "permission": "DELETE",
            "permissionType": "deny"
        }
    ]
}
```

### Common Pitfalls

1. **Forgetting the `asset` constraint when locking down a database.** The `database` objectType only controls the database entity record. Assets within it are a separate objectType with their own `databaseId` criteria check.

2. **Not including API route constraints.** Without Tier 1 API constraints, the user cannot call any API endpoints, even if they have Tier 2 data constraints.

3. **Not including web route constraints.** Without web constraints, the UI navigation will hide pages from the user, though API access would still work if API constraints are configured.

4. **Missing non-mutating POST routes for read-only roles.** Routes like `/search` and `/auth/routes` use POST but are non-mutating. Read-only roles must allow POST on these specific paths for the UI to function.

5. **Using `criteriaAnd` when you need `criteriaOr` for multiple databases.** If you put multiple `databaseId equals X` conditions in `criteriaAnd`, they will never match because a single entity can only have one databaseId value. Use `criteriaOr` for multi-database access.

### Automation with Permission Templates

For environments with many databases and user roles, manually creating all these constraints is error-prone. The `tools/permissionsSetup` directory contains a Python-based tool that loads JSON templates and imports complete constraint sets via the `vamscli role constraint template import` CLI command (which calls the `POST /auth/constraintsTemplateImport` API). Pre-built JSON templates are available in `documentation/permissionsTemplates/`. See the README in `tools/permissionsSetup` for usage instructions.

### Permission Templates API

The `POST /auth/constraintsTemplateImport` endpoint allows you to import a complete set of constraints from a JSON permission template in a single API call. This is the recommended approach for programmatic constraint setup.

**How it works:** You send a JSON template (with variable values filled in) to the API, and it creates all constraints in DynamoDB with the correct denormalized format. The API handles UUID generation, groupPermission mapping, and variable substitution.

**Important:** This API creates **constraints only** -- it does not create roles or assign users to roles. You must create the role and assign users separately via the `/roles` and `/user-roles` APIs.

#### JSON Template Format

Templates are self-describing JSON files that contain metadata, variable definitions, and constraint definitions:

```json
{
    "metadata": {
        "name": "Database Admin",
        "description": "Administrative access for a specific database",
        "version": "1.0"
    },
    "variables": [
        {
            "name": "DATABASE_ID",
            "required": true,
            "description": "The databaseId to scope permissions to"
        },
        { "name": "ROLE_NAME", "required": true, "description": "The role name to create" }
    ],
    "constraints": [
        {
            "name": "{{ROLE_NAME}}-web-routes",
            "description": "Allow navigation to all standard pages for {{ROLE_NAME}}",
            "objectType": "web",
            "criteriaAnd": [],
            "criteriaOr": [
                { "field": "route__path", "operator": "starts_with", "value": "/assets" }
            ],
            "groupPermissions": [{ "action": "GET", "type": "allow" }]
        }
    ]
}
```

Key differences from the constraint creation API:

-   `groupPermissions` use `action`/`type` (template format) instead of `permission`/`permissionType` (API format)
-   No `identifier`, `groupId`, or permission `id` fields needed -- the API generates these automatically
-   `{{VARIABLE}}` placeholders are replaced with values from `variableValues`

#### Available Templates

Pre-built JSON templates are available in `documentation/permissionsTemplates/`:

| Template           | File                      | Variables                  | Description                                                    |
| ------------------ | ------------------------- | -------------------------- | -------------------------------------------------------------- |
| Database Admin     | `database-admin.json`     | `DATABASE_ID`, `ROLE_NAME` | Full management of a specific database (13 constraints)        |
| Database User      | `database-user.json`      | `DATABASE_ID`, `ROLE_NAME` | Standard user access with archive-only delete (15 constraints) |
| Database Read-Only | `database-readonly.json`  | `DATABASE_ID`, `ROLE_NAME` | View-only access to a specific database (10 constraints)       |
| Global Read-Only   | `global-readonly.json`    | `ROLE_NAME`                | Read-only access across all databases (10 constraints)         |
| Deny Tagged Assets | `deny-tagged-assets.json` | `ROLE_NAME`, `TAG_VALUE`   | Overlay: deny editing of tagged assets (1 constraint)          |

#### Example API Call

```bash
# Import the database-admin template for a specific database
curl -X POST https://your-api/auth/constraintsTemplateImport \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "template": {
      "name": "Database Admin",
      "description": "Administrative access for my-project-db",
      "version": "1.0"
    },
    "variables": [
      {"name": "DATABASE_ID", "required": true, "description": "The databaseId to scope permissions to"},
      {"name": "ROLE_NAME", "required": true, "description": "The role name to create"}
    ],
    "variableValues": {
      "DATABASE_ID": "my-project-db",
      "ROLE_NAME": "my-project-admin"
    },
    "constraints": [ ... ]
  }'
```

The response includes the count and IDs of all created constraints:

```json
{
    "success": true,
    "message": "Successfully imported 13 constraints from template 'Database Admin' for role 'my-project-admin'",
    "constraintsCreated": 13,
    "constraintIds": ["uuid-1", "uuid-2", "..."],
    "timestamp": "2024-01-01T00:00:00.000000"
}
```

**Tip:** You can post the entire contents of a JSON template file as the request body, just add the `variableValues` field with your specific values.

### Default Role Reference

The system deploys these roles by default:

| Role            | Description          | Access Level                                                             |
| --------------- | -------------------- | ------------------------------------------------------------------------ |
| `admin`         | System Administrator | Full CRUD on all objectTypes across all databases                        |
| `basicReadOnly` | Basic Read-Only User | GET-only on all objectTypes across all databases, limited web/API routes |

To see the exact constraints deployed for each default role, refer to the CDK constructs in `infra/lib/nestedStacks/auth/constructs/dynamodb-authdefaults-*.ts`.

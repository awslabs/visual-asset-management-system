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
-   -   Metadata Field [`field`] (field)

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
-   `/comments`
-   `/databases`
-   `/databases/:databaseId/assets`
-   `/databases/:databaseId/assets/:assetId`
-   `/databases/:databaseId/assets/:assetId/file`
-   `/databases/:databaseId/assets/:assetId/uploads`
-   `/databases/:databaseId/pipelines`
-   `/databases/:databaseId/workflows`
-   `/databases/:databaseId/workflows/:workflowId`
-   `/metadataschema/:databaseId/create`
-   `/metadataschema/create`
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
-   `/asset-links/{assetId}` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/asset-links/{relationId}` - DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/asset-links` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   `/auth/constraints` - GET
-   `/auth/constraints/{constraintId}` - GET/PUT/POST/DELETE
-   `/auth/routes` - POST (No API authorization logic checks on base call) (POST considered non-mutating to retrieve data only)
-   `/auth/scopeds3access` - POST
-   -   `Asset` (assetId, databaseId) - POST (api: POST)
-   `/comments/assets/{assetId}` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/comments/assets/{assetId}/assetVersionId/{assetVersionId}` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}` - GET/PUT/POST/DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: PUT)
-   `/databases` - GET/PUT
-   -   `Database` (databaseId) - GET (api: GET)
-   -   `Database` (databaseId) - PUT (api: PUT)
-   `/databases/{databaseId}` - GET/DELETE
-   -   `Database` (databaseId) - GET (api: GET)
-   -   `Database` (databaseId) - DELETE (api: DELETE)
-   `/database/{databaseId}/assets` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}` - GET/DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/database/{databaseId}/assets/{assetId}/listFiles` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/columns` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/database/{databaseId}/assets/{assetId}/metadata` - GET
-   `/database/{databaseId}/assets/{assetId}/revert` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: POST)
-   `/database/{databaseId}/assets/{assetId}/download` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: POST)
-   `/database/{databaseId}/assets/{assetId}/workflows/{workflowId}` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   -   `Workflow` (databaseId, workflowId) - POST (api: POST)
-   -   `Pipeline` (databaseId, pipelineId, pipelineType) - POST (api: POST)
-   `/database/{databaseId}/assets/{assetId}/workflows/{workflowId}/executions` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Workflow` (databaseId, workflowId) - GET (api: GET)
-   `/database/{databaseId}/pipelines` - GET
-   -   `Pipeline` (databaseId, pipelineId, pipelineType) - GET (api: GET)
-   `/database/{databaseId}/workflows` - GET
-   -   `Workflow` (databaseId, workflowId) - GET (api: GET)
-   `/database/{databaseId}/pipelines/{pipelineId}` - GET/DELETE
-   -   `Pipeline` (databaseId, pipelineId, pipelineType) - GET (api: GET)
-   -   `Pipeline` (databaseId, pipelineId, pipelineType) - DELETE (api: DELETE)
-   `/database/{databaseId}/workflows/{workflowId}` - GET/DELETE
-   -   `Workflow` (databaseId, workflowId) - GET (api: GET)
-   -   `Workflow` (databaseId, workflowId) - DELETE (api: DELETE)
-   `/assets` - GET/PUT
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: PUT)
-   `/assets/uploadAssetWorkflow` - POST
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: POST)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   `/ingest-asset` - POST
-   -   `Asset` (assetId, assetName databaseId) - PUT (api: POST)
-   `/metadata/{databaseId}/{assetId}` - GET/PUT/POST/DELETE
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - PUT (api: PUT)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - POST (api: POST)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - DELETE (api: DELETE)
-   `/metadataschema/{databaseId}` - GET/PUT/POST
-   -   `MetadataSchema` (databaseId, field) - GET (api: GET)
-   -   `MetadataSchema` (databaseId, field) - POST (api: PUT)
-   -   `MetadataSchema` (databaseId, field) - POST (api: POST)
-   `/metadataschema/{databaseId}/{field}` - DELETE
-   -   `MetadataSchema` (databaseId, field) - DELETE (api: DELETE)
-   `/pipelines` - GET/PUT
-   -   `Pipeline` (databaseId, pipelineId, pipelineType) - PUT (api: PUT)
-   -   `Pipeline` (databaseId, pipelineId, pipelineType) - GET (api: GET)
-   `/roles` - GET/PUT/POST
-   -   `Role` (roleName) - GET (api: GET)
-   -   `Role` (roleName) - POST (api: POST)
-   -   `Role` (roleName) - PUT (api: PUT)
-   `/roles/{roleId}` - DELETE
-   -   `Role` (roleName) - DELETE (api: DELETE)
-   `/search` - GET/POST (Both GET/POST considered non-mutating to retrieve data only)
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET/POST)
-   `/secure-config` - GET (No API authorization logic checks on base call)
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
-   `/user-roles` - GET/PUT/POST/DELETE
-   -   `UserRole` (roleName, userId) - GET (api: GET)
-   -   `UserRole` (roleName, userId) - POST (api: POST)
-   -   `UserRole` (roleName, userId) - PUT (api: PUT)
-   -   `UserRole` (roleName, userId) - DELETE (api: DELETE)
-   `/visualizerAssets/{proxy+}` - GET
-   -   `Asset` (assetId, assetName databaseId, assetType, tags) - GET (api: GET)
-   `/workflows` - GET/PUT
-   -   `Pipeline` (databaseId, pipelineId, pipelineType) - GET (api: PUT)
-   -   `Workflow` (databaseId, workflowId) - GET (api: GET)
-   -   `Workflow` (databaseId, workflowId) - PUT (api: PUT)

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

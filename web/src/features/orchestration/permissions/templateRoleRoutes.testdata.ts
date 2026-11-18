/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Test data: what `GET /auth/routes/api/allowed` lists, for the routes the database, asset, search and
 * API key pages gate their actions on, to a role built from each shipped permission template
 * (`documentation/permissionsTemplates/`). The backend test
 * `backend/tests/handlers/auth/test_permission_templates_web_action_routes.py` checks the same
 * outcomes against the real templates and Casbin evaluation, so this file and the templates move
 * together.
 */

interface ListedRoute {
    path: string;
    methods: string[];
    category: string;
}

const route = (path: string, methods: string[], category = "test"): ListedRoute => ({
    path,
    methods,
    category,
});

/** A role from the `database-readonly` template: reads only, on these routes. */
export const DATABASE_READONLY_ROUTES: ListedRoute[] = [
    route("/database", ["GET"]),
    route("/database/{databaseId}", ["GET"]),
    route("/assets", ["GET"]),
    route("/database/{databaseId}/assets/{assetId}", ["GET"]),
    route("/database/{databaseId}/assets/{assetId}/metadata", ["GET"]),
    route("/auth/user/api-keys", ["GET"]),
    route("/auth/user/api-keys/{apiKeyId}", ["GET"]),
];

/** A role from the `database-user` template: creates, uploads, archives and edits metadata. */
export const DATABASE_USER_ROUTES: ListedRoute[] = [
    route("/database", ["GET"]),
    route("/database/{databaseId}", ["GET"]),
    route("/assets", ["GET", "POST"]),
    route("/uploads", ["POST"]),
    route("/database/{databaseId}/assets/{assetId}", ["GET"]),
    route("/database/{databaseId}/assets/{assetId}/archiveAsset", ["DELETE"]),
    route("/database/{databaseId}/assets/{assetId}/metadata", ["GET", "POST"]),
    route("/auth/user/api-keys", ["GET", "POST"]),
    route("/auth/user/api-keys/{apiKeyId}", ["GET", "PUT", "DELETE"]),
];

/**
 * The administrator role: every method on the routes listed here. It leaves out `deleteAsset`, so a
 * test that needs permanent delete adds that route to this listing itself.
 */
export const ADMIN_ROUTES: ListedRoute[] = [
    route("/database", ["GET", "POST"]),
    route("/database/{databaseId}", ["GET", "PUT", "DELETE"]),
    route("/assets", ["GET", "POST"]),
    route("/uploads", ["POST"]),
    route("/database/{databaseId}/assets/{assetId}", ["GET", "PUT"]),
    route("/database/{databaseId}/assets/{assetId}/archiveAsset", ["DELETE"]),
    route("/database/{databaseId}/assets/{assetId}/metadata", ["GET", "POST", "PUT", "DELETE"]),
    route("/auth/api-keys", ["GET", "POST"]),
    route("/auth/api-keys/{apiKeyId}", ["GET", "PUT", "DELETE"]),
    route("/auth/user/api-keys", ["GET", "POST"]),
    route("/auth/user/api-keys/{apiKeyId}", ["GET", "PUT", "DELETE"]),
];

/** The `fetchAllowedApiRoutes` result for a role's listing. */
export const allowedRoutesResponse = (routes: ListedRoute[]) => [
    true,
    { routes, userId: "template-user" },
];

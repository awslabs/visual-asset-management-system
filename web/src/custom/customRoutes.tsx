/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
 * do not conflict on upgrade.
 *
 * Web routes for the pages a fork adds. routes.tsx appends these to its route table, so each one
 * passes through the same `web`-constraint check (checkWebRoutesAllowed) as a core page and is
 * offered in the constraint editor's route list. Pages live in src/custom/pages/ and are loaded with
 * React.lazy like the core pages; paths under `/custom/` are recommended so a later core route does
 * not collide with one declared here.
 */

import type { RouteOption } from "../routes";

export const customRouteTable: RouteOption[] = [];

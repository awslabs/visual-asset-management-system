/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as Config from "../../../config/config";

/**
 * `route__path` criteria the enabled add-ons add to the basic read-only role's
 * "Allow GET on API Paths" constraint (`dynamodb-authdefaults-ro-construct.ts`),
 * in that constraint's DynamoDB attribute-value shape.
 */
export function addonReadOnlyApiRouteCriteria(config: Config.Config, roleIdClean: string): any[] {
    return [
        ...(config.app.addons.usePhysnaSync.enabled
            ? [
                  {
                      M: {
                          field: {
                              S: "route__path",
                          },
                          id: {
                              S: `16_${roleIdClean}_api_paths`,
                          },
                          operator: {
                              S: "starts_with",
                          },
                          value: {
                              S: "/addon/physna/viewer", //Physna viewer proxy (per-asset authorization is enforced server-side)
                          },
                      },
                  },
              ]
            : []),
        ...(config.app.addons?.useEdhWorkstations?.enabled
            ? [
                  {
                      M: {
                          field: {
                              S: "route__path",
                          },
                          id: {
                              S: `17_${roleIdClean}_api_paths`,
                          },
                          operator: {
                              S: "starts_with",
                          },
                          value: {
                              S: "/addon/edh/workstations",
                          },
                      },
                  },
                  {
                      M: {
                          field: {
                              S: "route__path",
                          },
                          id: {
                              S: `18_${roleIdClean}_api_paths`,
                          },
                          operator: {
                              S: "starts_with",
                          },
                          value: {
                              S: "/addon/edh/catalog",
                          },
                      },
                  },
              ]
            : []),
    ];
}

/**
 * `route__path` criteria the enabled add-ons add to the basic read-only role's
 * "Allow GET on Web Paths" constraint (`dynamodb-authdefaults-ro-construct.ts`),
 * in that constraint's DynamoDB attribute-value shape.
 */
export function addonReadOnlyWebRouteCriteria(config: Config.Config, roleIdClean: string): any[] {
    return [
        ...(config.app.addons?.useEdhWorkstations?.enabled
            ? [
                  {
                      M: {
                          field: {
                              S: "route__path",
                          },
                          id: {
                              S: `9_${roleIdClean}_web_paths`,
                          },
                          operator: {
                              S: "starts_with",
                          },
                          value: {
                              S: "/edh/workstations",
                          },
                      },
                  },
              ]
            : []),
    ];
}

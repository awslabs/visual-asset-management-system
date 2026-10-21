/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as Config from "../../../config/config";

/**
 * Constraints the enabled add-ons seed on the admin role for the permission object types they
 * declare (backend common/addonConstants.py), in the DynamoDB attribute-value shape of
 * `dynamodb-authdefaults-admin-construct.ts`. The construct appends them after its core
 * constraints, so the index-based ids of its custom resources stay those of the core items.
 */
export function addonAdminDefaultConstraints(config: Config.Config, roleName: string): any[] {
    return [
        ...(config.app.addons?.useEdhWorkstations?.enabled
            ? [
                  {
                      entityType: {
                          S: "constraint",
                      },
                      sk: {
                          S: "initial_admin_allow_all_edhworkstations",
                      },
                      constraintId: {
                          S: "initial_admin_allow_all_edhworkstations",
                      },
                      criteriaAnd: {
                          L: [
                              {
                                  M: {
                                      field: {
                                          S: "edhImageId",
                                      },
                                      id: {
                                          S: "all_edhworkstations",
                                      },
                                      operator: {
                                          S: "contains",
                                      },
                                      value: {
                                          S: ".*",
                                      },
                                  },
                              },
                          ],
                      },
                      description: {
                          S: "Allow GET/PUT/POST/DELETE on all EDH workstations for admin",
                      },
                      groupPermissions: {
                          L: [
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-get-all-edhworkstations",
                                      },
                                      permission: {
                                          S: "GET",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-put-all-edhworkstations",
                                      },
                                      permission: {
                                          S: "PUT",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-post-all-edhworkstations",
                                      },
                                      permission: {
                                          S: "POST",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-delete-all-edhworkstations",
                                      },
                                      permission: {
                                          S: "DELETE",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                          ],
                      },
                      name: {
                          S: "admin-allow-all-edhworkstations",
                      },
                      objectType: {
                          S: "edhWorkstation",
                      },
                  },
                  {
                      entityType: {
                          S: "constraint",
                      },
                      sk: {
                          S: "initial_admin_allow_all_edhimages",
                      },
                      constraintId: {
                          S: "initial_admin_allow_all_edhimages",
                      },
                      criteriaAnd: {
                          L: [
                              {
                                  M: {
                                      field: {
                                          S: "edhImageId",
                                      },
                                      id: {
                                          S: "all_edhimages",
                                      },
                                      operator: {
                                          S: "contains",
                                      },
                                      value: {
                                          S: ".*",
                                      },
                                  },
                              },
                          ],
                      },
                      description: {
                          S: "Allow GET/PUT/POST/DELETE on all EDH images for admin",
                      },
                      groupPermissions: {
                          L: [
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-get-all-edhimages",
                                      },
                                      permission: {
                                          S: "GET",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-put-all-edhimages",
                                      },
                                      permission: {
                                          S: "PUT",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-post-all-edhimages",
                                      },
                                      permission: {
                                          S: "POST",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-delete-all-edhimages",
                                      },
                                      permission: {
                                          S: "DELETE",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                          ],
                      },
                      name: {
                          S: "admin-allow-all-edhimages",
                      },
                      objectType: {
                          S: "edhImage",
                      },
                  },
                  {
                      entityType: {
                          S: "constraint",
                      },
                      sk: {
                          S: "initial_admin_allow_all_edhcontainers",
                      },
                      constraintId: {
                          S: "initial_admin_allow_all_edhcontainers",
                      },
                      criteriaAnd: {
                          L: [
                              {
                                  M: {
                                      field: {
                                          S: "edhJobDefinition",
                                      },
                                      id: {
                                          S: "all_edhcontainers",
                                      },
                                      operator: {
                                          S: "contains",
                                      },
                                      value: {
                                          S: ".*",
                                      },
                                  },
                              },
                          ],
                      },
                      description: {
                          S: "Allow GET/PUT/POST/DELETE on all EDH container jobs for admin",
                      },
                      groupPermissions: {
                          L: [
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-get-all-edhcontainers",
                                      },
                                      permission: {
                                          S: "GET",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-put-all-edhcontainers",
                                      },
                                      permission: {
                                          S: "PUT",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-post-all-edhcontainers",
                                      },
                                      permission: {
                                          S: "POST",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: "admin-allow-delete-all-edhcontainers",
                                      },
                                      permission: {
                                          S: "DELETE",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                          ],
                      },
                      name: {
                          S: "admin-allow-all-edhcontainers",
                      },
                      objectType: {
                          S: "edhContainer",
                      },
                  },
              ]
            : []),
    ];
}

/**
 * Constraints the enabled add-ons seed on the basic read-only role for the permission object types
 * they declare, in the DynamoDB attribute-value shape of `dynamodb-authdefaults-ro-construct.ts`,
 * which appends them after its core constraints.
 */
export function addonReadOnlyDefaultConstraints(
    config: Config.Config,
    roleName: string,
    roleIdClean: string
): any[] {
    return [
        // EDH Workstations add-on: GET on every workstation and catalog image. Ownership of a
        // workstation is enforced by the service from the session record, so the match is broad.
        ...(config.app.addons?.useEdhWorkstations?.enabled
            ? [
                  {
                      entityType: {
                          S: "constraint",
                      },
                      sk: {
                          S: `initial_${roleIdClean}_allow_get_edhworkstations`,
                      },
                      constraintId: {
                          S: `initial_${roleIdClean}_allow_get_edhworkstations`,
                      },
                      criteriaAnd: {
                          L: [
                              {
                                  M: {
                                      field: {
                                          S: "edhImageId",
                                      },
                                      id: {
                                          S: `${roleIdClean}_all_edhworkstations`,
                                      },
                                      operator: {
                                          S: "contains",
                                      },
                                      value: {
                                          S: ".*",
                                      },
                                  },
                              },
                          ],
                      },
                      description: {
                          S: "Allow GET on EDH workstations for basic read only user (own workstations only; ownership is enforced by the service)",
                      },
                      groupPermissions: {
                          L: [
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: `${roleIdClean}-allow-get-all-edhworkstations`,
                                      },
                                      permission: {
                                          S: "GET",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                          ],
                      },
                      name: {
                          S: `${roleIdClean}-allow-get-all-edhworkstations`,
                      },
                      objectType: {
                          S: "edhWorkstation",
                      },
                  },
                  {
                      entityType: {
                          S: "constraint",
                      },
                      sk: {
                          S: `initial_${roleIdClean}_allow_get_edhimages`,
                      },
                      constraintId: {
                          S: `initial_${roleIdClean}_allow_get_edhimages`,
                      },
                      criteriaAnd: {
                          L: [
                              {
                                  M: {
                                      field: {
                                          S: "edhImageId",
                                      },
                                      id: {
                                          S: `${roleIdClean}_all_edhimages`,
                                      },
                                      operator: {
                                          S: "contains",
                                      },
                                      value: {
                                          S: ".*",
                                      },
                                  },
                              },
                          ],
                      },
                      description: {
                          S: "Allow GET on all EDH catalog images for basic read only user",
                      },
                      groupPermissions: {
                          L: [
                              {
                                  M: {
                                      groupId: {
                                          S: roleName,
                                      },
                                      id: {
                                          S: `${roleIdClean}-allow-get-all-edhimages`,
                                      },
                                      permission: {
                                          S: "GET",
                                      },
                                      permissionType: {
                                          S: "allow",
                                      },
                                  },
                              },
                          ],
                      },
                      name: {
                          S: `${roleIdClean}-allow-get-all-edhimages`,
                      },
                      objectType: {
                          S: "edhImage",
                      },
                  },
              ]
            : []),
    ];
}

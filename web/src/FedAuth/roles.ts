/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { Auth } from "aws-amplify";

export async function anyRoleOf(roles: string[]) {
    try {
        const user = await Auth.currentSession();
        const userRoles = JSON.parse(user.getIdToken().payload["vams:roles"]);
        return userRoles.some((r: string) => roles.includes(r));
    } catch (e) {
        console.log("error checking user roles", e);
        return false;
    }
}

export default async function checkRole(role: string) {
    try {
        const user = await Auth.currentSession();
        const roles = JSON.parse(user.getIdToken().payload["vams:roles"]);
        return roles.includes(role);
    } catch (e) {
        console.log("error checking user roles", e);
        return false;
    }
}

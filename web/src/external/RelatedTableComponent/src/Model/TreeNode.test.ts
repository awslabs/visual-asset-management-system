/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ExpandableTableNodeStatus, TreeNode } from "./TreeNode";

describe("TreeUtility", () => {
    it("createNodes", () => {
        const item = {
            id: "ae65323e-6778-420b-ac1a-eff7a8950300",
            children: [],
        };
        const node = new TreeNode(item);
        expect(node.getStatus()).toEqual(ExpandableTableNodeStatus.normal);
    });
});

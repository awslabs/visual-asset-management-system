/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { validateFileExtensions } from "./CreateDatabase";

// The form accepts what the API's database model accepts (issue #371 IF-044): letters, digits,
// "_" and further dots after the leading dot.
describe("CreateDatabase: Restrict File Upload Extensions", () => {
    it.each([".x_t,.x_b", ".X_T", ".tar.gz", ".step,.x_t,.tar.gz", ".glb,.gltf,.obj", ".all", ""])(
        "accepts %p",
        (value) => {
            expect(validateFileExtensions(value)).toBeNull();
        }
    );

    it.each([".x-t", ".a b", ".x/t", "x_t", "."])("refuses %p", (value) => {
        expect(validateFileExtensions(value)).not.toBeNull();
    });
});

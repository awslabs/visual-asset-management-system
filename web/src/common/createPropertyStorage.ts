/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const promisify = (ctx, fn) =>
    function (...args) {
        const result = fn.apply(ctx, [this.name, ...args]);
        return result && result.then ? result : Promise.resolve(result);
    };

export const createPropertyStorage = (name, storage) => ({
    name,
    save: promisify(storage, storage.save),
    load: promisify(storage, storage.load),
});

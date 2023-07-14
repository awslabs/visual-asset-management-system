/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const and = (a, b) => a && b;
const or = (a, b) => a || b;
const not = (a) => !a;
const exact = (value, pattern) => value === pattern;
const partial = (value, pattern) =>
    typeof pattern === "string" &&
    typeof value === "string" &&
    pattern.toLowerCase().indexOf(value.toLowerCase()) > -1;

export function buildMatcher(tokens, operation) {
    if (!tokens.length) {
        return () => true;
    }
    const matchers = tokens.map(({ isFreeText, value, negated, propertyKey }) => {
        return (item) => {
            const keys = isFreeText ? Object.keys(item) : [propertyKey];
            const intermediate = keys.some((key) =>
                isFreeText ? partial(value, item[key]) : exact(value, item[key])
            );
            return negated ? not(intermediate) : intermediate;
        };
    });
    const reducer = (matchers) => {
        return (item) => {
            return matchers.reduce(
                (acc, matcher) =>
                    operation === "or" ? or(acc, matcher(item)) : and(acc, matcher(item)),
                operation === "or" ? false : true
            );
        };
    };
    return reducer(matchers);
}

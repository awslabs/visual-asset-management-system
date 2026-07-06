/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const and = (a: any, b: any) => a && b;
const or = (a: any, b: any) => a || b;
const not = (a: any) => !a;
const exact = (value: any, pattern: any) => value === pattern;
const partial = (value: any, pattern: any) =>
    typeof pattern === "string" &&
    typeof value === "string" &&
    pattern.toLowerCase().indexOf(value.toLowerCase()) > -1;

export function buildMatcher(tokens: any, operation: any) {
    if (!tokens.length) {
        return () => true;
    }
    const matchers = tokens.map(
        ({
            isFreeText,
            value,
            negated,
            propertyKey,
        }: {
            isFreeText: any;
            value: any;
            negated: any;
            propertyKey: any;
        }) => {
            return (item: any) => {
                const keys = isFreeText ? Object.keys(item) : [propertyKey];
                const intermediate = keys.some((key) =>
                    isFreeText ? partial(value, item[key]) : exact(value, item[key])
                );
                return negated ? not(intermediate) : intermediate;
            };
        }
    );
    const reducer = (matchers: any) => {
        return (item: any) => {
            return matchers.reduce(
                (acc: any, matcher: any) =>
                    operation === "or" ? or(acc, matcher(item)) : and(acc, matcher(item)),
                operation === "or" ? false : true
            );
        };
    };
    return reducer(matchers);
}

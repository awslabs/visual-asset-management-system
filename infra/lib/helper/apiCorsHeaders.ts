/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The CORS values the REST API emits, shared with the AWS WAF rate-limit custom response.
 *
 * `rest-api-gateway-construct.ts` emits these on every `OPTIONS` preflight (through the OpenAPI
 * spec's MOCK integration in `buildOpenApiSpec.ts`) and on the `GatewayResponseDefault4XX/5XX`
 * gateway responses. `wafv2-basic-construct.ts` emits the same values on the rate rule's 429
 * custom response, because AWS WAF answers a throttled request before API Gateway and none of the
 * API's own CORS injection applies to it. One definition here is what keeps the two identical;
 * `test/waf/wafRateLimit.test.ts` asserts the WAF response against these constants.
 *
 * This module has no imports on purpose: the WAF stack and its unit tests load it without pulling
 * in the API nested stack's module graph.
 */

/** `Access-Control-Allow-Headers` value: the request headers VAMS clients send cross-origin. */
export const API_CORS_ALLOW_HEADERS =
    "Authorization,Content-Type,Origin,Range,X-Amz-Date,X-Api-Key,X-Amz-Security-Token,X-Amz-User-Agent,Access-Control-Allow-Origin";

/** `Access-Control-Allow-Methods` value: every method the API's routes may use. */
export const API_CORS_ALLOW_METHODS = "GET,POST,PUT,PATCH,DELETE,OPTIONS,HEAD";

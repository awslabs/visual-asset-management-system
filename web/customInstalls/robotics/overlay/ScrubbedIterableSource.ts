/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAMS-authored wrapper for the robotics viewer build, copied into the Lichtblick clone by
 * web/customInstalls/robotics/roboticsViewerInstall.js.
 *
 * Upstream readers put the URL they failed on into their error text ("Fetching remote file failed.
 * <url> Status code: 403", "GET <url> failed with status 403"), and the app shows that text in its
 * alerts. For a presigned URL that text is a working credential on screen. This wrapper sits
 * between a source and the player and rewrites every error, alert and initialization result that
 * crosses it: each of the selection's URLs becomes its file name, any other signed URL is removed.
 */

import type { Immutable, MessageEvent, Time } from "@lichtblick/suite";
import type {
    GetBackfillMessagesArgs,
    IIterableSource,
    IMessageCursor,
    Initialization,
    IteratorResult,
    MessageIteratorArgs,
} from "@lichtblick/suite-base/players/IterablePlayer/IIterableSource";

import { scrubAlert, scrubError } from "./vamsSourceSafety";

/**
 * Wraps `source` so nothing it reports carries one of `urls`. Methods not listed here (sourceType,
 * getStart, getEnd, terminate, prewarm) pass through, bound to the wrapped source.
 */
export function withScrubbedErrors<T extends IIterableSource<unknown>>(
    source: T,
    urls: readonly string[],
    names: readonly string[]
): T {
    const scrub = (error: unknown): unknown => scrubError(error, urls, names);
    const scrubResult = (
        result: Readonly<IteratorResult<unknown>>
    ): Readonly<IteratorResult<unknown>> =>
        result.type === "alert"
            ? { ...result, alert: scrubAlert(result.alert, urls, names) }
            : result;
    const scrubResults = (
        results: Readonly<IteratorResult<unknown>>[] | undefined
    ): Readonly<IteratorResult<unknown>>[] | undefined =>
        results?.some((result) => result.type === "alert") === true
            ? results.map(scrubResult)
            : results;

    const initialize = async (): Promise<Initialization> => {
        try {
            const initialization = await source.initialize();
            return {
                ...initialization,
                // The player shows Initialization.name in place of its own when one is set; its own
                // is the file names.
                name: undefined,
                alerts: initialization.alerts.map((alert) => scrubAlert(alert, urls, names)),
            };
        } catch (error) {
            throw scrub(error);
        }
    };

    const messageIterator = (
        args: Immutable<MessageIteratorArgs>
    ): AsyncIterableIterator<Readonly<IteratorResult<unknown>>> => {
        const inner = source.messageIterator(args);
        return (async function* scrubbedMessages() {
            try {
                for (;;) {
                    const step = await inner.next();
                    if (step.done === true) {
                        return;
                    }
                    yield scrubResult(step.value);
                }
            } catch (error) {
                throw scrub(error);
            } finally {
                await inner.return?.();
            }
        })();
    };

    const getBackfillMessages = async (
        args: Immutable<GetBackfillMessagesArgs>
    ): Promise<MessageEvent<unknown>[]> => {
        try {
            return await source.getBackfillMessages(args);
        } catch (error) {
            throw scrub(error);
        }
    };

    const innerGetMessageCursor = source.getMessageCursor?.bind(source);
    const getMessageCursor =
        innerGetMessageCursor == undefined
            ? undefined
            : (
                  args: Immutable<MessageIteratorArgs> & { abort?: AbortSignal }
              ): IMessageCursor<unknown> => {
                  const cursor = innerGetMessageCursor(args);
                  return {
                      next: async () => {
                          try {
                              const result = await cursor.next();
                              return result == undefined ? undefined : scrubResult(result);
                          } catch (error) {
                              throw scrub(error);
                          }
                      },
                      nextBatch: async (durationMs: number) => {
                          try {
                              return scrubResults(await cursor.nextBatch(durationMs));
                          } catch (error) {
                              throw scrub(error);
                          }
                      },
                      readUntil: async (end: Time) => {
                          try {
                              return scrubResults(await cursor.readUntil(end));
                          } catch (error) {
                              throw scrub(error);
                          }
                      },
                      end: async () => {
                          await cursor.end();
                      },
                  };
              };

    const overrides: Record<string | symbol, unknown> = {
        initialize,
        messageIterator,
        getBackfillMessages,
        ...(getMessageCursor == undefined ? {} : { getMessageCursor }),
    };

    return new Proxy(source, {
        get(target, property) {
            if (Object.prototype.hasOwnProperty.call(overrides, property)) {
                return overrides[property];
            }
            // Bound to the wrapped source: its methods read private (#) fields, which a call with
            // the proxy as `this` cannot reach.
            const value: unknown = Reflect.get(target, property, target);
            return typeof value === "function"
                ? (value as (...args: unknown[]) => unknown).bind(target)
                : value;
        },
    });
}

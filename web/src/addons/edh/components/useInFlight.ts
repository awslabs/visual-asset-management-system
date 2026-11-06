/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useRef, useState } from "react";

/**
 * A synchronous guard against sending a request twice. `claim(key, value)` records a request for
 * `key` and returns false while one is already in flight; `release(key)` ends it. A mutation's
 * `isPending` changes only once the query client's notification has rendered, so both clicks of a
 * double-click pass a check on it; the claim is held in a ref, set before the second click arrives.
 * `inFlight` re-renders with the claims, for disabling the controls that would send another.
 */
export function useInFlight<T = undefined>() {
    const claims = useRef(new Map<string, T | undefined>());
    const [inFlight, setInFlight] = useState<ReadonlyMap<string, T | undefined>>(() => new Map());
    const claim = useCallback((key: string, value?: T) => {
        if (claims.current.has(key)) return false;
        claims.current.set(key, value);
        setInFlight(new Map(claims.current));
        return true;
    }, []);
    const release = useCallback((key: string) => {
        claims.current.delete(key);
        setInFlight(new Map(claims.current));
    }, []);
    return { inFlight, claim, release };
}

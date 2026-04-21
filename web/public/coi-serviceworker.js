/*! coi-serviceworker v0.1.7 - Guido Zuidhof and contributors, licensed under MIT */
/*
 * Modified for VAMS - Visual Asset Management System
 * This service worker adds Cross-Origin-Embedder-Policy (COEP) and Cross-Origin-Opener-Policy (COOP)
 * headers to enable SharedArrayBuffer and other cross-origin isolated features required for WASM.
 *
 * IMPORTANT: This file MUST be served from the root of the website (not a subdirectory)
 * to ensure it can control all pages. Service workers can only control pages within their scope.
 */

let coepCredentialless = true;

if (typeof window === "undefined") {
    // ============================================
    // SERVICE WORKER CONTEXT (runs in worker thread)
    // ============================================

    self.addEventListener("install", () => {
        //console.log("[COI Service Worker] Installing...");
        self.skipWaiting();
    });

    self.addEventListener("activate", (event) => {
        //console.log("[COI Service Worker] Activating...");
        event.waitUntil(self.clients.claim());
        console.log("[COI Service Worker] Activated and claimed all clients");
    });

    self.addEventListener("message", (ev) => {
        if (!ev.data) {
            return;
        } else if (ev.data.type === "deregister") {
            //console.log("[COI Service Worker] Deregistering...");
            self.registration
                .unregister()
                .then(() => {
                    return self.clients.matchAll();
                })
                .then((clients) => {
                    clients.forEach((client) => client.navigate(client.url));
                });
        } else if (ev.data.type === "coepCredentialless") {
            coepCredentialless = ev.data.value;
            //console.log("[COI Service Worker] COEP credentialless mode:", coepCredentialless);
        }
    });

    self.addEventListener("fetch", function (event) {
        const r = event.request;

        // Skip requests that can't be handled
        if (r.cache === "only-if-cached" && r.mode !== "same-origin") {
            return;
        }

        const request =
            coepCredentialless && r.mode === "no-cors"
                ? new Request(r, {
                      credentials: "omit",
                  })
                : r;

        event.respondWith(
            fetch(request)
                .then((response) => {
                    if (response.status === 0) {
                        return response;
                    }

                    const newHeaders = new Headers(response.headers);
                    const coepValue = "credentialless";

                    newHeaders.set("Cross-Origin-Embedder-Policy", coepValue);
                    if (!coepCredentialless) {
                        newHeaders.set("Cross-Origin-Resource-Policy", "cross-origin");
                    }
                    newHeaders.set("Cross-Origin-Opener-Policy", "same-origin");

                    // Log header injection for navigation requests (main document loads)
                    // if (r.mode === "navigate" || r.destination === "document") {
                    //     console.log("[COI Service Worker] Headers injected for:", r.url);
                    //     console.log("  - Cross-Origin-Embedder-Policy:", coepValue);
                    //     console.log("  - Cross-Origin-Opener-Policy: same-origin");
                    // }

                    return new Response(response.body, {
                        status: response.status,
                        statusText: response.statusText,
                        headers: newHeaders,
                    });
                })
                .catch((e) => {
                    console.error("[COI Service Worker] Fetch error for:", r.url, e);
                    throw e;
                })
        );
    });
} else {
    // ============================================
    // WINDOW CONTEXT (runs in main page)
    // ============================================

    (() => {
        // EARLY EXIT: If the page is already cross-origin isolated (e.g., server set COOP/COEP headers),
        // there's no need for the service worker to do anything. This prevents unnecessary reloads
        // when deployed via CloudFront or other servers that set these headers natively.
        if (window.crossOriginIsolated === true) {
            console.log(
                "[COI Service Worker] Page is already cross-origin isolated (headers set by server). Service worker not needed."
            );
            return;
        }

        const reloadedBySelf = window.sessionStorage.getItem("coiReloadedBySelf");
        window.sessionStorage.removeItem("coiReloadedBySelf");
        const coepDegrading = reloadedBySelf === "coepdegrade";

        // Configuration options - can be customized via window.coi
        const coi = {
            shouldRegister: () => !reloadedBySelf,
            shouldDeregister: () => false,
            coepCredentialless: () => true,
            coepDegrade: () => true,
            doReload: () => window.location.reload(),
            quiet: true,
            ...window.coi,
        };

        const n = navigator;
        const controlling = n.serviceWorker && n.serviceWorker.controller;

        // // Log current state for debugging
        if (!coi.quiet) {
            console.log("[COI Service Worker] Window context initialized");
            console.log("  - crossOriginIsolated:", window.crossOriginIsolated);
            console.log("  - Service Worker controlling:", !!controlling);
            console.log("  - Previously reloaded by self:", reloadedBySelf);
            console.log("  - Secure context:", window.isSecureContext);
        }

        // Record the failure if the page is served by serviceWorker but not cross-origin isolated
        if (controlling && !window.crossOriginIsolated) {
            window.sessionStorage.setItem("coiCoepHasFailed", "true");
            !coi.quiet &&
                console.log("[COI Service Worker] COEP has failed despite service worker control");
        }
        const coepHasFailed = window.sessionStorage.getItem("coiCoepHasFailed");

        if (controlling) {
            // Reload only on the first failure
            const reloadToDegrade =
                coi.coepDegrade() && !(coepDegrading || window.crossOriginIsolated);

            n.serviceWorker.controller.postMessage({
                type: "coepCredentialless",
                value:
                    reloadToDegrade || (coepHasFailed && coi.coepDegrade())
                        ? false
                        : coi.coepCredentialless(),
            });

            if (reloadToDegrade) {
                !coi.quiet && console.log("[COI Service Worker] Reloading page to degrade COEP...");
                window.sessionStorage.setItem("coiReloadedBySelf", "coepdegrade");
                coi.doReload("coepdegrade");
                return;
            }

            if (coi.shouldDeregister()) {
                n.serviceWorker.controller.postMessage({ type: "deregister" });
            }
        }

        // If already cross-origin isolated, nothing more to do
        if (window.crossOriginIsolated !== false) {
            !coi.quiet && console.log("[COI Service Worker] Already cross-origin isolated!");
            return;
        }

        // Don't register if we shouldn't
        if (!coi.shouldRegister()) {
            !coi.quiet &&
                console.log(
                    "[COI Service Worker] Skipping registration (shouldRegister returned false)"
                );
            return;
        }

        // Check for secure context (required for service workers)
        if (!window.isSecureContext) {
            console.log(
                "[COI Service Worker] Cannot register - not a secure context (HTTPS required)"
            );
            return;
        }

        // Check for service worker support
        if (!n.serviceWorker) {
            console.error(
                "[COI Service Worker] Service workers not supported (possibly private browsing mode)"
            );
            return;
        }

        // Get the script path - we need to register from the root
        const currentScript = window.document.currentScript;
        if (!currentScript) {
            console.error("[COI Service Worker] Cannot determine script location");
            return;
        }

        const scriptUrl = new URL(currentScript.src, window.location.href);
        const scriptPath = scriptUrl.pathname;

        !coi.quiet &&
            console.log("[COI Service Worker] Registering service worker from:", scriptPath);

        // Register the service worker with root scope
        n.serviceWorker.register(scriptPath, { scope: "/" }).then(
            (registration) => {
                !coi.quiet && console.log("[COI Service Worker] Registered successfully");
                !coi.quiet && console.log("  - Scope:", registration.scope);
                !coi.quiet && console.log("  - Active:", !!registration.active);
                !coi.quiet && console.log("  - Installing:", !!registration.installing);
                !coi.quiet && console.log("  - Waiting:", !!registration.waiting);

                registration.addEventListener("updatefound", () => {
                    !coi.quiet && console.log("[COI Service Worker] Update found, will reload...");
                    window.sessionStorage.setItem("coiReloadedBySelf", "updatefound");
                    coi.doReload();
                });

                // If the registration is active, but it's not controlling the page, reload
                if (registration.active && !n.serviceWorker.controller) {
                    !coi.quiet &&
                        console.log(
                            "[COI Service Worker] Active but not controlling, reloading..."
                        );
                    window.sessionStorage.setItem("coiReloadedBySelf", "notcontrolling");
                    coi.doReload();
                }
            },
            (err) => {
                !coi.quiet && console.error("[COI Service Worker] Registration failed:", err);
                !coi.quiet && console.error("  - Script path:", scriptPath);
                !coi.quiet &&
                    console.error(
                        "  - Make sure the service worker file is served from the root of the website"
                    );
            }
        );
    })();
}

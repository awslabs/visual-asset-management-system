/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The web-application dependency tables in `NOTICE.md` must agree with what the web build actually
 * depends on.
 *
 * `NOTICE.md` is a hand-maintained attribution file, so nothing in the build reads it and nothing
 * fails when a row falls out of step with `web/package.json`. Left alone it drifts in BOTH
 * directions -- rows for packages that were removed years ago, and declared packages with no row --
 * and a licence column that was typed from memory rather than read from the package. An attribution
 * file that is wrong is a compliance defect, not a documentation nit, so this guard checks each table
 * against the manifests it describes:
 *
 * - "WEB APPLICATION" against `web/package.json` and `web/package-lock.json`.
 * - "WEB CUSTOM INSTALLS (3D VIEWERS)" and "PAID COMPONENTS - WEB VIEWERS" against every
 *   `web/customInstalls/<viewer>/package.json`, its lockfile, and the viewer install scripts (the
 *   git-cloned viewers have no manifest; their pin lives in the script).
 *
 * Licences are read from the installed package under `node_modules` where present and from the
 * tracked lockfile otherwise, never assumed from the package name.
 *
 * Durable guard (root CLAUDE.md Rule 13): every failure this catches is re-writable by the next
 * dependency bump.
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const NOTICE_PATH = path.join(REPO_ROOT, "NOTICE.md");
const WEB_ROOT = path.join(REPO_ROOT, "web");
const CUSTOM_INSTALLS_ROOT = path.join(WEB_ROOT, "customInstalls");

const WEB_APP_HEADING = "THIRD PARTY COMPONENTS - WEB APPLICATION";
const CUSTOM_INSTALLS_HEADING = "THIRD PARTY COMPONENTS - WEB CUSTOM INSTALLS (3D VIEWERS)";
const PAID_VIEWERS_HEADING = "THIRD PARTY PAID COMPONENTS - WEB VIEWERS";

/*
 * Floors for the count controls. Far below the real sizes (73 / 19 / 2 rows today) so ordinary
 * dependency churn never touches them, while a parser that silently matches nothing fails at once.
 */
const MIN_WEB_APP_ROWS = 50;
const MIN_VIEWER_ROWS = 10;
const MIN_LOCKFILE_ENTRIES = 500;

// ---------------------------------------------------------------------------------------------------
// Manifest readers
// ---------------------------------------------------------------------------------------------------

type StringMap = Record<string, string>;

interface PackageManifest {
    name?: string;
    version?: string;
    license?: unknown;
    licenses?: unknown;
    dependencies?: Record<string, unknown>;
    devDependencies?: Record<string, unknown>;
    optionalDependencies?: Record<string, unknown>;
    overrides?: Record<string, unknown>;
}

interface LockEntry {
    version?: string;
    dev?: boolean;
    license?: unknown;
}

interface Lockfile {
    packages?: Record<string, LockEntry>;
}

interface ResolvedPackage {
    version: string;
    license: string | undefined;
    dev: boolean;
}

function readJson<T>(file: string): T {
    return JSON.parse(fs.readFileSync(file, "utf-8")) as T;
}

/** Only string-valued entries; nested override objects target a transitive dependency's own tree. */
function stringEntries(section: Record<string, unknown> | undefined): StringMap {
    const out: StringMap = {};
    for (const [k, v] of Object.entries(section ?? {})) {
        if (typeof v === "string") out[k] = v;
    }
    return out;
}

/**
 * The licence a manifest declares, as one string. Old packages use a `{ type }` object or a
 * `licenses` array; SPDX expressions such as `(MPL-2.0 OR Apache-2.0)` arrive as a plain string.
 */
function licenseOf(manifest: { license?: unknown; licenses?: unknown }): string | undefined {
    const lic = manifest.license;
    if (typeof lic === "string" && lic.trim()) return lic.trim();
    if (lic && typeof lic === "object" && typeof (lic as { type?: unknown }).type === "string") {
        return (lic as { type: string }).type;
    }
    if (Array.isArray(manifest.licenses)) {
        const types = manifest.licenses
            .map((l) => (typeof l === "string" ? l : (l as { type?: unknown })?.type))
            .filter((t): t is string => typeof t === "string");
        if (types.length) return types.join(" OR ");
    }
    return undefined;
}

/** Every package a v2/v3 lockfile resolves, keyed by package name (top-level tree only). */
function resolvedPackages(lockfilePath: string): Map<string, ResolvedPackage> {
    const out = new Map<string, ResolvedPackage>();
    if (!fs.existsSync(lockfilePath)) return out;
    const lock = readJson<Lockfile>(lockfilePath);
    for (const [key, entry] of Object.entries(lock.packages ?? {})) {
        // Only hoisted entries: a nested `node_modules/a/node_modules/b` is a second copy of `b`
        // pinned for `a`, not what the project resolves `b` to.
        const m = /^node_modules\/((?:@[^/]+\/)?[^/]+)$/.exec(key);
        if (!m || !entry.version) continue;
        out.set(m[1], {
            version: entry.version,
            license: licenseOf(entry),
            dev: entry.dev === true,
        });
    }
    return out;
}

/** Licence of the package installed under `<root>/node_modules`, or undefined when not installed. */
function installedLicense(root: string, name: string): string | undefined {
    const file = path.join(root, "node_modules", name, "package.json");
    if (!fs.existsSync(file)) return undefined;
    return licenseOf(readJson<PackageManifest>(file));
}

// ---------------------------------------------------------------------------------------------------
// NOTICE.md table parsing
// ---------------------------------------------------------------------------------------------------

interface NoticeRow {
    name: string;
    /** Version cell as written; for linked cells, the link text. */
    version: string;
    /** Link target of the version cell, when it is a link. */
    url: string | undefined;
    /** Licence cell with footnote markers (`\*`, `\*\*`) removed. */
    license: string;
    line: number;
}

/** Undo the escaping Prettier applies to Markdown table cells (`@types/mapbox\_\_mapbox-gl-draw`). */
const unescapeCell = (cell: string): string => cell.replace(/\\([_*])/g, "$1");

/** Body of the `## <heading>` section, up to the next `## ` heading. */
function sectionLines(notice: string, heading: string): { lines: string[]; start: number } {
    const all = notice.split(/\r?\n/);
    const start = all.findIndex((l) => l.trim() === `## ${heading}`);
    if (start < 0) throw new Error(`NOTICE.md has no section "## ${heading}"`);
    let end = all.length;
    for (let i = start + 1; i < all.length; i++) {
        if (all[i].startsWith("## ")) {
            end = i;
            break;
        }
    }
    return { lines: all.slice(start, end), start };
}

/** Every body row of every three-column table in a section. */
function tableRows(notice: string, heading: string): NoticeRow[] {
    const { lines, start } = sectionLines(notice, heading);
    const rows: NoticeRow[] = [];
    lines.forEach((line, i) => {
        if (!line.startsWith("|")) return;
        const cells = line
            .split("|")
            .slice(1, -1)
            .map((c) => c.trim());
        if (cells.length !== 3) return;
        if (cells.every((c) => /^:?-+:?$/.test(c))) return; // separator
        if (/^(Package|Name)$/.test(cells[0])) return; // header
        const link = /^\[(.+?)\]\((\S+?)\)$/.exec(cells[1]);
        rows.push({
            name: unescapeCell(cells[0]),
            version: link ? link[1] : cells[1],
            url: link ? link[2] : undefined,
            license: cells[2].replace(/(\\\*)+$/, "").trim(),
            line: start + i + 1,
        });
    });
    return rows;
}

const at = (row: NoticeRow): string => `${row.name} (NOTICE.md:${row.line})`;

/** A declared range without its leading range operator: `^8.33.0` -> `8.33.0`. */
const bareVersion = (range: string): string => range.replace(/^[\^~]/, "");

const sortedUnique = (values: string[]): string[] => Array.from(new Set(values)).sort();

/** Split a version cell that lists several versions: `0.0.77, 0.0.68`. */
const versionsIn = (cell: string): string[] =>
    sortedUnique(
        cell
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean)
    );

const notice = fs.readFileSync(NOTICE_PATH, "utf-8");

// ---------------------------------------------------------------------------------------------------
// WEB APPLICATION table <-> web/package.json
// ---------------------------------------------------------------------------------------------------

describe("NOTICE.md WEB APPLICATION table matches web/package.json", () => {
    const manifest = readJson<PackageManifest>(path.join(WEB_ROOT, "package.json"));
    const runtimeDeps = stringEntries(manifest.dependencies);
    const devDeps = stringEntries(manifest.devDependencies);
    const overrides = stringEntries(manifest.overrides);
    const resolved = resolvedPackages(path.join(WEB_ROOT, "package-lock.json"));
    const rows = tableRows(notice, WEB_APP_HEADING);
    const listed = new Set(rows.map((r) => r.name));

    /** The range web/package.json declares for a package, from whichever section carries it. */
    const declaredRange = (name: string): string | undefined =>
        runtimeDeps[name] ?? devDeps[name] ?? overrides[name];

    it("parses a populated table and populated manifests (count control)", () => {
        // Control for every assertion below: each iterates one of these collections, so an empty
        // one would report success having compared nothing.
        expect(rows.length).toBeGreaterThanOrEqual(MIN_WEB_APP_ROWS);
        expect(Object.keys(runtimeDeps).length).toBeGreaterThanOrEqual(MIN_WEB_APP_ROWS);
        expect(resolved.size).toBeGreaterThanOrEqual(MIN_LOCKFILE_ENTRIES);
    });

    it("lists no package twice", () => {
        const dupes = rows.map((r) => r.name).filter((n, i, all) => all.indexOf(n) !== i);
        expect(dupes).toEqual([]);
    });

    it("lists EVERY runtime dependency", () => {
        const missing = Object.keys(runtimeDeps).filter((d) => !listed.has(d));
        expect(missing).toEqual([]);
    });

    it("lists ONLY packages the web build depends on", () => {
        // A row is legitimate when web/package.json declares the package (runtime, dev, or an
        // override pin), or the lockfile resolves it into the production tree -- a transitive
        // package deliberately attributed, such as the editor behind `jodit-react`. A row for a
        // package in neither place is attribution for something that no longer ships.
        const orphans = rows
            .filter((r) => declaredRange(r.name) === undefined)
            .filter((r) => {
                const lock = resolved.get(r.name);
                return lock === undefined || lock.dev;
            })
            .map(at);
        expect(orphans).toEqual([]);
    });

    it("carries the declared range for declared packages and the resolved version otherwise", () => {
        const wrong: string[] = [];
        for (const row of rows) {
            const declared = declaredRange(row.name);
            const expected = declared ?? resolved.get(row.name)?.version;
            if (expected === undefined) continue; // reported by the ONLY-dependencies test
            if (row.version !== expected) {
                wrong.push(`${at(row)}: table says ${row.version}, manifest says ${expected}`);
            }
        }
        expect(wrong).toEqual([]);
    });

    it("carries the licence the installed package declares", () => {
        const wrong: string[] = [];
        let compared = 0;
        for (const row of rows) {
            const actual = installedLicense(WEB_ROOT, row.name) ?? resolved.get(row.name)?.license;
            if (actual === undefined) continue;
            compared++;
            if (row.license !== actual) {
                wrong.push(`${at(row)}: table says ${row.license}, package says ${actual}`);
            }
        }
        // The lockfile carries a licence for nearly every entry, so this cannot be near zero
        // even on a checkout that has not run `npm install`.
        expect(compared).toBeGreaterThanOrEqual(MIN_WEB_APP_ROWS);
        expect(wrong).toEqual([]);
    });
});

// ---------------------------------------------------------------------------------------------------
// WEB CUSTOM INSTALLS + PAID tables <-> web/customInstalls/*/package.json
// ---------------------------------------------------------------------------------------------------

interface ViewerInstall {
    dir: string;
    /** Runtime + optional dependencies the install manifest declares. */
    declared: StringMap;
    resolved: Map<string, ResolvedPackage>;
    /** Concatenated text of the install scripts directly under the viewer directory. */
    scripts: string;
}

/** Every viewer directory under web/customInstalls that carries an install script or a manifest. */
function viewerInstalls(): ViewerInstall[] {
    return fs
        .readdirSync(CUSTOM_INSTALLS_ROOT, { withFileTypes: true })
        .filter((e) => e.isDirectory())
        .map((e) => {
            const dir = path.join(CUSTOM_INSTALLS_ROOT, e.name);
            const manifestPath = path.join(dir, "package.json");
            const manifest = fs.existsSync(manifestPath)
                ? readJson<PackageManifest>(manifestPath)
                : {};
            const scripts = fs
                .readdirSync(dir)
                .filter((f) => f.endsWith(".js"))
                .map((f) => fs.readFileSync(path.join(dir, f), "utf-8"))
                .join("\n");
            return {
                dir: e.name,
                declared: {
                    ...stringEntries(manifest.dependencies),
                    ...stringEntries(manifest.optionalDependencies),
                },
                resolved: resolvedPackages(path.join(dir, "package-lock.json")),
                scripts,
            };
        })
        .filter((v) => Object.keys(v.declared).length > 0 || v.scripts.length > 0);
}

describe("NOTICE.md viewer tables match web/customInstalls/*/package.json", () => {
    const installs = viewerInstalls();
    const withManifest = installs.filter((v) => Object.keys(v.declared).length > 0);
    const viewerRows = tableRows(notice, CUSTOM_INSTALLS_HEADING);
    const paidRows = tableRows(notice, PAID_VIEWERS_HEADING);

    /** Every range each install declares for a package, across all installs. */
    const declaredRanges = new Map<string, string[]>();
    for (const v of withManifest) {
        for (const [name, range] of Object.entries(v.declared)) {
            declaredRanges.set(name, [...(declaredRanges.get(name) ?? []), range]);
        }
    }
    /** Every resolution of a package across all install lockfiles, with the install it came from. */
    const resolutions = (name: string): Array<{ install: ViewerInstall; pkg: ResolvedPackage }> =>
        installs.flatMap((install) => {
            const pkg = install.resolved.get(name);
            return pkg ? [{ install, pkg }] : [];
        });

    /** Install scripts that name the row's repository -- the git-cloned viewers. */
    const scriptsNaming = (url: string | undefined): ViewerInstall[] => {
        if (!url) return [];
        const bare = url.replace(/\/$/, "");
        return installs.filter(
            (v) => v.scripts.includes(`${bare}.git`) || v.scripts.includes(bare)
        );
    };

    /** Paid-table rows are linked as `name@version`. */
    const paidLinks = paidRows.map((r) => {
        const m = /^(.+)@([^@]+)$/.exec(r.version);
        return { row: r, name: m ? m[1] : r.version, version: m ? m[2] : "" };
    });
    const paidNames = new Set(paidLinks.map((p) => p.name));
    const viewerNames = new Set(viewerRows.map((r) => r.name));

    it("parses populated tables and populated install manifests (count control)", () => {
        expect(viewerRows.length).toBeGreaterThanOrEqual(MIN_VIEWER_ROWS);
        expect(declaredRanges.size).toBeGreaterThanOrEqual(MIN_VIEWER_ROWS);
        expect(withManifest.length).toBeGreaterThanOrEqual(3);
        expect(paidRows.length).toBeGreaterThanOrEqual(1);
    });

    it("lists no package twice", () => {
        const names = [...viewerRows, ...paidLinks.map((p) => p.row)].map((r) => r.name);
        const dupes = names.filter((n, i, all) => all.indexOf(n) !== i);
        expect(dupes).toEqual([]);
    });

    it("lists EVERY dependency declared by a viewer install", () => {
        const missing = Array.from(declaredRanges.keys()).filter(
            (name) => !viewerNames.has(name) && !paidNames.has(name)
        );
        expect(missing).toEqual([]);
    });

    it("lists ONLY packages a viewer install depends on or clones", () => {
        // A row is legitimate when some install manifest declares the package, some install
        // lockfile resolves it (a transitive package deliberately attributed), or an install script
        // names its repository (a viewer built from a git clone, which has no manifest here).
        const orphans = viewerRows
            .filter(
                (r) =>
                    !declaredRanges.has(r.name) &&
                    resolutions(r.name).length === 0 &&
                    scriptsNaming(r.url).length === 0
            )
            .map(at);
        expect(orphans).toEqual([]);
    });

    it("carries every declared version, the resolved version, or the cloned pin", () => {
        const wrong: string[] = [];
        for (const row of viewerRows) {
            const declared = declaredRanges.get(row.name);
            if (declared) {
                // One row per package; where two viewers pin different versions the cell lists both.
                const expected = sortedUnique(declared.map(bareVersion));
                if (JSON.stringify(versionsIn(row.version)) !== JSON.stringify(expected)) {
                    wrong.push(`${at(row)}: table says ${row.version}, manifests say ${expected}`);
                }
                continue;
            }
            const resolvedHere = resolutions(row.name);
            if (resolvedHere.length) {
                const expected = sortedUnique(resolvedHere.map((r) => r.pkg.version));
                if (JSON.stringify(versionsIn(row.version)) !== JSON.stringify(expected)) {
                    wrong.push(`${at(row)}: table says ${row.version}, lockfiles say ${expected}`);
                }
                continue;
            }
            const cloners = scriptsNaming(row.url);
            if (cloners.length && !cloners.some((v) => v.scripts.includes(row.version))) {
                wrong.push(`${at(row)}: ${row.version} is not the pin in ${cloners[0].dir}`);
            }
        }
        expect(wrong).toEqual([]);
    });

    it("carries the licence the installed package declares", () => {
        const wrong: string[] = [];
        let compared = 0;
        for (const row of viewerRows) {
            const known = sortedUnique(
                resolutions(row.name)
                    .map(
                        ({ install, pkg }) =>
                            installedLicense(
                                path.join(CUSTOM_INSTALLS_ROOT, install.dir),
                                row.name
                            ) ?? pkg.license
                    )
                    .filter((l): l is string => l !== undefined)
            );
            if (known.length === 0) continue; // git clones and packages that declare no licence
            compared++;
            const ok =
                known.length === 1
                    ? row.license === known[0]
                    : known.every((l) => row.license.includes(l));
            if (!ok) wrong.push(`${at(row)}: table says ${row.license}, package says ${known}`);
        }
        expect(compared).toBeGreaterThanOrEqual(MIN_VIEWER_ROWS);
        expect(wrong).toEqual([]);
    });

    it("links each paid viewer as name@version with the declared version", () => {
        const wrong: string[] = [];
        for (const p of paidLinks) {
            const declared = declaredRanges.get(p.name);
            if (!declared) {
                wrong.push(`${at(p.row)}: ${p.name} is not declared by any viewer install`);
                continue;
            }
            const expected = sortedUnique(declared.map(bareVersion));
            if (JSON.stringify([p.version]) !== JSON.stringify(expected)) {
                wrong.push(`${at(p.row)}: table says ${p.version}, manifests say ${expected}`);
            }
        }
        expect(wrong).toEqual([]);
    });
});

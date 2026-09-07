/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Fail if a synthesized assembly is stamped for an account other than the one you are about to deploy
 * into.
 *
 * WHY THIS EXISTS, measured rather than imagined. `infra/config/config.json` ships `env.account: null`,
 * so the target account is resolved from AMBIENT CREDENTIALS at SYNTH time — not at deploy time. Running
 * `npx cdk synth` without the deployment's `AWS_PROFILE` exported therefore produces a complete,
 * internally consistent, correct-LOOKING assembly aimed at whatever account the shell happened to have,
 * and `cdk deploy --app <that assembly>` then either deploys to the wrong account or fails in a way that
 * names a bootstrap problem rather than the real cause. Every artifact-level check an operator would
 * think to run passes, because the artifact is not corrupt — it is correct for the wrong account.
 *
 * `docs/review/DEPLOY-RUNBOOK.md` records the rule as prose ("export AWS_PROFILE before the SYNTH, not
 * just before the deploy"), and prose does not fail a build. This is the same walk, executable, so it can
 * gate a deploy instead of relying on the operator remembering.
 *
 * Usage:
 *   node scripts/assertManifestAccount.js                       # expect the ambient STS account
 *   node scripts/assertManifestAccount.js --account 123456789012
 *   node scripts/assertManifestAccount.js --app /tmp/cdkout --account 123456789012
 *
 * Exit 0 when every stack artifact resolves to the expected account, non-zero otherwise. Exit 0 with a
 * notice when there is no assembly on disk — this gates a deploy, and refusing to run before a synth
 * would make it useless in a fresh checkout.
 */

import * as fs from "fs";
import * as path from "path";
import { execFileSync } from "child_process";

function arg(name: string, fallback: string): string {
    const i = process.argv.indexOf(name);
    return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const appDir = path.resolve(arg("--app", path.join(__dirname, "..", "cdk.out")));
const manifestPath = path.join(appDir, "manifest.json");

if (!fs.existsSync(manifestPath)) {
    console.log(`assertManifestAccount: no assembly at ${manifestPath} — nothing to check yet.`);
    process.exit(0);
}

/** The account we are about to deploy into. Explicit flag wins; otherwise ask STS. */
function expectedAccount(): string {
    const explicit = arg("--account", "");
    if (explicit) return explicit;
    try {
        const out = execFileSync(
            "aws",
            ["sts", "get-caller-identity", "--query", "Account", "--output", "text"],
            { encoding: "utf-8", stdio: ["ignore", "pipe", "ignore"] }
        );
        return out.trim();
    } catch {
        return "";
    }
}

const expected = expectedAccount();
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));

// Every deployable stack artifact carries `environment: aws://<account>/<region>`. Read it from the
// artifact rather than grepping the templates: a template can reference an account in a string without
// being TARGETED at it, and the environment is the value the CLI actually deploys against.
const found = new Map<string, string[]>(); // account -> [artifact ids]
for (const [id, artifact] of Object.entries<any>(manifest.artifacts || {})) {
    if (artifact.type !== "aws:cloudformation:stack") continue;
    const match = /^aws:\/\/([^/]+)\/(.+)$/.exec(artifact.environment || "");
    if (!match) continue;
    const account = match[1];
    if (!found.has(account)) found.set(account, []);
    found.get(account)!.push(id);
}

if (found.size === 0) {
    // A zero-artifact result would make every assertion below pass vacuously, which is the failure mode
    // this whole check exists to prevent. Treat it as a failure, not a pass.
    console.error(
        `assertManifestAccount: FAIL — ${manifestPath} contains no aws:cloudformation:stack artifact ` +
            `with a resolved environment. Nothing was checked, so this cannot be read as approval.`
    );
    process.exit(2);
}

const accounts = [...found.keys()].sort();
console.log(`assertManifestAccount: assembly ${appDir}`);
for (const account of accounts) {
    console.log(`  ${account}: ${found.get(account)!.length} stack artifact(s)`);
}

// An unresolved environment is the placeholder CDK uses when no account could be determined. It is not
// the wrong account, but it cannot be deployed either, so name it distinctly.
const unresolved = accounts.filter((a) => a.includes("unknown-account"));
if (unresolved.length) {
    console.error(
        `assertManifestAccount: FAIL — the assembly has an unresolved account (${unresolved.join(
            ", "
        )}). ` + `Synth could not determine the target: export AWS_PROFILE and re-synth.`
    );
    process.exit(3);
}

if (!expected) {
    console.error(
        "assertManifestAccount: FAIL — could not determine the expected account. Pass --account " +
            "<id>, or make `aws sts get-caller-identity` work in this shell. Refusing to pass without " +
            "having compared anything."
    );
    process.exit(4);
}

const wrong = accounts.filter((a) => a !== expected);
if (wrong.length) {
    console.error(
        `assertManifestAccount: FAIL — expected every stack artifact to target ${expected}, but the ` +
            `assembly targets ${wrong.join(", ")}.\n` +
            `  This is the config.json \`env.account: null\` trap: the account is resolved at SYNTH ` +
            `time from ambient credentials. Re-synth with AWS_PROFILE exported:\n` +
            `    export AWS_PROFILE=<profile> && npx cdk synth --all -o ${appDir}\n` +
            `  Offending artifacts: ${wrong
                .map((a) => found.get(a)!.slice(0, 3).join(", "))
                .join(" | ")}`
    );
    process.exit(1);
}

console.log(`assertManifestAccount: OK — all artifacts target ${expected}.`);

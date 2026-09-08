/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every pipeline section of the configuration reference carries an "Implemented by" admonition that
 * names the pipeline's nested stack and the compute it runs on. Nothing else ties that sentence to
 * the construct, so it can state AWS Batch for a pipeline that is a containerized Lambda, or name a
 * nested stack file that does not exist, and no build or lint step notices.
 *
 * This guard derives each pipeline's compute type from the constructs its nested stack actually
 * instantiates — the Batch compute environment kind and GPU reservation, an ECS Fargate task
 * definition, an EKS cluster, or a Docker-image Lambda — and asserts that the documented row states
 * that type and no other. The pipeline list itself comes from the imports of
 * `pipelineBuilder-nestedStack.ts`, so a new pipeline is covered the moment it is registered.
 *
 * Durable (root CLAUDE.md Rule 13): a pipeline's compute can be re-platformed at any time, and each
 * such change must land in this page.
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const INFRA_LIB = path.join(REPO_ROOT, "infra", "lib");
const NESTED_STACKS = path.join(INFRA_LIB, "nestedStacks");
const PIPELINES_ROOT = path.join(NESTED_STACKS, "pipelines");
const PIPELINE_BUILDER = path.join(PIPELINES_ROOT, "pipelineBuilder-nestedStack.ts");
const SHARED_CONSTRUCTS_DIR = path.join(PIPELINES_ROOT, "constructs");
const DOC_PATH = path.join(
    REPO_ROOT,
    "documentation",
    "docusaurus-site",
    "docs",
    "deployment",
    "configuration-reference.md"
);

type ComputeType = "BATCH_FARGATE" | "BATCH_GPU" | "ECS_FARGATE" | "EKS" | "LAMBDA_CONTAINER";

/** The wording an "Implemented by" row must carry for each compute type. */
const DOCUMENTED_AS: Record<ComputeType, string> = {
    BATCH_FARGATE: "AWS Batch on Fargate",
    BATCH_GPU: "AWS Batch on GPU instances",
    ECS_FARGATE: "Amazon ECS on Fargate",
    EKS: "Amazon EKS",
    LAMBDA_CONTAINER: "containerized AWS Lambda function",
};

const toPosix = (p: string) => p.split(path.sep).join("/");
const read = (file: string) => fs.readFileSync(file, "utf-8");

/** Every TypeScript source under a tree. */
function tsFilesUnder(root: string): string[] {
    const out: string[] = [];
    const walk = (dir: string) => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) walk(full);
            else if (entry.name.endsWith(".ts")) out.push(full);
        }
    };
    walk(root);
    return out;
}

interface PipelineStack {
    className: string;
    /** Absolute path of the nested stack file. */
    file: string;
    /** Repo-relative POSIX path, as the documentation cites it. */
    docPath: string;
    /** Directory whose sources make up the pipeline (constructs + lambda builders). */
    dir: string;
}

/**
 * The pipelines VAMS ships, taken from the nested stacks `pipelineBuilder-nestedStack.ts` imports
 * from its own tree. Each import is checked to be instantiated so a stale import cannot register a
 * pipeline that is no longer built.
 */
function pipelineStacks(): PipelineStack[] {
    const src = read(PIPELINE_BUILDER);
    const stacks: PipelineStack[] = [];
    const importRe = /import \{ (\w+NestedStack) \} from "(\.\/[^"]+)";/g;
    let m: RegExpExecArray | null;
    while ((m = importRe.exec(src)) !== null) {
        const [, className, rel] = m;
        const file = path.resolve(PIPELINES_ROOT, `${rel}.ts`);
        if (!fs.existsSync(file)) throw new Error(`pipelineBuilder imports a missing file: ${rel}`);
        if (!src.includes(`new ${className}(`)) {
            throw new Error(`pipelineBuilder imports ${className} but never instantiates it`);
        }
        stacks.push({
            className,
            file,
            docPath: toPosix(path.relative(REPO_ROOT, file)),
            dir: path.dirname(file),
        });
    }
    return stacks;
}

/** Construct classes shared across pipelines, keyed by class name, with their source. */
function sharedConstructs(): Map<string, string> {
    const out = new Map<string, string>();
    for (const file of tsFilesUnder(SHARED_CONSTRUCTS_DIR)) {
        const text = read(file);
        const m = /export class (\w+) extends Construct\b/.exec(text);
        if (m) out.set(m[1], text);
    }
    return out;
}

/**
 * The compute a pipeline runs on, read from what its sources instantiate. A shared construct the
 * pipeline instantiates (for example `BatchFargatePipelineConstruct`) contributes its own source, so
 * the compute environment kind is read from where it is declared rather than inferred from a name.
 */
function computeTypeOf(stack: PipelineStack, shared: Map<string, string>): ComputeType {
    let pool = tsFilesUnder(stack.dir).map(read).join("\n");
    for (const [name, text] of shared) {
        if (pool.includes(`new ${name}(`)) pool += `\n${text}`;
    }
    const has = (re: RegExp) => re.test(pool);

    const batchCfnEnv = has(/new (?:batch\.)?CfnComputeEnvironment\(/);
    const batchEc2 =
        has(/new batch\.ManagedEc2EcsComputeEnvironment\(/) || (batchCfnEnv && has(/type: "EC2"/));
    const batchFargate =
        has(/new batch\.FargateComputeEnvironment\(/) || (batchCfnEnv && has(/type: "FARGATE/));
    const gpuReservation = has(/type: "GPU"/) || has(/\bgpu: \d/);
    const ecsFargate = has(/new ecs\.FargateTaskDefinition\(/);
    const eks = has(/new eks\.Cluster\(/);
    const dockerLambda = has(/new lambda\.DockerImageFunction\(/);

    const found: ComputeType[] = [];
    if (batchEc2) {
        if (!gpuReservation) {
            throw new Error(
                `${stack.className}: EC2 Batch compute without a GPU reservation has no documented wording`
            );
        }
        found.push("BATCH_GPU");
    }
    if (batchFargate) found.push("BATCH_FARGATE");
    if (ecsFargate) found.push("ECS_FARGATE");
    if (eks) found.push("EKS");
    if (found.length === 0 && dockerLambda) found.push("LAMBDA_CONTAINER");

    if (found.length !== 1) {
        throw new Error(
            `${stack.className}: expected exactly one compute type, derived [${found.join(", ")}]`
        );
    }
    return found[0];
}

interface Admonition {
    /** 1-indexed line of the opening `:::` fence. */
    line: number;
    text: string;
}

/** Every `:::<kind>[Implemented by]` admonition in the page. */
function implementedByBlocks(doc: string): Admonition[] {
    const lines = doc.split("\n");
    const blocks: Admonition[] = [];
    let open: Admonition | null = null;
    lines.forEach((raw, i) => {
        const line = raw.trim();
        if (open === null) {
            if (/^:::\w+\[Implemented by\]/.test(line)) open = { line: i + 1, text: "" };
        } else if (line === ":::") {
            blocks.push(open);
            open = null;
        } else {
            open.text += `${raw}\n`;
        }
    });
    if (open !== null) throw new Error("unterminated Implemented-by admonition");
    return blocks;
}

/**
 * Resolve a `.ts` path as the page cites it: repo-relative when it starts with `infra/`, otherwise
 * relative to `infra/lib/nestedStacks`, to `infra/lib`, or to the directory of a repo-relative path
 * cited earlier in the same block.
 */
function resolveCitedPath(token: string, block: string): string | undefined {
    const candidates: string[] = [];
    if (token.startsWith("infra/")) {
        candidates.push(path.join(REPO_ROOT, token));
    } else {
        candidates.push(path.join(NESTED_STACKS, token), path.join(INFRA_LIB, token));
        const anchor = /`(infra\/[^`]+\.ts)`/.exec(block);
        if (anchor) candidates.push(path.join(REPO_ROOT, path.dirname(anchor[1]), token));
    }
    return candidates.find((c) => fs.existsSync(c));
}

describe("configuration reference: pipeline 'Implemented by' rows match the constructs", () => {
    const shared = sharedConstructs();
    const stacks = pipelineStacks();
    const doc = read(DOC_PATH);
    const blocks = implementedByBlocks(doc);
    const derived = new Map(stacks.map((s) => [s.className, computeTypeOf(s, shared)]));

    it("derives the pipeline list and compute types from source (count control)", () => {
        // Control for the per-pipeline assertions: the list they iterate must be the real pipeline
        // set, and the classifier must discriminate rather than return one answer for everything.
        // VAMS ships 13 pipeline nested stacks across five compute kinds; the floors sit below both
        // so ordinary additions do not touch this test while an empty or constant result fails.
        expect(stacks.length).toBeGreaterThanOrEqual(12);
        expect(shared.size).toBeGreaterThanOrEqual(2);
        expect(new Set(derived.values()).size).toBeGreaterThanOrEqual(4);
        expect(blocks.length).toBeGreaterThanOrEqual(stacks.length);
    });

    it("EVERY pipeline nested stack has an Implemented-by row naming its file and class", () => {
        const undocumented = stacks
            .filter((s) => !blocks.some((b) => b.text.includes(`\`${s.docPath}\``)))
            .map((s) => s.docPath);
        expect(undocumented).toEqual([]);

        const misnamed: string[] = [];
        for (const s of stacks) {
            for (const b of blocks.filter((b) => b.text.includes(`\`${s.docPath}\``))) {
                if (!b.text.includes(`\`${s.className}\``))
                    misnamed.push(`L${b.line}: ${s.className}`);
            }
        }
        expect(misnamed).toEqual([]);
    });

    it("EVERY row states the compute type its constructs instantiate, and no other", () => {
        const wrong: string[] = [];
        for (const s of stacks) {
            const type = derived.get(s.className) as ComputeType;
            const expected = DOCUMENTED_AS[type];
            const others = (Object.keys(DOCUMENTED_AS) as ComputeType[])
                .filter((t) => t !== type)
                .map((t) => DOCUMENTED_AS[t]);
            for (const b of blocks.filter((b) => b.text.includes(`\`${s.docPath}\``))) {
                if (!b.text.includes(expected)) {
                    wrong.push(`L${b.line} ${s.className}: expected "${expected}"`);
                }
                for (const other of others) {
                    if (b.text.includes(other)) {
                        wrong.push(`L${b.line} ${s.className}: also states "${other}"`);
                    }
                }
            }
        }
        expect(wrong).toEqual([]);
    });

    it("EVERY .ts path cited in an Implemented-by row exists", () => {
        // The other way a row lies: naming a stack file that was never created or has moved.
        const cited: string[] = [];
        const missing: string[] = [];
        for (const b of blocks) {
            const tokenRe = /`([\w./-]+\.ts)`/g;
            let m: RegExpExecArray | null;
            while ((m = tokenRe.exec(b.text)) !== null) {
                cited.push(m[1]);
                if (resolveCitedPath(m[1], b.text) === undefined)
                    missing.push(`L${b.line}: ${m[1]}`);
            }
        }
        // Control: the page cites a stack file for nearly every section.
        expect(cited.length).toBeGreaterThanOrEqual(25);
        expect(missing).toEqual([]);
    });
});

/**
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The CAD STEP agent image tag is derived from the hash of its container source asset. Output that
 * Python tooling leaves in a working copy (pytest and ruff caches, bytecode, egg-info) must not reach
 * that hash, or a deploy from a working copy that has run the tests rebuilds an unchanged image.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { FileSystem } from "aws-cdk-lib";
import { CAD_STEP_AGENT_SOURCE_EXCLUDE } from "../../lib/nestedStacks/pipelines/genAi/cadStepAgent/constructs/cadStepAgentCodeBuild-construct";

const SOURCES: Record<string, string> = {
    Dockerfile: "FROM public.ecr.aws/docker/library/python:3.12-slim\n",
    "requirements.txt": "cadquery\n",
    "cad_step_agent/__init__.py": "",
    "cad_step_agent/agent.py": "def run():\n    return 1\n",
    "tests/test_agent.py": "def test_run():\n    assert True\n",
};

const TOOL_OUTPUT: Record<string, string> = {
    ".pytest_cache/v/cache/lastfailed": "{}\n",
    ".pytest_cache/README.md": "pytest cache\n",
    "tests/.pytest_cache/v/cache/nodeids": "[]\n",
    ".ruff_cache/0.6.9/123": "x",
    "cad_step_agent/__pycache__/agent.cpython-312.pyc": "bytecode",
    "cad_step_agent.egg-info/PKG-INFO": "Name: cad-step-agent\n",
};

const roots: string[] = [];

function tree(files: Record<string, string>): string {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "cad-step-agent-asset-"));
    roots.push(root);
    for (const [rel, body] of Object.entries(files)) {
        const file = path.join(root, rel);
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.writeFileSync(file, body);
    }
    return root;
}

function fingerprint(root: string): string {
    return FileSystem.fingerprint(root, { exclude: CAD_STEP_AGENT_SOURCE_EXCLUDE });
}

afterAll(() => {
    for (const root of roots) fs.rmSync(root, { recursive: true, force: true });
});

describe("CAD STEP agent container source asset", () => {
    it("hashes the same with and without local tool output", () => {
        const clean = tree(SOURCES);
        const tested = tree({ ...SOURCES, ...TOOL_OUTPUT });
        expect(fingerprint(tested)).toBe(fingerprint(clean));
    });

    it("hashes differently when a source file changes", () => {
        const before = tree(SOURCES);
        const after = tree({ ...SOURCES, "cad_step_agent/agent.py": "def run():\n    return 2\n" });
        expect(fingerprint(after)).not.toBe(fingerprint(before));
    });
});

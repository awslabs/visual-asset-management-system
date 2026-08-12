// infra/test/resourceNameRegistry.test.ts
import { ResourceNameRegistry } from "../lib/nestedStacks/resourceNames/resourceNameRegistry";

describe("ResourceNameRegistry", () => {
    it("stores and lists descriptors", () => {
        const r = new ResourceNameRegistry();
        r.register({ paramKey: "dynamoTables/assetStorage", value: "table-a" });
        r.register({ paramKey: "s3Buckets/assetAuxiliary", value: "bucket-b" });
        expect(r.list()).toHaveLength(2);
        expect(r.list()[0]).toEqual({ paramKey: "dynamoTables/assetStorage", value: "table-a" });
    });

    it("throws on duplicate paramKey", () => {
        const r = new ResourceNameRegistry();
        r.register({ paramKey: "dynamoTables/assetStorage", value: "table-a" });
        expect(() =>
            r.register({ paramKey: "dynamoTables/assetStorage", value: "table-b" })
        ).toThrow(/duplicate/i);
    });

    it("throws on empty value", () => {
        const r = new ResourceNameRegistry();
        expect(() => r.register({ paramKey: "dynamoTables/assetStorage", value: "" })).toThrow(
            /empty value/i
        );
    });

    it("rejects malformed paramKeys that would break SSM paths or construct IDs", () => {
        const r = new ResourceNameRegistry();
        expect(() => r.register({ paramKey: "noSegments", value: "v" })).toThrow(/invalid/i);
        expect(() => r.register({ paramKey: "/leading/slash", value: "v" })).toThrow(/invalid/i);
        expect(() => r.register({ paramKey: "trailing/slash/", value: "v" })).toThrow(/invalid/i);
        expect(() => r.register({ paramKey: "has space/key", value: "v" })).toThrow(/invalid/i);
        expect(() => r.register({ paramKey: "has-hyphen/key", value: "v" })).toThrow(/invalid/i);
        expect(() => r.register({ paramKey: "a//b", value: "v" })).toThrow(/invalid/i);
    });

    it("accepts multi-segment keys for future resource categories", () => {
        const r = new ResourceNameRegistry();
        r.register({ paramKey: "sqsQueues/workflowAutoExecute", value: "queue-url" });
        r.register({ paramKey: "dynamoTables/v2/assetStorage", value: "table-v2" });
        expect(r.list()).toHaveLength(2);
    });

    it("has() reports registration state", () => {
        const r = new ResourceNameRegistry();
        expect(r.has("dynamoTables/assetStorage")).toBe(false);
        r.register({ paramKey: "dynamoTables/assetStorage", value: "table-a" });
        expect(r.has("dynamoTables/assetStorage")).toBe(true);
    });

    it("list() returns a copy that does not expose internal state", () => {
        const r = new ResourceNameRegistry();
        r.register({ paramKey: "dynamoTables/assetStorage", value: "table-a" });
        const copy = r.list();
        copy.pop();
        expect(r.list()).toHaveLength(1);
    });
});

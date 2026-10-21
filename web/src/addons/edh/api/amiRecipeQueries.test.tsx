import { computeAmiRefetchInterval, AMI_POLL_MS } from "./amiRecipeQueries";

describe("computeAmiRefetchInterval", () => {
    it("polls while any recipe's latest build is moving and stops otherwise", () => {
        expect(computeAmiRefetchInterval(undefined)).toBe(false);
        expect(computeAmiRefetchInterval([{ latestBuild: { status: "AVAILABLE" } } as any])).toBe(
            false
        );
        expect(computeAmiRefetchInterval([{ latestBuild: { status: "BUILDING" } } as any])).toBe(
            AMI_POLL_MS
        );
        expect(computeAmiRefetchInterval([{ status: "QUEUED" } as any])).toBe(AMI_POLL_MS);
    });
});

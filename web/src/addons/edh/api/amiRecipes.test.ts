import { apiClient } from "../../../services/apiClient";
import * as api from "./amiRecipes";

jest.mock("../../../services/apiClient", () => ({
    apiClient: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn() },
}));

describe("amiRecipes service", () => {
    beforeEach(() => jest.clearAllMocks());

    it("lists recipes and unwraps Items", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ Items: [{ recipeId: "r" }] });
        expect(await api.listAmiRecipes()).toEqual([true, { Items: [{ recipeId: "r" }] }]);
        expect(apiClient.get).toHaveBeenCalledWith("addon/edh/admin/ami-recipes");
    });

    it("encodes the recipe id and version in every path", async () => {
        (apiClient.post as jest.Mock).mockResolvedValue({ version: 2 });
        (apiClient.del as jest.Mock).mockResolvedValue({ status: "RETIRED" });
        (apiClient.put as jest.Mock).mockResolvedValue({ recipeId: "a b" });
        await api.startAmiBuild("a b");
        expect(apiClient.post).toHaveBeenCalledWith("addon/edh/admin/ami-recipes/a%20b/builds", {});
        await api.registerAmiBuild("a b", 2);
        expect(apiClient.post).toHaveBeenCalledWith(
            "addon/edh/admin/ami-recipes/a%20b/builds/2/register",
            {}
        );
        await api.retireAmiBuild("a b", 2);
        expect(apiClient.del).toHaveBeenCalledWith(
            "addon/edh/admin/ami-recipes/a%20b/builds/2",
            {}
        );
        await api.updateAmiRecipe("a b", { recipeId: "a b" } as any);
        expect(apiClient.put).toHaveBeenCalledWith("addon/edh/admin/ami-recipes/a%20b", {
            body: { recipeId: "a b" },
        });
    });

    it("reports a failed call as [false, message]", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(new Error("boom"));
        expect(await api.listAmiRecipes()).toEqual([false, "boom"]);
    });
});

# Custom pages

Fork extension point. Upstream VAMS ships only this README in this directory and does not modify it, so pages added here do not conflict on upgrade.

Each page a fork adds is a component in this directory, registered as a route in `../customRoutes.tsx` (loaded with `React.lazy`) and, when it belongs in the side navigation, as a link in `../customNavigation.ts`. Pages call the API through `../services/customAPIService.ts`, never through `apiClient` directly, and use Cloudscape components with individual imports, as the core pages in `src/pages/` do (`web/CLAUDE.md`).

The "Extending VAMS in a fork" developer guide has a worked example.

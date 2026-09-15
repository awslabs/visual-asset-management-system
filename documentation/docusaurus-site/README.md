# Website

This website is built using [Docusaurus](https://docusaurus.io/), a modern static website generator.

VAMS uses npm as its package manager throughout. A `package-lock.json` is committed here, and
installing with a different package manager resolves a dependency graph other than the locked one.

## Installation

```bash
npm install
```

## Local Development

```bash
npm run start
```

This command starts a local development server and opens up a browser window. Most changes are reflected live without having to restart the server.

## Build

```bash
npm run build
```

This command generates static content into the `build` directory and can be served using any static contents hosting service.

## Type checking

```bash
npm run typecheck
```

This checks the custom React components under `src/`, including the interactive ConfigBuilder. The
production build transpiles TypeScript without type checking, so a type error surfaces here and not
in `npm run build`.

## Deployment

Using SSH:

```bash
USE_SSH=true npm run deploy
```

Not using SSH:

```bash
GIT_USER=<Your GitHub username> npm run deploy
```

If you are using GitHub pages for hosting, this command is a convenient way to build the website and push to the `gh-pages` branch.

Documentation is also deployed automatically by CI when changes under `documentation/docusaurus-site/`
land on `main` or a `release/*` branch — see `.gitlab-ci.yml` and `.github/workflows/docs.yml`.

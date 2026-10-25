# Contributing Guidelines

Thank you for your interest in contributing to our project. Whether it's a bug report, new feature, correction, or additional
documentation, we greatly value feedback and contributions from our community.

Please read through this document before submitting any issues or pull requests to ensure we have all the necessary
information to effectively respond to your bug report or contribution.

## Branch model and where contributions go

Understanding the branch model before you start saves a round of review comments asking you to retarget your change.

-   **`development`** is the main integration branch. **All contributions target `development`**, or a `development-*`
    integration branch when the maintainers have opened one for a larger effort. This is true for every kind of change,
    including a fix you believe should ship as a hotfix.
-   **`main`** holds the released, tagged versions. It is protected. **Nothing targets `main` directly** — not a feature,
    not a fix, not documentation.
-   **`release/X.Y.Z`** branches are release staging branches. They are created by the maintainers for major, minor, and
    hotfix releases, and they promote the candidate commits from `development`. You do not open a pull request against a
    `release/*` branch; the maintainers cherry-pick a merged `development` commit onto a release branch when a change is
    part of that release.

So the flow for a contribution is always:

```
your fork (feature/* or fix/*)  ->  pull request into `development`  ->  review + CI  ->  merge
                                                                              |
                     maintainers promote candidates from `development` into a release/* branch,
                     then release to `main` (tagged) — you do not open PRs for these steps
```

If you open a pull request against `main` or a `release/*` branch, a maintainer will ask you to change the base to
`development`.

## Reporting Bugs/Feature Requests

We welcome you to use the GitHub issue tracker to report bugs or suggest features. The repository provides issue
templates — **Bug report**, **Feature request**, and **Documentation** — so choosing the matching template gives us the
fields we need.

When filing an issue, please check existing open, or recently closed, issues to make sure somebody else hasn't already
reported the issue. Please try to include as much information as you can. Details like these are incredibly useful:

-   A reproducible test case or series of steps
-   The version of our code being used (the release tag, or the branch and commit for an unreleased build)
-   Any modifications you've made relevant to the bug
-   Anything unusual about your environment or deployment

Opening an issue before you start significant work lets us discuss the approach so your time is not wasted — see the
pull request steps below.

## Contributing via Pull Requests

Contributions via pull requests are much appreciated. Before sending us a pull request, please ensure that:

1. You are working against the latest source on the **`development`** branch (or the `development-*` branch the
   maintainers have directed you to). Do not branch from, or target, `main` or a `release/*` branch.
2. You check existing open, and recently merged, pull requests to make sure someone else hasn't addressed the problem
   already.
3. You open an issue to discuss any significant work - we would hate for your time to be wasted.

To send us a pull request, please:

1. Fork the repository.
2. Create a working branch off `development`. Use a short, descriptive branch name prefixed by the kind of change —
   `feature/<description>` for new functionality, `fix/<description>` for a bug fix.
3. Modify the source; please focus on the specific change you are contributing. If you also reformat all the code, it
   will be hard for us to focus on your change.
4. Add or update tests for your change (see **Tests** below) and ensure local tests pass.
5. Add a changelog and revision-history entry for any user-visible change (see **Changelog and revision history** below).
6. Commit to your fork using clear commit messages.
7. Send us a pull request **with `development` as the base branch**, filling in the pull request template (described
   below) and linking the issue it resolves.
8. Pay attention to any automated CI failures reported in the pull request, and stay involved in the conversation.

### Structuring your pull request

The repository's pull request template asks for four things; please fill each one in:

-   **Reason for this change** — the bug or use case behind the change, and a link to the issue it addresses.
-   **Description of changes** — what you changed, and any important design decisions.
-   **Description of how you validated changes** — the unit and/or integration tests you added or ran, and any manual
    verification.
-   **Issue # (if applicable)** — reference the issue so GitHub closes it on merge, for example `Closes #123`. A pull
    request that resolves several issues lists each one.

Keep a pull request focused on a single change; it is far easier to review, and far more likely to merge quickly, than a
pull request that mixes unrelated changes. Submitting the pull request confirms that your contribution is made under the
terms of the Apache-2.0 license, as the template states.

### CI gates

Every pull request runs the repository's automated checks, and they must pass before a change is merged: linting and
formatting, the backend and CLI test suites, and CDK synthesis. Running the equivalent checks locally before you push
(the `/deploy-check` helper documented under `.claude/commands/` runs them in one pass) avoids a round trip through CI.

### Changelog and revision history

Every user-visible change that ships in a release updates **both** of these, under the section for the version currently
in development (the top-most version section in each file):

-   `CHANGELOG.md` — a concise entry under the matching subsection (`Features`, `Bug Fixes`, or `Chores`), with a
    component prefix (for example `**Web**`, `**Backend**`, `**CDK**`, `**CLI**`, `**Pipeline**`, `**Documentation**`) and
    the issue or pull request number at the end of the line.
-   `documentation/docusaurus-site/docs/additional/revisions.md` — a matching entry under the same version, in the
    corresponding list (`Added`, `Key fixes`, or `Other changes`), citing the same issue or pull request number.

Describe **what changed**, concisely. Do not put build, test, or CI status in these files (no test counts, no "all tests
pass", no "CI green") — that belongs in the pull request, not the changelog. A purely internal change with no
user-visible effect (an internal refactor, a test-only change) does not need a changelog entry.

### Tests

Add tests with your change:

-   A **new feature** comes with unit tests covering the behavior it adds.
-   A **bug fix** comes with a regression test — one that fails against the old code and passes with your fix — so the
    bug cannot return unnoticed.

Run the relevant suites locally before you push. The per-component development guides linked below describe the test
frameworks and conventions for each part of the codebase.

### Development guides

These in-repo guides describe the project's standards and make the process easier to follow. They are written for both
human contributors and AI-assisted development tools:

-   `CLAUDE.md` at the repository root, and the per-directory `CLAUDE.md` files under `web/`, `backend/`, `infra/`,
    `tools/`, `backendPipelines/`, and `documentation/` — the coding standards, critical rules, and gold-standard
    reference files for each component.
-   `.kiro/steering/*.md` — the same standards in the Kiro steering format.
-   `.claude/commands/*.md` — helper command documentation, including `/deploy-check` (the pre-submission validation
    checklist), `/update-changelog`, and `/update-docs`.
-   The **Developer Guide** section of the [documentation site](https://awslabs.github.io/visual-asset-management-system/)
    — setup, architecture, and how to extend each component.

GitHub provides additional document on [forking a repository](https://help.github.com/articles/fork-a-repo/) and
[creating a pull request](https://help.github.com/articles/creating-a-pull-request/).

## Finding contributions to work on

Looking at the existing issues is a great way to find something to contribute on. As our projects, by default, use the default GitHub issue labels (enhancement/bug/duplicate/help wanted/invalid/question/wontfix), looking at any 'help wanted' issues is a great place to start.

## Code of Conduct

This project has adopted the [Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct).
For more information see the [Code of Conduct FAQ](https://aws.github.io/code-of-conduct-faq) or contact
opensource-codeofconduct@amazon.com with any additional questions or comments.

## Security issue notifications

If you discover a potential security issue in this project we ask that you notify AWS/Amazon Security via our [vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/). Please do **not** create a public github issue.

## Licensing

See the [LICENSE](./LICENSE) file for our project's licensing. We will ask you to confirm the licensing of your contribution.

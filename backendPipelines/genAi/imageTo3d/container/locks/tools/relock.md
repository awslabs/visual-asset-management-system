# Re-locking an Image to 3D image

Each converted image (`locks/<model>/`) installs its whole Python set from hashed lock files. Re-lock when the
upstream commit, a package, the base image or a Dockerfile install moves; the build fails until you do.

Use the repository's pinned uv (`required-version` in each `pyproject.toml`) and run the commands in
`locks/<model>/`.

## Keep every version (the lock was only made or checked)

1. `uvx --from uv==0.13.8 uv lock --check` passes.
2. Regenerate the exports with the commands in their headers; `git diff` must show no change:

    ```
    uv export --frozen --no-emit-project --no-dev --no-annotate --no-group build --no-group gpl-repair -o requirements.lock
    uv export --frozen --no-emit-project --no-dev --no-annotate --only-group build -o requirements-build.lock
    uv export --frozen --no-emit-project --no-dev --no-annotate --only-group gpl-repair -o requirements-gpl.lock
    ```

    (`python .github/scripts/python_dependency_audit.py` from the repository root does the same for every project
    and also audits the exports.)

## Move something on purpose

1. Change the image: the `*_COMMIT` argument, a pinned package in `pyproject.toml`, or a Dockerfile install.
2. Build the image once from the unlocked or previous state and take its set: `docker run --rm --entrypoint
/opt/venv/bin/pip <image> freeze --all`. Remove packages built from source (`name @ file://...`) from the file
   and record them in `out-of-lock.json` with their pinned commit.
3. Write the result to `frozen-set.txt` (keep the header comment), then `python ../tools/sync_constraints.py .`.
4. Copy the new upstream requirements file to `upstream/<file>.recorded` and update the matching lines in
   `pyproject.toml` `dependencies`. A line that a later Dockerfile step replaces gets an entry in
   `override-dependencies` with a comment.

    A dependency the image never runs is left out the same way, with the entry `"<name> ; sys_platform == 'never'"`:
    no platform matches the marker, so uv drops the package and everything only it needs. Stable3DGen's gradio is
    the case: only the upstream demo `app.py` imports it, and it holds pillow below 13. The comment names why the
    package is unused, and the Dockerfile imports the modules a job runs at build time (`Dockerfile.stable3dgen`),
    so leaving out a package the job does need fails the build rather than a job. The package's lines also come out
    of `frozen-set.txt` (step 3), whose header says what moved on purpose.

5. Move `exclude-newer` in `pyproject.toml` (an absolute date, not "7 days") to the day of the build you froze in
   step 2, never earlier: an earlier date or a relative window rejects versions that build installed, and
   `uv lock --check` then fails. Then `uv lock`, then the three exports above.
6. Build the image. The build runs both checks. Compare `pip freeze --all` of the new image with
   `frozen-set.txt`: the diff must be empty, or every difference must be one you meant.
7. Run `python .github/scripts/python_dependency_audit.py` from the repository root. An advisory in a version that
   stays is added to `.github/python-audit-allowlist.json` with a reason and a review date.

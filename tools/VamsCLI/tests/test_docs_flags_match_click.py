"""Every CLI flag the documentation shows must exist on the command it is shown under.

A documented flag that does not exist is worse than an undocumented one: the reader pastes the line,
Click exits 2 with "No such option", and they have no way to tell a documentation error from a
product error. Nothing else catches this. `documentation/docusaurus-site/docs/cli/` is the single
source of truth for CLI documentation and is written by hand, the Docusaurus build only validates
links and MDX, and the drift is invisible at import time because the docs are not Python.

Two failure shapes have both occurred in this documentation, so there are two checks:

1. **Option tables** — a command's reference table listing an option the command does not take.
2. **Fenced examples** — a runnable `vamscli ...` line carrying an option the command does not take.

Both resolve each documented command against the live Click tree (`vamscli.main.cli`) and compare
against that exact command's `params`. Resolving **per command** rather than per group is the point:
a flag can be real on nine sibling commands and absent on the tenth (`--confirm` exists on
`assets delete` but not on `pipeline delete`), so a group-level union check reports clean on exactly
the case worth catching. Long forms and short forms are both checked, character for character.

Scope limits, deliberate and worth knowing before trusting a pass:

-   The option-table check reads only the **first column** of a table row. A flag named in a
    table's description text is not validated.
-   Neither check reads **prose**. A flag mentioned in a sentence outside a fenced code block is
    not validated. The example check is fence-scoped on purpose — parsing prose produces false
    positives from shell fragments, and one is already handled below (see `_SHELL_STOP`).
-   Neither check validates option *arguments*, *types*, or whether a required option was omitted.

`test_checkers_have_coverage` guards both: a checker whose command resolution silently breaks would
find nothing and report clean, so the counts are asserted rather than merely printed. The control
tests assert the checkers still *fire* on a known-bad input for the same reason.
"""

import glob
import os
import re
import shlex

import pytest

from vamscli.main import cli

# ---------------------------------------------------------------------------
# Locating the documentation
# ---------------------------------------------------------------------------

# tests/ -> VamsCLI/ -> tools/ -> repo root
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_DOCS_CLI = os.path.join(_REPO_ROOT, "documentation", "docusaurus-site", "docs", "cli")


def _discover_pages():
    """Every CLI documentation page, so a newly added page is checked without editing this file."""
    pages = []
    for ext in ("*.md", "*.mdx"):
        pages.extend(glob.glob(os.path.join(_DOCS_CLI, "**", ext), recursive=True))
    return sorted(pages)


_PAGES = _discover_pages()


def _page_id(path):
    return os.path.relpath(path, _DOCS_CLI).replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Click tree introspection
# ---------------------------------------------------------------------------


def _root_global_opts():
    """Root-group options and which of them take a value, read from Click rather than hardcoded.

    Derived so that adding a global option does not turn every example that uses it into a failure.
    Arity matters: `--profile production` is followed by its value, and that value must not be
    mistaken for a subcommand name while walking the command path.
    """
    opts = {"--help"}
    takes_value = set()
    for param in cli.params:
        spellings = [o for o in list(param.opts) + list(param.secondary_opts) if o.startswith("-")]
        opts.update(spellings)
        if not getattr(param, "is_flag", False) and getattr(param, "nargs", 1) != 0:
            takes_value.update(spellings)
    return opts, takes_value


_GLOBAL_OPTS, _GLOBAL_OPTS_WITH_VALUE = _root_global_opts()


def _resolve(parts):
    """Walk a command path (['assets', 'export']) to its Click node, or None if it does not exist."""
    node = cli
    for part in parts:
        commands = getattr(node, "commands", None)
        if not commands or part not in commands:
            return None
        node = commands[part]
    return node


def _resolve_leaf(parts):
    """Resolve a command path to a leaf command. Groups return None -- a group has no own options."""
    node = _resolve(parts)
    if node is None or hasattr(node, "commands"):
        return None
    return node


def _flags_of(command):
    """Every option spelling the command accepts, including short forms and `--x/--no-x` secondaries."""
    flags = set()
    for param in command.params:
        for opt in list(param.opts) + list(param.secondary_opts):
            if opt.startswith("-"):
                flags.add(opt)
    return flags


# ---------------------------------------------------------------------------
# Check 1: option tables
# ---------------------------------------------------------------------------

# `## assets export`, `### file set-primary`, optionally written as `## vamscli assets export`.
# Lowercase-only by design: prose headings ("### Criteria operators") must not resolve.
_COMMAND_HEADING = re.compile(
    r"^#{2,4}\s+(?:vamscli\s+)?([a-z][a-z0-9-]*(?:\s+[a-z][a-z0-9-]*)*)\s*$"
)
_TABLE_FLAG = re.compile(r"`(--?[A-Za-z0-9][A-Za-z0-9-]*)`")


def check_option_tables(text):
    """Validate option-table rows against the command named by the enclosing heading.

    Returns (problems, cells_checked, commands_resolved).
    """
    problems = []
    cells_checked = 0
    commands_resolved = 0

    current_name = None
    current_flags = None
    in_fence = False

    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        heading = _COMMAND_HEADING.match(line)
        if heading:
            parts = heading.group(1).split()
            command = _resolve_leaf(parts)
            if command is None:
                current_name, current_flags = None, None
            else:
                current_name, current_flags = " ".join(parts), _flags_of(command)
                commands_resolved += 1
            continue

        if current_flags is None or not line.startswith("|"):
            continue

        cells = line.strip().strip("|").split("|")
        if len(cells) < 3:
            continue

        for flag in _TABLE_FLAG.findall(cells[0]):
            if flag in _GLOBAL_OPTS:
                continue
            cells_checked += 1
            if flag not in current_flags:
                problems.append(
                    f"line {lineno}: option table for `{current_name}` documents {flag}, "
                    f"which is not a parameter of that command. Real options: "
                    f"{', '.join(sorted(current_flags))}"
                )

    return problems, cells_checked, commands_resolved


# ---------------------------------------------------------------------------
# Check 2: fenced examples
# ---------------------------------------------------------------------------

# A documentation example is often a shell one-liner: `$(vamscli assets list ... | jq -r '...')`.
# Everything from the first shell operator onward belongs to another program, and attributing
# `jq -r` to `assets list` is a false positive that this set exists to prevent.
_SHELL_STOP = {"|", "||", "&&", ";", "&", ">", ">>", "<", "<<"}
_LONG_OPT = re.compile(r"--[A-Za-z0-9][A-Za-z0-9-]*$")
_SHORT_OPT = re.compile(r"-[A-Za-z]$")


def _tokenize(command_line):
    """Split a `vamscli ...` line, stopping at the first token that hands off to another program."""
    try:
        tokens = shlex.split(command_line, posix=True)
    except ValueError:
        return None  # unbalanced quotes in a prose-ish line; not an invocation we can judge
    kept = []
    for token in tokens:
        if token in _SHELL_STOP or ")" in token or token.endswith(";"):
            break
        kept.append(token)
    return kept


def _split_invocation(tokens):
    """Separate a `vamscli` invocation into its parts.

    Returns (node, path, pre_command_opts, rest) where `pre_command_opts` are options that appeared
    *before* the subcommand name -- the global position -- and `rest` is everything from the first
    option after the subcommand onward.

    Options in the global position are collected rather than assumed valid. An unrecognized one must
    not abort the walk: `vamscli --debug assets download ...` is precisely the shape of a documented
    flag that does not exist, and stopping there would silently skip the line instead of reporting
    it. A recognized global that takes a value has its value skipped so it cannot be mistaken for a
    subcommand name; for an unrecognized option the arity is unknowable, so a following token that
    happens to match a subcommand name can mis-attribute the command -- the unrecognized option is
    still reported either way.
    """
    node = cli
    path = []
    pre_opts = []
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            if path:
                break  # options after the subcommand belong to the subcommand
            pre_opts.append(token)
            index += 1
            if token.split("=")[0] in _GLOBAL_OPTS_WITH_VALUE and "=" not in token:
                index += 1  # skip the option's value
            continue

        commands = getattr(node, "commands", None)
        if commands and token in commands:
            node = commands[token]
            path.append(token)
            index += 1
            continue

        if path:
            break  # a positional argument of the resolved command
        index += 1  # a stray value before the command was named; keep looking

    return node, path, pre_opts, tokens[index:]


def check_examples(text):
    """Validate options in fenced `vamscli ...` examples against the command they are used on.

    Returns (problems, invocations_checked).
    """
    problems = []
    invocations = 0
    in_fence = False

    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            continue

        stripped = line.strip()
        if "vamscli " not in stripped:
            continue

        command_line = stripped[stripped.index("vamscli "):].rstrip("\\").strip()
        tokens = _tokenize(command_line)
        if tokens is None:
            continue

        node, path, pre_opts, rest = _split_invocation(tokens[1:])
        if not path or hasattr(node, "commands"):
            continue  # a group, or an unrecognized path -- nothing to attribute options to

        invocations += 1

        # Options before the subcommand are root-group options and nothing else.
        for token in pre_opts:
            flag = token.split("=")[0]
            if not (_LONG_OPT.match(flag) or _SHORT_OPT.match(flag)):
                continue
            if flag not in _GLOBAL_OPTS:
                problems.append(
                    f"line {lineno}: example places {flag} before the command, but the root group "
                    f"has no such option. Global options: {', '.join(sorted(_GLOBAL_OPTS))}"
                )

        allowed = _flags_of(node) | _GLOBAL_OPTS
        for token in rest:
            flag = token.split("=")[0]
            if not flag.startswith("-"):
                continue
            if not (_LONG_OPT.match(flag) or _SHORT_OPT.match(flag)):
                continue
            if flag not in allowed:
                problems.append(
                    f"line {lineno}: `vamscli {' '.join(path)}` example passes {flag}, "
                    f"which is not a parameter of that command. Real options: "
                    f"{', '.join(sorted(allowed - _GLOBAL_OPTS))}"
                )

    return problems, invocations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.skipif(not _PAGES, reason="CLI documentation not present in this checkout")
@pytest.mark.parametrize("page", _PAGES, ids=_page_id)
def test_option_tables_match_click(page):
    """No option-table row documents a flag its command does not accept."""
    problems, _, _ = check_option_tables(_read(page))
    assert not problems, "{}:\n  {}".format(_page_id(page), "\n  ".join(problems))


@pytest.mark.skipif(not _PAGES, reason="CLI documentation not present in this checkout")
@pytest.mark.parametrize("page", _PAGES, ids=_page_id)
def test_examples_match_click(page):
    """No fenced `vamscli` example passes a flag its command does not accept."""
    problems, _ = check_examples(_read(page))
    assert not problems, "{}:\n  {}".format(_page_id(page), "\n  ".join(problems))


def test_checkers_have_coverage():
    """The checkers must actually resolve commands and inspect flags.

    A checker whose heading regex or command resolution breaks inspects nothing and reports clean,
    so the two tests above would pass while validating nothing at all. These floors are set well
    below the counts measured when the test was written (28 pages, 135 command headings, 651 option
    cells, 833 example invocations) so that ordinary documentation edits do not trip them, while a
    resolution failure -- which drops the counts to zero -- does.
    """
    assert os.path.isdir(_DOCS_CLI), f"CLI documentation directory not found: {_DOCS_CLI}"
    assert len(_PAGES) >= 15, f"only discovered {len(_PAGES)} CLI documentation pages"

    total_cells = total_commands = total_invocations = 0
    for page in _PAGES:
        text = _read(page)
        _, cells, commands = check_option_tables(text)
        _, invocations = check_examples(text)
        total_cells += cells
        total_commands += commands
        total_invocations += invocations

    assert total_commands >= 60, f"resolved only {total_commands} documented commands"
    assert total_cells >= 300, f"checked only {total_cells} option-table flag cells"
    assert total_invocations >= 300, f"checked only {total_invocations} example invocations"


def test_flag_sets_vary_across_commands():
    """Control: the field these checks compare against must actually vary per command.

    A document-versus-source check is only meaningful if the source field it reads varies the way
    the document does. Comparing a column against a field that happens to hold one value everywhere
    produces confident nonsense -- every pipeline schema in this repository declares
    `executionType: "Lambda"`, so a check reading that field to validate a per-pipeline "compute"
    column would be wrong for every row.

    `Command.params` passes that test decisively: 153 leaf commands carry 113 distinct flag sets,
    from 0 to 28 options each. The floors below are loose; they exist so that a refactor to one
    shared option set would fail here -- announcing that these checks had silently degraded into the
    group-level union check they were written to avoid -- rather than continuing to pass.
    """
    def leaves(node, path=()):
        commands = getattr(node, "commands", None)
        if not commands:
            if path:
                yield path, node
            return
        for name, child in commands.items():
            yield from leaves(child, path + (name,))

    flag_sets = {path: frozenset(_flags_of(cmd)) for path, cmd in leaves(cli)}
    assert len(flag_sets) >= 100, f"resolved only {len(flag_sets)} leaf commands"

    distinct = set(flag_sets.values())
    assert len(distinct) >= 50, (
        f"only {len(distinct)} distinct flag sets across {len(flag_sets)} commands -- "
        "a per-command check against a near-constant field is a union check in disguise"
    )


def test_resolution_distinguishes_sibling_commands():
    """Control: per-command resolution, which is the whole reason this file is not a union check.

    `--confirm` is real on `assets delete` and absent from `pipeline delete`. Across the 21 commands
    named `delete` it is present on 10 and absent on 11, so neither is the "normal" case and no
    intuition from sibling commands can substitute for reading the decorator. A checker that unioned
    a group's flags would accept it everywhere.
    """
    assets_delete = _resolve_leaf(["assets", "delete"])
    pipeline_delete = _resolve_leaf(["pipeline", "delete"])
    assert assets_delete is not None and pipeline_delete is not None

    assert "--confirm" in _flags_of(assets_delete)
    assert "--confirm" not in _flags_of(pipeline_delete)
    assert "--pipeline-id" in _flags_of(pipeline_delete)

    # A group resolves to None: groups carry no options of their own.
    assert _resolve_leaf(["assets"]) is None
    assert _resolve_leaf(["no", "such", "command"]) is None


def test_option_table_checker_reports_a_bad_flag():
    """Control: the table checker fires on a flag that does not exist."""
    page = "\n".join([
        "## assets get",
        "",
        "| Option          | Type | Required | Description |",
        "| --------------- | ---- | -------- | ----------- |",
        "| `--show-archived` | Flag | No | Real option |",
        "| `--no-such-flag`  | Flag | No | Invented option |",
    ])
    problems, cells, commands = check_option_tables(page)

    assert commands == 1, "heading did not resolve to a command"
    assert cells == 2
    assert len(problems) == 1
    assert "--no-such-flag" in problems[0]


def test_example_checker_reports_a_bad_flag_and_accepts_the_real_one():
    """Control: the example checker fires on `--debug` and passes the real `--verbose`.

    `--debug` was documented as a global flag on the diagnostics section of the assets/files
    troubleshooting page; the root group has never had it.
    """
    bad = "```bash\nvamscli --debug assets download /local/path -d my-db -a my-asset\n```"
    good = "```bash\nvamscli --verbose assets download /local/path -d my-db -a my-asset\n```"

    problems, invocations = check_examples(bad)
    assert invocations == 1, "example did not resolve to a command"
    assert len(problems) == 1
    assert "--debug" in problems[0]

    problems, invocations = check_examples(good)
    assert invocations == 1
    assert problems == []


def test_example_checker_ignores_a_piped_program():
    """Control: options after a shell operator belong to the other program, not to vamscli.

    `jq -r` in `$(vamscli assets list ... | jq -r '...')` is not an `assets list` option.
    """
    page = (
        "```bash\n"
        "for a in $(vamscli assets list -d my-db --json-output | jq -r '.assets[].assetId'); do\n"
        "```"
    )
    problems, invocations = check_examples(page)
    assert invocations == 1
    assert problems == [], f"shell handoff misattributed: {problems}"


def test_example_checker_validates_short_forms():
    """Control: short options are checked too, not just long ones."""
    page = "```bash\nvamscli assets get my-asset -d my-db -Z bogus\n```"
    problems, invocations = check_examples(page)
    assert invocations == 1
    assert len(problems) == 1
    assert "-Z" in problems[0]

#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""`get_k8s_client` must treat a private EKS endpoint as the endpoint, not as an obstacle.

The cluster's Kubernetes API endpoint is private, so it resolves to a VPC address. The client used to
read that address as a sign it had been misrouted and try to reach the control plane's PUBLIC address
instead — "Detected private IP 10.1.4.153, attempting to bypass VPC endpoint", then "Attempting to
force NAT Gateway route by disabling VPC endpoint DNS". That logic was written when the cluster's
endpoint was public and the Lambda reached it out through the NAT gateway; against a private endpoint
it is precisely backwards.

Two independent defects are covered here, and neither is visible in a normal read of the file.

1.  **`os` was shadowed inside `get_eks_token`.** A local `import os` sits several hundred lines into
    the function, in a fallback branch. Python decides a name is local at COMPILE time from any
    binding anywhere in the function body, so the earlier `os.path.exists(token_file)` raised
    `UnboundLocalError` — reported as the warning "Failed to get identity: cannot access local
    variable 'os' where it is not associated with a value", which reads like a permissions problem.
    Asserted through `symtable`, which is the same compile-time scope decision the interpreter makes,
    rather than by calling the function: the failing branch is wrapped in `except Exception`, so
    executing it proves nothing.

2.  **`hostname` was assigned inside the `try` whose handler referenced it.** A resolution failure on
    the first statement therefore raised `NameError` from the handler instead of logging the warning
    it was written to log.
"""

import ast
import os
import symtable

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SOURCE_PATH = os.path.join(_LAMBDA_DIR, "kubernetes_utils.py")

with open(_SOURCE_PATH, encoding="utf-8") as _fh:
    SOURCE = _fh.read()


def _scope(name):
    """The symtable entry for a function at any nesting depth."""

    def walk(table):
        if table.get_name() == name:
            return table
        for child in table.get_children():
            found = walk(child)
            if found is not None:
                return found
        return None

    return walk(symtable.symtable(SOURCE, "kubernetes_utils.py", "exec"))


class TestModuleIsWellFormed:
    def test_the_functions_under_test_exist(self):
        """The control. Every assertion below is satisfied by a file in which these functions were
        renamed or removed, and `_scope` returning None would make the scope checks vacuous."""
        assert _scope("get_k8s_client") is not None
        assert _scope("get_eks_token") is not None

    def test_os_is_imported_exactly_once_at_module_level(self):
        """The module imported `os` twice, which is what made a third import look unremarkable."""
        module = ast.parse(SOURCE)
        top_level = [
            alias.name
            for node in module.body
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert top_level.count("os") == 1


class TestOsIsNotShadowedInsideTokenGeneration:
    def test_os_is_not_a_local_of_get_eks_token(self):
        symbol = _scope("get_eks_token").lookup("os")
        assert not symbol.is_local(), (
            "a binding of 'os' inside get_eks_token makes every earlier os.* use raise "
            "UnboundLocalError, whatever order the branches run in"
        )

    def test_os_is_still_reachable_there(self):
        """The other half. Removing the import must not have left the name unresolvable — the
        function does use `os.path.exists` on the service-account token files."""
        assert "os.path.exists" in SOURCE
        symbol = _scope("get_eks_token").lookup("os")
        assert symbol.is_global() or symbol.is_free(), (
            "os must resolve to an enclosing or module scope for the token-file check to run"
        )


class TestPrivateEndpointIsUsedDirectly:
    def test_no_attempt_is_made_to_route_around_a_private_address(self):
        for phrase in (
            "attempting to bypass VPC endpoint",
            "force NAT Gateway route",
            "dns.resolver",
            "8.8.8.8",
        ):
            assert phrase not in SOURCE, (
                f"{phrase!r} routes around the private endpoint that is now the only way in"
            )

    def test_the_endpoint_is_never_rewritten_to_a_bare_ip(self):
        """Substituting a resolved address for the hostname breaks TLS certificate verification even
        when the address is reachable, so the request fails for a second, unrelated reason."""
        assert "endpoint.replace(hostname" not in SOURCE

    def test_hostname_is_bound_before_the_resolution_attempt(self):
        """Guards the handler that reports a resolution failure: it interpolates `hostname`, so the
        assignment has to precede the `try` rather than sit inside it."""
        tree = ast.parse(SOURCE)
        assignments, uses_in_handler = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "hostname" for t in node.targets
            ):
                assignments.append(node.lineno)
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    for inner in ast.walk(handler):
                        if isinstance(inner, ast.Name) and inner.id == "hostname":
                            uses_in_handler.append((node.lineno, inner.lineno))

        assert assignments, "hostname is no longer assigned; this test needs rewriting"
        for try_line, use_line in uses_in_handler:
            assert any(a < try_line for a in assignments), (
                f"hostname used at line {use_line} in the handler of the try at line {try_line}, "
                f"but is only assigned at {assignments} — a failure on the first statement raises "
                f"NameError from the handler instead of logging"
            )

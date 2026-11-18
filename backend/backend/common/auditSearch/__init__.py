# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The audit search library: building a safe CloudWatch Logs Insights query, parsing the records it
returns, redacting and encoding them, and rechecking every filter on the parsed row.

It is a pure library. It calls no AWS service, reads no configuration and registers no route; the
audit search handler (a later change) wires it to Logs Insights and to authorization.

* ``queryBuilder`` validates a request into enumerated filters and builds the query text from them.
* ``containsFilter`` validates and evaluates the ``contains`` free-text terms.
* ``parsers`` read a record into a row, dispatching on ``auditVersion`` and on the group's legacy
  adapter.
* ``rowModel`` is the row a search returns; ``recordMatcher`` rechecks the filters on a parsed row.
* ``redaction`` and ``exportEncoding`` make a row safe to return and to export.

Logs Insights narrows the candidates; the server decides. Nothing from a caller reaches the query
text except a validated, escaped literal, and no regular expression is built from caller input.
"""

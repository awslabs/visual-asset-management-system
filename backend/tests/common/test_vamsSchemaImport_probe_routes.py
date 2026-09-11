#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every path the vamsSchema importer sends must match a registered API route.

The importer probes ``existsPath`` (GET) to decide create (POST) vs update (PUT). That probe works
today only because ``pipelineService`` branches on the presence of a path parameter instead of calling
``ApiRoute.matches``, and because ``enforceAPI`` auto-approves a ``lambdaCrossCall`` before consulting
the route. A path that matches no route is therefore invisible until either of those changes — at which
point the probe takes the wrong branch, every redeploy attempts a CREATE of an existing pipeline, and
the registration custom resource fails the whole deploy for all built-ins.
"""

import pytest

from backend.backend.common import apiRoutes
from backend.backend.common.workflows import vamsSchemaImport as vsi


def _bundle():
    """A minimal bundle exercising the pipeline, template, workflow, and trigger request paths."""
    return {
        "pipeline": {
            "pipelineId": "my-pipeline",
            "pipelineName": "My Pipeline",
            "databaseId": "GLOBAL",
            "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}},
            "systemConfig": {"inputFileArity": "one"},
        },
        "workflow": {
            "workflowId": "my-workflow",
            "workflowName": "My Workflow",
            "databaseId": "GLOBAL",
            "pipelines": [{"databaseId": "GLOBAL", "pipelineId": "my-pipeline"}],
        },
        "templates": [
            {"templateId": "my-template", "templateName": "My Template", "configBody": "{}"}
        ],
    }


def _paths(requests):
    """(kind, field, path) for every path field present on each request."""
    for req in requests:
        for field in ("existsPath", "createPath", "updatePath"):
            path = req.get(field)
            if path:
                yield req.get("kind", "?"), field, path


def test_every_importer_path_matches_a_registered_route():
    requests = vsi.build_import_requests(_bundle())
    assert requests, "the bundle produced no import requests"

    unmatched = [
        (kind, field, path)
        for kind, field, path in _paths(requests)
        if not any(route.matches(path) for route in apiRoutes.ALL_API_ROUTES)
    ]
    assert not unmatched, f"importer paths matching no registered API route: {unmatched}"


def test_the_matcher_can_detect_an_unregistered_path():
    # Positive control: without it, a matcher that accepted everything would pass the check above.
    # This is the exact shape the importer used for its pipeline probe.
    bogus = "/pipelines/GLOBAL/my-pipeline"
    assert not any(route.matches(bogus) for route in apiRoutes.ALL_API_ROUTES)


@pytest.mark.parametrize("kind", ["pipeline", "workflow"])
def test_exists_probe_shares_the_route_of_its_update(kind):
    # The probe and the update act on the same single entity, so they must resolve to the same route.
    requests = vsi.build_import_requests(_bundle())
    req = next(r for r in requests if r.get("kind") == kind)
    assert req["existsPath"] == req["updatePath"]

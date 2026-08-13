# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v1 request model for the Physna Viewer metadata endpoint.

The endpoint is invoked as:

    GET /addon/physna/viewer?databaseId=...&assetId=...&relativePath=...

and returns JSON. The frontend uses the response to build a direct
``<iframe src="${physnaApiBase}/tenants/{tenantId}/viewer/asset?...">``
pointing at Physna's hosted viewer, bypassing all JS/HTML proxying.
"""

from typing import Optional

from pydantic import Field

# The Lambda runtime ships aws_lambda_powertools 2.36.0 + Pydantic 1.10.7,
# where root_validator is re-exported from the parser module. In older dev/
# test environments that ship a newer powertools (3.x) this re-export is
# missing, so we fall back to importing directly from pydantic. BaseModel is
# re-exported consistently, so we always get that via powertools.
from aws_lambda_powertools.utilities.parser import BaseModel  # noqa: F401
try:  # pragma: no cover — runtime-dependent branch
    from aws_lambda_powertools.utilities.parser import root_validator
except ImportError:  # pragma: no cover
    from pydantic import root_validator  # type: ignore[assignment]

from customLogging.logger import safeLogger
from common.validators import (
    validate,
    id_pattern,
    filename_pattern,
    relative_file_path_pattern,
)

logger = safeLogger(service_name="PhysnaViewerModels")


class PhysnaViewerRequestModel(BaseModel, extra="ignore"):
    """Query-string parameters accepted by ``GET /addon/physna/viewer``.

    The ``theme`` and ``origin`` params that older versions of this endpoint
    accepted were used only to build an HTML response; the lambda now returns
    JSON and the frontend handles theme / parentOrigin itself when
    assembling the Physna viewer URL. Kept minimal on purpose.
    """

    databaseId: str = Field(
        min_length=4,
        max_length=256,
        strip_whitespace=True,
        regex=id_pattern,
    )
    assetId: str = Field(
        min_length=1,
        max_length=256,
        strip_whitespace=False,
        regex=filename_pattern,
    )
    relativePath: str = Field(
        min_length=2,
        max_length=1024,
        strip_whitespace=False,
        regex=relative_file_path_pattern,
    )

    # ``skip_on_failure=True`` is a no-op under Pydantic v1 (Lambda runtime)
    # and required under Pydantic v2's compatibility shim (dev/test envs);
    # passing it here keeps both worlds happy.
    @root_validator(skip_on_failure=True)
    def _validate_fields(cls, values):  # noqa: N805 — pydantic signature
        (valid, message) = validate(
            {
                "databaseId": {
                    "value": values.get("databaseId"),
                    "validator": "ID",
                },
                "assetId": {
                    "value": values.get("assetId"),
                    "validator": "ASSET_ID",
                },
                "relativePath": {
                    "value": values.get("relativePath"),
                    "validator": "RELATIVE_FILE_PATH",
                },
            }
        )
        if not valid:
            raise ValueError(message)
        return values

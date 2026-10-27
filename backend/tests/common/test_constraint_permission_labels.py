# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The constraint editor's permission labels name the HTTP verb only.

Which of PUT and POST creates and which updates depends on the object type (see
documentation/docusaurus-site/docs/concepts/permissions-model.md): on a pipeline or workflow POST
creates and PUT modifies, while creating an asset checks PUT. A label that names one meaning is
wrong for some object type, so the labels name the verb and nothing else.
"""

import pytest

from backend.backend.common.constants import CONSTRAINT_PERMISSION_LABELS

VERB_MEANINGS = ("add", "create", "update", "modify", "edit", "new")


@pytest.mark.unit
class TestConstraintPermissionLabels:
    @pytest.mark.parametrize("verb", ["PUT", "POST"])
    def test_the_write_verb_labels_carry_no_create_or_update_wording(self, verb):
        labels = [entry["label"] for entry in CONSTRAINT_PERMISSION_LABELS if entry["value"] == verb]

        assert labels == [verb]
        assert not any(word in labels[0].lower() for word in VERB_MEANINGS)

    def test_every_verb_is_listed_once_with_its_value(self):
        assert [entry["value"] for entry in CONSTRAINT_PERMISSION_LABELS] == ["GET", "PUT", "POST", "DELETE"]

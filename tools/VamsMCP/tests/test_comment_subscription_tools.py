"""The comment, subscription, metadata-schema and API-key tools.

Three response shapes in one group, and the differences are load-bearing:

*   the comment listings nest a BARE ARRAY under ``message``, which both ``unwrap_message`` (it only
    unwraps a dict) and ``paginate()`` (it reads ``Items`` off the page) hand back as zero rows;
*   the subscription listing nests the usual Items/NextToken page under ``message``;
*   the metadata-schema and API-key calls carry no envelope at all.

A tool wired to the wrong one of these returns an empty success rather than an error, so each shape
is asserted against a real payload rather than a MagicMock's default.

The write and destructive BODIES live in ``test_gated_tools.py``, which reloads the module with both
gates on. The docstring contracts are here for all of them: ``_docstring_of`` reads the source, so it
works whether or not the tool is registered.
"""

import inspect

import pytest

from vams_mcp import server


@pytest.fixture
def mock_client(monkeypatch):
    from unittest.mock import MagicMock

    client = MagicMock()
    monkeypatch.setattr(server, "CLIENT", client)
    return client


@pytest.fixture
def real_paginate_client(mock_client):
    """A mock client whose paginate/unwrap_message are the REAL implementations.

    The subscription listing's envelope is unwrapped inside ``paginate()``, so a MagicMock paginate
    cannot show whether the items were found.
    """
    mock_client.config = server.CONFIG
    mock_client.unwrap_message = server.VamsClient.unwrap_message
    mock_client.paginate = lambda *args, **kwargs: server.VamsClient.paginate(
        mock_client, *args, **kwargs
    )
    return mock_client


def _docstring_of(name):
    """The named function's docstring, from the source, collapsed to single-spaced text.

    The gated tools are not registered at the default settings, so their docstrings are unreachable
    through the tool metadata. Collapsing keeps an assertion from depending on where a sentence
    happens to wrap, matching the helper in ``test_server_tools.py``.
    """
    import ast
    from pathlib import Path

    source = Path(server.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return " ".join((ast.get_docstring(node) or "").split())
    raise AssertionError(f"no function named {name!r} in server.py")


_COMMENTS_ENVELOPE = {
    "message": [
        {"assetId": "a1", "assetVersionId:commentId": "v1:c1", "commentBody": "first"},
        {"assetId": "a1", "assetVersionId:commentId": "v1:c2", "commentBody": "second"},
    ]
}


# --- Comment listings: the bare array under `message` ------------------------


def test_list_asset_comments_lifts_the_bare_array_onto_items(mock_client):
    mock_client.api.list_asset_comments.return_value = _COMMENTS_ENVELOPE
    result = server.list_asset_comments("a1")
    assert result["count"] == 2
    assert [row["commentBody"] for row in result["Items"]] == ["first", "second"]


def test_list_asset_comments_does_not_route_through_paginate(mock_client):
    """paginate() would send a startingToken and read `Items` off a page that has neither, returning
    a clean empty list for an asset that has comments. Pinned because the fix is to NOT use it."""
    mock_client.api.list_asset_comments.return_value = _COMMENTS_ENVELOPE
    server.list_asset_comments("a1")
    assert not mock_client.paginate.called


def test_list_asset_comments_forwards_both_bound_parameters(mock_client):
    mock_client.api.list_asset_comments.return_value = {"message": []}
    server.list_asset_comments("a1", max_items=5, page_size=2)
    call = mock_client.api.list_asset_comments.call_args
    assert call.args == ("a1",)
    assert call.kwargs == {"max_items": 5, "page_size": 2}


def test_list_asset_comments_flags_a_result_that_reached_max_items(mock_client):
    """The route discards its pagination token, so this flag is the only thing between a bounded read
    and an agent reporting the count as a total."""
    mock_client.api.list_asset_comments.return_value = _COMMENTS_ENVELOPE
    result = server.list_asset_comments("a1", max_items=2)
    assert result["truncated"] is True
    assert "no continuation token" in result["note"]


def test_list_asset_comments_adds_no_bound_keys_below_the_limit(mock_client):
    mock_client.api.list_asset_comments.return_value = _COMMENTS_ENVELOPE
    result = server.list_asset_comments("a1", max_items=50)
    assert "truncated" not in result and "note" not in result


def test_list_asset_comments_falls_back_to_page_size_as_the_bound(mock_client):
    """The handler sets maxItems from pageSize when only pageSize is given, so a full page is a
    bounded result even though the caller named no maximum."""
    mock_client.api.list_asset_comments.return_value = _COMMENTS_ENVELOPE
    result = server.list_asset_comments("a1", page_size=2)
    assert result["truncated"] is True


def test_the_default_bound_is_flagged_when_the_caller_narrows_nothing():
    """The case a max_items-only check misses.

    Asserted on the helper directly rather than through a mocked call: the flag has to fire on the
    server's own default with no parameter supplied at all, which is only visible at that size.
    """
    rows = [{"commentBody": str(i)} for i in range(server.COMMENT_LIST_DEFAULT_BOUND)]
    result = server._bounded_message_list({"message": rows}, None, None, "comment")
    assert result["truncated"] is True
    assert str(server.COMMENT_LIST_DEFAULT_BOUND) in result["note"]

    one_short = {"message": rows[:-1]}
    assert "truncated" not in server._bounded_message_list(one_short, None, None, "comment")


@pytest.mark.parametrize("page", [{}, {"message": None}, {"message": "Succeeded"}, None])
def test_bounded_message_list_tolerates_a_wrong_message_shape(page):
    """A 200 whose body is not the documented array must read as zero rows rather than raise: the
    tool would otherwise return an AttributeError string where the API reported an empty thread."""
    assert server._bounded_message_list(page, None, None, "comment") == {"Items": [], "count": 0}


def test_list_asset_version_comments_scopes_to_one_version(mock_client):
    mock_client.api.list_asset_version_comments.return_value = _COMMENTS_ENVELOPE
    result = server.list_asset_version_comments("a1", "v1", max_items=10)
    call = mock_client.api.list_asset_version_comments.call_args
    assert call.args == ("a1", "v1")
    assert call.kwargs == {"max_items": 10, "page_size": None}
    assert result["count"] == 2
    # Not the asset-wide route, which would also return the other versions' comments.
    assert not mock_client.api.list_asset_comments.called


@pytest.mark.parametrize("tool", ["list_asset_comments", "list_asset_version_comments"])
def test_the_comment_listings_deliberately_take_no_starting_token(tool):
    """The route accepts a startingToken and returns none, so a `starting_token` parameter here could
    never be filled from a previous call — an agent-visible knob with no reachable value. The bound is
    reported instead. Adding one is the change this pins against."""
    parameters = inspect.signature(getattr(server, tool)).parameters
    assert "starting_token" not in parameters
    assert "max_items" in parameters and "page_size" in parameters


@pytest.mark.parametrize("tool", ["list_asset_comments", "list_asset_version_comments"])
def test_the_comment_listings_expose_no_show_deleted_flag(tool):
    """The handler forwards showDeleted and the service ignores it on these two routes, so the
    parameter would silently do nothing — worse than its absence."""
    assert "show_deleted" not in inspect.signature(getattr(server, tool)).parameters


# --- get_comment ------------------------------------------------------------


def test_get_comment_unwraps_the_message_envelope(mock_client):
    mock_client.unwrap_message = server.VamsClient.unwrap_message
    mock_client.api.get_comment.return_value = {"message": {"commentBody": "hello"}}
    result = server.get_comment("a1", "v1", "c1")
    assert result == {"commentBody": "hello"}
    assert mock_client.api.get_comment.call_args.args == ("a1", "v1", "c1")


def test_get_comment_surfaces_not_found_rather_than_an_empty_success(mock_client):
    """The endpoint answers 200 with {} for a missing comment, and APIClient turns that emptiness
    into CommentNotFoundError. @tool_result must relay it as an error, not as an empty object."""
    from vamscli.utils.exceptions import CommentNotFoundError

    mock_client.unwrap_message = server.VamsClient.unwrap_message
    mock_client.api.get_comment.side_effect = CommentNotFoundError("Comment 'c1' not found")
    result = server.get_comment("a1", "v1", "c1")
    assert result["error_type"] == "CommentNotFoundError"


# --- Subscriptions ----------------------------------------------------------


def test_list_subscriptions_unwraps_the_nested_page(real_paginate_client):
    """Items and NextToken sit UNDER `message` here, unlike the comment listings' bare array."""
    real_paginate_client.api.list_subscriptions.return_value = {
        "message": {"Items": [{"entityId": "asset-1", "subscribers": ["u1"]}]}
    }
    result = server.list_subscriptions()
    assert result["count"] == 1
    assert result["Items"][0]["entityId"] == "asset-1"


def test_list_subscriptions_forwards_paging_as_named_arguments(real_paginate_client):
    """`APIClient.list_subscriptions` takes page_size/starting_token as keywords, not a params dict,
    so the callable has to translate rather than forward paginate()'s dict."""
    real_paginate_client.api.list_subscriptions.return_value = {"message": {"Items": []}}
    server.list_subscriptions(starting_token="resume-here")
    kwargs = real_paginate_client.api.list_subscriptions.call_args.kwargs
    assert kwargs["starting_token"] == "resume-here"
    assert kwargs["page_size"] == server.CONFIG.page_size


def test_list_subscriptions_reports_an_outstanding_token(real_paginate_client):
    real_paginate_client.api.list_subscriptions.return_value = {
        "message": {"Items": [{"entityId": "asset-1"}], "NextToken": "more"}
    }
    result = server.list_subscriptions(max_items=1)
    assert result["truncated"] is True
    assert result["NextToken"] == "more"


@pytest.mark.parametrize(
    "message,subscribed,unrecognized",
    [
        ("success", True, False),
        ("Subscription doesn't exists.", False, False),
        ("something else entirely", False, True),
    ],
)
def test_check_subscription_reads_the_verdict_off_the_message(
    mock_client, message, subscribed, unrecognized
):
    """Both real answers are HTTP 200, so the status says nothing. A third string must not read as
    "not subscribed" — that would silently invert the answer if the backend wording changed."""
    mock_client.api.check_subscription.return_value = {"message": message}
    result = server.check_subscription("a1", "u1")
    assert result["subscribed"] is subscribed
    assert result["message"] == message
    assert result.get("unrecognizedResponse", False) is unrecognized


def test_check_subscription_forwards_the_asset_and_user(mock_client):
    mock_client.api.check_subscription.return_value = {"message": "success"}
    server.check_subscription("a1", "u1")
    assert mock_client.api.check_subscription.call_args.args == ("a1", "u1")


def test_the_subscription_verdict_strings_match_the_handler(mock_client):
    """Control for the parametrization above: it decides the answer by comparing to these two
    constants, so a typo in either would make every call report the wrong verdict while the test
    above still passed on its own copy of the string."""
    assert server.SUBSCRIBED_MESSAGE == "success"
    assert server.NOT_SUBSCRIBED_MESSAGE == "Subscription doesn't exists."


# --- API keys ---------------------------------------------------------------


@pytest.mark.parametrize("tool", ["get_api_key", "get_user_api_key"])
def test_the_api_key_reads_forward_the_id_and_return_the_record(mock_client, tool):
    """Unenveloped, unlike the comment and subscription reads: returned as received."""
    record = {"apiKeyId": "k1", "apiKeyName": "ci", "userId": "u1", "enabled": True}
    getattr(mock_client.api, tool).return_value = record
    assert getattr(server, tool)("k1") == record
    getattr(mock_client.api, tool).assert_called_once_with("k1")


# --- Registration and gating ------------------------------------------------


@pytest.mark.asyncio
async def test_the_comment_subscription_and_api_key_reads_are_registered():
    names = {t.name for t in await server.mcp.list_tools()}
    for expected in (
        "list_asset_comments",
        "list_asset_version_comments",
        "get_comment",
        "list_subscriptions",
        "check_subscription",
        "get_api_key",
        "get_user_api_key",
    ):
        assert expected in names


@pytest.mark.asyncio
async def test_the_new_mutating_tools_are_gated_off_by_default():
    names = {t.name for t in await server.mcp.list_tools()}
    for gated in (
        "add_comment",
        "update_comment",
        "create_subscription",
        "update_subscription",
        "create_metadata_schema",
        "update_metadata_schema",
        "delete_comment",
        "delete_subscription",
        "unsubscribe",
        "delete_metadata_schema",
    ):
        assert gated not in names


# --- Docstring contracts ----------------------------------------------------
#
# Each fragment names a behavior that reads as its opposite: a comment write that overwrites, a
# subscriber list that replaces, a "remove this user" that removes everyone. The docstring is the only
# place an agent learns any of it.


@pytest.mark.parametrize("fragment", ["UNCONDITIONAL", "REPLACES", "uuid4", "16384"])
def test_add_comment_docstring_warns_that_a_reused_id_overwrites(fragment):
    assert fragment in _docstring_of("add_comment")


def test_update_comment_docstring_states_the_creator_only_rule():
    docstring = _docstring_of("update_comment")
    assert "CREATOR" in docstring and "403" in docstring


@pytest.mark.parametrize("fragment", ["REPLACE", "not an addition", "unsubscribed"])
def test_update_subscription_docstring_states_the_replacement_semantics(fragment):
    assert fragment in _docstring_of("update_subscription")


def test_create_subscription_docstring_states_that_a_duplicate_is_an_error():
    assert "ERROR, not a no-op" in _docstring_of("create_subscription")


@pytest.mark.parametrize(
    "fragment", ["WHOLE subscription", "IGNORES", "unsubscribe()", "notification topic"]
)
def test_delete_subscription_docstring_states_it_removes_every_subscriber(fragment):
    assert fragment in _docstring_of("delete_subscription")


def test_unsubscribe_docstring_distinguishes_it_from_delete_subscription():
    docstring = _docstring_of("unsubscribe")
    assert "ONE subscriber" in docstring
    assert "delete_subscription()" in docstring


@pytest.mark.parametrize(
    "tool,fragment",
    [
        ("list_asset_comments", "CANNOT be paged"),
        ("list_asset_comments", "showDeleted"),
        ("list_asset_version_comments", "token is discarded"),
        ("get_comment", "200"),
        ("check_subscription", "BOTH cases"),
        ("check_subscription", "unrecognizedResponse"),
        ("get_api_key", "never again"),
        ("get_api_key", "vamscli api-key list"),
        ("create_metadata_schema", '{"fields": [ ... ]}'),
        ("update_metadata_schema", "REPLACES"),
        ("delete_metadata_schema", "Irreversible"),
    ],
)
def test_new_tool_docstrings_state_the_behavior_that_reads_as_its_opposite(tool, fragment):
    assert fragment in _docstring_of(tool)


def test_the_docstring_checks_are_capable_of_failing():
    """Positive control for the assertions above: `_docstring_of` resolves by AST, so a renamed tool
    raises rather than passing — but a fragment that is simply absent has to fail."""
    docstring = _docstring_of("get_api_key")
    assert docstring, "no docstring was read, so every assertion above is vacuous"
    assert "CANNOT be paged" not in docstring

"""Prints the signed-in user's VAMS token for one VAMS CLI profile, as one JSON line on stdout.

Run by the extension with the VAMS CLI's own Python (``python token_helper.py --profile <name>``) when the
Omniverse client library asks for a sign-in to the local storage adapter. The output is read from the pipe and
handed to ``omni.client.set_storage_access_token``; it is never written to a file or a log. Exit status 3 means
the profile has no usable sign-in (none, or expired and not renewable), and nothing is printed.

A Cognito sign-in that has expired, or expires within REFRESH_MARGIN_SECONDS, is renewed first with the profile's
refresh token, as ``vamscli auth refresh`` does, and the renewed tokens are saved to the profile, so the Content
window keeps working past the sign-in's one-hour token lifetime. Override tokens (VAMS API keys, IdP tokens) have
no refresh token and are used as they are. A failed renewal leaves the profile as it was.
"""
import argparse
import contextlib
import json
import sys
from datetime import datetime, timezone

REFRESH_MARGIN_SECONDS = 300


def _expires_within(expires_at, seconds):
    if not expires_at:
        return False
    now = datetime.now(timezone.utc).timestamp() + seconds
    if isinstance(expires_at, (int, float)):
        when = expires_at / 1000 if expires_at > 1e12 else expires_at
        return when <= now
    try:
        when = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp() <= now


def _expired(expires_at):
    return _expires_within(expires_at, 0)


def _renew(profile_manager, auth):
    """The profile's auth record with renewed Cognito tokens, saved; None when it cannot be renewed."""
    if auth.get("token_type") == "override" or not auth.get("refresh_token"):
        return None
    try:
        # The CLI's own messages must not reach stdout, which carries only the answer.
        with contextlib.redirect_stdout(sys.stderr):
            from vamscli.commands.auth import get_authenticator

            fresh = get_authenticator(profile_manager.load_config()).refresh_token(auth["refresh_token"])
            if not fresh.get("access_token"):
                return None
            renewed = dict(auth)
            renewed.update(fresh)
            profile_manager.save_auth_profile(renewed)
            return renewed
    except Exception as error:  # noqa: BLE001 - any failure leaves the profile as it was
        sys.stderr.write(f"sign-in renewal failed ({type(error).__name__})\n")
        return None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    from vamscli.utils.profile import ProfileManager

    profile_manager = ProfileManager(args.profile)
    auth = profile_manager.load_auth_profile() or {}
    if auth.get("access_token") and _expires_within(auth.get("expires_at"), REFRESH_MARGIN_SECONDS):
        auth = _renew(profile_manager, auth) or auth
    token = auth.get("access_token") or ""
    if not token or _expired(auth.get("expires_at")):
        return 3
    sys.stdout.write(json.dumps({"token": token, "expires_at": auth.get("expires_at")}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

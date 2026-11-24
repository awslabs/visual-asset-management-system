"""Prints the signed-in user's VAMS token for one VAMS CLI profile, as one JSON line on stdout.

Run by the extension with the VAMS CLI's own Python (``python token_helper.py --profile <name>``) when the
Omniverse client library asks for a sign-in to the local storage adapter. The output is read from the pipe and
handed to ``omni.client.set_storage_access_token``; it is never written to a file or a log. Exit status 3 means
the profile has no usable sign-in (none, or expired), and nothing is printed.
"""
import argparse
import json
import sys
from datetime import datetime, timezone


def _expired(expires_at):
    if not expires_at:
        return False
    if isinstance(expires_at, (int, float)):
        seconds = expires_at / 1000 if expires_at > 1e12 else expires_at
        return seconds <= datetime.now(timezone.utc).timestamp()
    try:
        when = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when <= datetime.now(timezone.utc)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    from vamscli.utils.profile import ProfileManager

    auth = ProfileManager(args.profile).load_auth_profile() or {}
    token = auth.get("access_token") or ""
    if not token or _expired(auth.get("expires_at")):
        return 3
    sys.stdout.write(json.dumps({"token": token, "expires_at": auth.get("expires_at")}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

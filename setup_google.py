#!/usr/bin/env python3
"""One-shot Google connect: save your OAuth client ID, then run the login flow.

    python setup_google.py <YOUR_CLIENT_ID.apps.googleusercontent.com>
    # or run with no args and paste it when prompted

Prereq (one-time, only you can do it — see Aethelark_Google_Setup.md):
create a **Desktop** OAuth client in the Google Cloud Console and enable the
Gmail, Calendar, People, and Tasks APIs. This script then writes the client ID to
config and opens Google's consent page. On success the eagle's Google tools
(Gmail brief, Calendar, Contacts, Tasks) are connected; tokens are stored in
config/google_token.json. Re-run any time to reconnect.
"""
import sys

from actions.google_auth import set_client_id, sign_in_google, SCOPES


def main() -> int:
    if len(sys.argv) > 1:
        cid = sys.argv[1].strip()
    else:
        cid = input("Paste your Google Desktop client ID: ").strip()

    if not cid:
        print("❌ No client ID provided.")
        return 1
    if not cid.endswith(".apps.googleusercontent.com"):
        print("⚠️  That doesn't look like a client ID (expected "
              "…apps.googleusercontent.com) — trying anyway.")

    if not set_client_id(cid):
        print("❌ Couldn't save the client ID to config/api_keys.json.")
        return 1
    print("✅ Saved client ID.")
    print(f"   Requesting {len(SCOPES)} scopes: identity + Gmail, Calendar, Contacts, Tasks.")
    print("   A browser window is opening for Google consent — approve it there…")

    res = sign_in_google()
    status = res.get("status")
    if status == "ok":
        who = res.get("email") or res.get("name") or "your account"
        print(f"\n🦅 Connected as {who}. Google tools are live — try “what did I miss?”.")
        return 0
    if status == "not_configured":
        print("❌ Still reads as not configured — did the save above succeed?")
        return 1
    print(f"\n❌ Sign-in {status}: {res.get('message', '')}")
    print("   Common fixes: add yourself as a Test user on the OAuth consent screen; "
          "make sure the client type is 'Desktop app'; enable the four APIs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

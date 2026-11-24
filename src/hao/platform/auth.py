"""Who is calling? (ADR 0011)

Two modes:

* ``dev`` (default, local only): no login. Reviewers type their email in the console, as in Phases 4–5.
* ``proxy`` (pilot / production): the platform sits behind an identity-aware proxy (e.g. oauth2-proxy in
  front of Kaveri's IdP). The proxy authenticates the user with SSO and forwards their email in a header.
  The platform trusts that header only when the request also carries the proxy's shared secret, so a
  caller who reaches the platform directly can't forge an identity.

The review identity comes from the asserted login, never from a form field. Admin actions (controller
run, policy reload, kill switch, sweep) need an email on ``admin_emails``.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from hao.config import Settings


def current_user(request: Request) -> str | None:
    """The asserted email in proxy mode; None in dev mode (the caller supplies a reviewer email)."""
    s: Settings = request.app.state.settings
    if s.auth_mode == "dev":
        return None
    if s.auth_mode != "proxy":
        raise HTTPException(500, f"unknown auth_mode {s.auth_mode!r}")
    if s.auth_proxy_secret and not hmac.compare_digest(request.headers.get("X-Proxy-Secret", ""),
                                                       s.auth_proxy_secret):
        raise HTTPException(401, "request did not come through the identity proxy")
    email = request.headers.get(s.auth_email_header, "").strip().lower()
    if not email:
        raise HTTPException(401, "not signed in")
    if s.auth_allowed_domain and not email.endswith("@" + s.auth_allowed_domain.lower()):
        raise HTTPException(403, f"only @{s.auth_allowed_domain} accounts can use this service")
    return email


def require_viewer(request: Request) -> str | None:
    return current_user(request)


def require_admin(request: Request) -> str:
    user = current_user(request)
    s: Settings = request.app.state.settings
    if user is None:  # dev mode
        return "dev:admin"
    if user not in {e.lower() for e in s.admin_emails}:
        raise HTTPException(403, "admin only")
    return user

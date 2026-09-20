from __future__ import annotations

import time
from typing import Any

import httpx


class GitHubAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _parse_json(response: httpx.Response) -> Any:
    """A 2xx response with a truncated or non-JSON body (proxy/CDN interstitial, network hiccup
    after headers sent) previously raised an uncaught json.JSONDecodeError from a bare
    response.json() call at each of this module's dozen call sites -- the same ambiguous-exit-code
    problem _request()'s own transport-error retry wrapping already solves for connection
    failures. json.JSONDecodeError is a ValueError subclass, so catching ValueError here covers it
    without importing the json module just for its exception type."""
    try:
        return response.json()
    except ValueError as exc:
        raise GitHubAPIError(f"invalid JSON response from GitHub: {exc}") from exc


def _expect_response(response: httpx.Response | None) -> httpx.Response:
    """_request() only returns None when called with allow_404=True; every call site below
    omits that flag, so None here means _request's contract was violated. Raising explicitly
    (rather than a bare assert, which python -O strips entirely) keeps that guarantee real."""
    if response is None:
        raise GitHubAPIError("GitHubClient._request unexpectedly returned no response")
    return response


def _unwrap(value: object, default: bool) -> bool:
    """GitHub's GET response wraps some booleans as {"enabled": bool}; PUT wants raw bool."""
    if isinstance(value, dict):
        return bool(value.get("enabled", default))
    if value is None:
        return default
    return bool(value)


def _actor_refs(raw: dict | None) -> dict | None:
    """GitHub's GET response shapes actor allow-lists (branch-protection restrictions,
    required_pull_request_reviews.dismissal_restrictions/bypass_pull_request_allowances) as
    arrays of full user/team/app objects; the PUT/PATCH request body expects arrays of bare
    login/slug strings. Sending the GET shape back verbatim 422s -- this is the transform between
    the two, shared by every endpoint with this exact GET/PUT asymmetry. `raw.get(key) or []`
    (not `raw.get(key, [])`) so an explicit `null` sub-key is treated the same as an absent one --
    `.get(key, [])` only substitutes the default when the key is missing entirely, not when it's
    present with a None value, which previously crashed with TypeError: 'NoneType' object is not
    iterable."""
    if raw is None:
        return None
    return {
        "users": [user["login"] for user in raw.get("users") or []],
        "teams": [team["slug"] for team in raw.get("teams") or []],
        "apps": [app["slug"] for app in raw.get("apps") or []],
    }


class GitHubClient:
    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        base_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ) -> None:
        """If `client` is provided, it is used as-is and `token`/`base_url`/`timeout` are ignored
        for constructing it -- the caller owns that client's auth headers and lifecycle."""
        self.owner = owner
        self.repo = repo
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GitHubClient:  # noqa: PYI034 (Self needs Python 3.11+; we target 3.10+)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        allow_404: bool = False,
        allow_422: bool = False,
        idempotent: bool = True,
    ) -> httpx.Response | None:
        """`idempotent=False` is for the one call that genuinely isn't (create_ruleset's POST,
        which creates a new resource each time) -- NOT a blanket "POST is dangerous" rule.
        set_required_signatures's enable path is also a POST, but repeating it is a no-op, so it
        (and every other call) keeps the default and retries on 5xx like any other method."""
        attempt = 0
        while True:
            try:
                response = self._client.request(method, path, json=json, params=params)
            except httpx.RequestError as exc:
                # A transient network blip (DNS hiccup, TLS reset, timeout) previously propagated
                # as a raw httpx exception -- uncaught by cli.py's except GitHubAPIError/
                # PolicyResolutionError, it crashed with Python's default exit code 1, colliding
                # with EXIT_DRIFT the same way _ConfigClickException exists to prevent for setup
                # errors. Retry it exactly like a 5xx (same idempotency rule), then surface a
                # clean GitHubAPIError instead of httpx's own exception type.
                if idempotent and attempt < self._max_retries:
                    time.sleep(self._backoff_seconds * (2**attempt))
                    attempt += 1
                    continue
                raise GitHubAPIError(f"network error on {method} {path}: {exc}") from exc

            if response.status_code == 404 and allow_404:
                return None
            if response.status_code == 422 and allow_422:
                return None
            if response.status_code < 400:
                return response

            is_rate_limited = response.status_code == 429 or (
                response.status_code == 403 and "rate limit" in response.text.lower()
            )
            # 429/secondary-rate-limit responses are always safe to retry regardless of
            # idempotency: GitHub rejects them before doing any work.
            is_server_error = response.status_code >= 500
            is_retryable = is_rate_limited or (is_server_error and idempotent)

            if is_retryable and attempt < self._max_retries:
                time.sleep(self._retry_delay(response, attempt))
                attempt += 1
                continue

            raise GitHubAPIError(
                f"GitHub API error {response.status_code} on {method} {path}: {response.text}",
                status_code=response.status_code,
            )

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        """Honors GitHub's Retry-After header (present on secondary-rate-limit responses,
        typically a short wait) instead of blindly exponential-backing-off past a window GitHub
        explicitly told us the length of. Deliberately does NOT honor X-RateLimit-Reset (the
        primary rate limit) -- that reset can be up to an hour away, and silently blocking a CLI
        invocation for that long is a product decision, not a pure reliability fix; failing after
        the existing retry budget with a clear GitHubAPIError remains the right default for a
        primary-limit exhaustion."""
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self._backoff_seconds * (2**attempt)

    def get_branch_protection(self, branch: str) -> dict | None:
        response = self._request(
            "GET", f"/repos/{self.owner}/{self.repo}/branches/{branch}/protection", allow_404=True
        )
        return _parse_json(response) if response is not None else None

    def put_branch_protection(self, branch: str, payload: dict) -> dict:
        response = self._request(
            "PUT", f"/repos/{self.owner}/{self.repo}/branches/{branch}/protection", json=payload
        )
        return _parse_json(_expect_response(response))

    def get_required_signatures(self, branch: str) -> bool:
        response = self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/branches/{branch}/protection/required_signatures",
            allow_404=True,
        )
        return response is not None and _unwrap(_parse_json(response), False)

    def set_required_signatures(self, branch: str, enabled: bool) -> None:
        method = "POST" if enabled else "DELETE"
        self._request(
            method,
            f"/repos/{self.owner}/{self.repo}/branches/{branch}/protection/required_signatures",
        )

    def get_repo(self) -> dict:
        response = self._request("GET", f"/repos/{self.owner}/{self.repo}")
        return _parse_json(_expect_response(response))

    def update_repo_settings(self, payload: dict) -> dict:
        response = self._request("PATCH", f"/repos/{self.owner}/{self.repo}", json=payload)
        return _parse_json(_expect_response(response))

    def get_vulnerability_alerts(self) -> bool:
        response = self._request(
            "GET", f"/repos/{self.owner}/{self.repo}/vulnerability-alerts", allow_404=True
        )
        return response is not None

    def enable_vulnerability_alerts(self) -> None:
        self._request("PUT", f"/repos/{self.owner}/{self.repo}/vulnerability-alerts")

    def disable_vulnerability_alerts(self) -> None:
        self._request("DELETE", f"/repos/{self.owner}/{self.repo}/vulnerability-alerts")

    def get_automated_security_fixes(self) -> bool:
        response = self._request(
            "GET", f"/repos/{self.owner}/{self.repo}/automated-security-fixes", allow_404=True
        )
        return response is not None and _unwrap(_parse_json(response), False)

    def enable_automated_security_fixes(self) -> None:
        self._request("PUT", f"/repos/{self.owner}/{self.repo}/automated-security-fixes")

    def disable_automated_security_fixes(self) -> None:
        self._request("DELETE", f"/repos/{self.owner}/{self.repo}/automated-security-fixes")

    def get_private_vulnerability_reporting(self) -> bool | None:
        """None means unavailable (404 or 422) -- not every repo is eligible."""
        response = self._request(
            "GET", f"/repos/{self.owner}/{self.repo}/private-vulnerability-reporting",
            allow_404=True, allow_422=True,
        )
        return _unwrap(_parse_json(response), False) if response is not None else None

    def enable_private_vulnerability_reporting(self) -> bool:
        """Returns False (meaning unavailable) on 422; True on success."""
        response = self._request(
            "PUT", f"/repos/{self.owner}/{self.repo}/private-vulnerability-reporting", allow_422=True
        )
        return response is not None

    def disable_private_vulnerability_reporting(self) -> bool:
        """Returns False (meaning unavailable) on 422; True on success."""
        response = self._request(
            "DELETE", f"/repos/{self.owner}/{self.repo}/private-vulnerability-reporting", allow_422=True
        )
        return response is not None

    def update_security_and_analysis(self, payload: dict) -> dict | None:
        """Returns None when GitHub Advanced Security isn't licensed on this repo (422) -- an
        expected, non-error outcome, not every repo has it. Any other failure still raises."""
        response = self._request(
            "PATCH", f"/repos/{self.owner}/{self.repo}",
            json={"security_and_analysis": payload}, allow_422=True,
        )
        return _parse_json(response) if response is not None else None

    def list_rulesets(self) -> list[dict]:
        results: list[dict] = []
        path: str | None = f"/repos/{self.owner}/{self.repo}/rulesets"
        params: dict | None = {"per_page": 100}
        while path is not None:
            response = _expect_response(self._request("GET", path, params=params))
            results.extend(_parse_json(response))
            next_link = response.links.get("next")
            path = next_link["url"] if next_link else None
            params = None  # the next-page URL already carries its own query string
        return results

    def get_ruleset(self, ruleset_id: int) -> dict:
        response = self._request("GET", f"/repos/{self.owner}/{self.repo}/rulesets/{ruleset_id}")
        return _parse_json(_expect_response(response))

    def find_ruleset_by_name(self, name: str, rulesets: list[dict] | None = None) -> dict | None:
        candidates = rulesets if rulesets is not None else self.list_rulesets()
        for summary in candidates:
            if summary["name"] == name:
                return self.get_ruleset(summary["id"])
        return None

    def create_ruleset(self, payload: dict) -> dict:
        response = self._request(
            "POST", f"/repos/{self.owner}/{self.repo}/rulesets", json=payload, idempotent=False
        )
        return _parse_json(_expect_response(response))

    def update_ruleset(self, ruleset_id: int, payload: dict) -> dict:
        response = self._request(
            "PUT", f"/repos/{self.owner}/{self.repo}/rulesets/{ruleset_id}", json=payload
        )
        return _parse_json(_expect_response(response))

    def delete_ruleset(self, ruleset_id: int) -> None:
        self._request("DELETE", f"/repos/{self.owner}/{self.repo}/rulesets/{ruleset_id}")

    def get_rules_for_branch(self, branch: str) -> list[dict]:
        """GitHub's authoritative, already-evaluated view of every active rule for this branch --
        across repository AND organization rulesets, with disabled/evaluate-enforcement rulesets
        already excluded. Each entry carries a `ruleset_id`, which apply.py cross-checks against a
        managed ruleset's own id to verify it's actually contributing here, not just correctly
        shaped on paper."""
        response = self._request("GET", f"/repos/{self.owner}/{self.repo}/rules/branches/{branch}")
        return _parse_json(_expect_response(response))

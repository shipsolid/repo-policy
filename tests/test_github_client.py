from unittest.mock import patch

import httpx
import pytest
import respx

from repo_policy.github_client import GitHubAPIError, GitHubClient, _actor_refs, _expect_response


@pytest.fixture
def client():
    c = GitHubClient(token="test-token", owner="acme", repo="widgets", backoff_seconds=0.0)
    yield c
    c.close()


def test_actor_refs_none_when_raw_is_none():
    assert _actor_refs(None) is None


def test_actor_refs_extracts_bare_identifiers():
    raw = {
        "users": [{"login": "octocat"}],
        "teams": [{"slug": "core"}],
        "apps": [{"slug": "dependabot"}],
    }
    assert _actor_refs(raw) == {"users": ["octocat"], "teams": ["core"], "apps": ["dependabot"]}


def test_actor_refs_treats_explicit_null_sub_key_the_same_as_absent():
    """GitHub's GET response for restrictions/dismissal_restrictions/bypass_pull_request_allowances
    normally omits a sub-key entirely when it's empty, which raw.get(key, []) already handles --
    but an explicit `"apps": null` (or users/teams) previously crashed with
    TypeError: 'NoneType' object is not iterable, since .get(key, []) only substitutes the
    default when the key is absent, not when it's present with a None value."""
    raw = {"users": [{"login": "octocat"}], "teams": None, "apps": None}
    assert _actor_refs(raw) == {"users": ["octocat"], "teams": [], "apps": []}


@respx.mock
def test_request_sends_auth_and_version_headers(client):
    route = respx.get("https://api.github.com/repos/acme/widgets/ping").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client._request("GET", "/repos/acme/widgets/ping")
    sent = route.calls[0].request
    assert sent.headers["Authorization"] == "Bearer test-token"
    assert sent.headers["X-GitHub-Api-Version"] == "2022-11-28"


@respx.mock
def test_request_returns_none_on_404_when_allowed(client):
    respx.get("https://api.github.com/repos/acme/widgets/missing").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    assert client._request("GET", "/repos/acme/widgets/missing", allow_404=True) is None


@respx.mock
def test_request_retries_on_500_then_succeeds(client):
    route = respx.get("https://api.github.com/repos/acme/widgets/flaky")
    route.side_effect = [
        httpx.Response(500, json={"message": "boom"}),
        httpx.Response(200, json={"ok": True}),
    ]
    response = client._request("GET", "/repos/acme/widgets/flaky")
    assert response.json() == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_request_raises_after_exhausting_retries(client):
    respx.get("https://api.github.com/repos/acme/widgets/broken").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    with pytest.raises(GitHubAPIError) as exc_info:
        client._request("GET", "/repos/acme/widgets/broken")
    assert exc_info.value.status_code == 500


@respx.mock
def test_request_does_not_retry_non_idempotent_post_on_500(client):
    route = respx.post("https://api.github.com/repos/acme/widgets/rulesets").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    with pytest.raises(GitHubAPIError):
        client._request(
            "POST", "/repos/acme/widgets/rulesets", json={"name": "repo-policy:main"}, idempotent=False
        )
    assert route.call_count == 1


@respx.mock
def test_request_retries_idempotent_post_on_500(client):
    """set_required_signatures's enable path is a POST but IS idempotent (posting it twice has
    the same end state) -- only genuinely non-idempotent calls like create_ruleset should opt out
    via idempotent=False. Every other call, including POST, retries on 500 by default."""
    route = respx.post(
        "https://api.github.com/repos/acme/widgets/branches/main/protection/required_signatures"
    )
    route.side_effect = [
        httpx.Response(500, json={"message": "boom"}),
        httpx.Response(200, json={"enabled": True}),
    ]
    response = client._request(
        "POST", "/repos/acme/widgets/branches/main/protection/required_signatures"
    )
    assert response.json() == {"enabled": True}
    assert route.call_count == 2


@respx.mock
def test_create_ruleset_does_not_retry_on_500(client):
    route = respx.post("https://api.github.com/repos/acme/widgets/rulesets").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    with pytest.raises(GitHubAPIError):
        client.create_ruleset({"name": "repo-policy:main"})
    assert route.call_count == 1


@respx.mock
def test_request_retries_transport_error_then_succeeds(client):
    """A transient network blip (DNS hiccup, TLS reset, timeout) must not crash with a raw
    httpx exception -- it should retry like any other transient failure and, if every attempt
    fails, surface a clean GitHubAPIError instead of propagating httpx's own exception type."""
    route = respx.get("https://api.github.com/repos/acme/widgets/ping")
    route.side_effect = [
        httpx.ConnectError("connection refused"),
        httpx.Response(200, json={"ok": True}),
    ]
    response = client._request("GET", "/repos/acme/widgets/ping")
    assert response.json() == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_request_raises_github_api_error_after_exhausting_retries_on_transport_error(client):
    route = respx.get("https://api.github.com/repos/acme/widgets/ping").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    with pytest.raises(GitHubAPIError):
        client._request("GET", "/repos/acme/widgets/ping")
    assert route.call_count == client._max_retries + 1


@respx.mock
def test_request_does_not_retry_transport_error_for_non_idempotent_call(client):
    route = respx.post("https://api.github.com/repos/acme/widgets/rulesets").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(GitHubAPIError):
        client._request(
            "POST", "/repos/acme/widgets/rulesets", json={"name": "repo-policy:main"}, idempotent=False
        )
    assert route.call_count == 1


@respx.mock
def test_request_still_retries_post_on_429(client):
    route = respx.post("https://api.github.com/repos/acme/widgets/rulesets")
    route.side_effect = [
        httpx.Response(429, json={"message": "You have exceeded a secondary rate limit"}),
        httpx.Response(201, json={"id": 1, "name": "repo-policy:main"}),
    ]
    response = client._request("POST", "/repos/acme/widgets/rulesets", json={"name": "repo-policy:main"})
    assert response.json()["id"] == 1
    assert route.call_count == 2


@respx.mock
def test_request_raises_immediately_on_non_retryable_4xx(client):
    route = respx.get("https://api.github.com/repos/acme/widgets/forbidden").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )
    with pytest.raises(GitHubAPIError) as exc_info:
        client._request("GET", "/repos/acme/widgets/forbidden")
    assert exc_info.value.status_code == 401
    assert route.call_count == 1


@respx.mock
def test_request_honors_retry_after_header_over_exponential_backoff(client):
    route = respx.get("https://api.github.com/repos/acme/widgets/rate-limited")
    route.side_effect = [
        httpx.Response(
            429, json={"message": "secondary rate limit"}, headers={"Retry-After": "13"}
        ),
        httpx.Response(200, json={"ok": True}),
    ]
    with patch("repo_policy.github_client.time.sleep") as mock_sleep:
        response = client._request("GET", "/repos/acme/widgets/rate-limited")
    assert response.json() == {"ok": True}
    mock_sleep.assert_called_once_with(13.0)


@respx.mock
def test_request_falls_back_to_exponential_backoff_without_retry_after_header(client):
    route = respx.get("https://api.github.com/repos/acme/widgets/rate-limited")
    route.side_effect = [
        httpx.Response(429, json={"message": "secondary rate limit"}),
        httpx.Response(200, json={"ok": True}),
    ]
    with patch("repo_policy.github_client.time.sleep") as mock_sleep:
        response = client._request("GET", "/repos/acme/widgets/rate-limited")
    assert response.json() == {"ok": True}
    mock_sleep.assert_called_once_with(0.0)  # client fixture uses backoff_seconds=0.0


@respx.mock
def test_request_retries_on_429_then_succeeds(client):
    route = respx.get("https://api.github.com/repos/acme/widgets/rate-limited")
    route.side_effect = [
        httpx.Response(429, json={"message": "You have exceeded a secondary rate limit"}),
        httpx.Response(200, json={"ok": True}),
    ]
    response = client._request("GET", "/repos/acme/widgets/rate-limited")
    assert response.json() == {"ok": True}
    assert route.call_count == 2


def test_close_does_not_close_an_injected_client():
    injected = httpx.Client()
    injected_owner = GitHubClient(token="t", owner="acme", repo="widgets", client=injected)
    injected_owner.close()
    assert injected.is_closed is False
    injected.close()


def test_close_closes_a_client_it_created_itself():
    self_owned = GitHubClient(token="t", owner="acme", repo="widgets")
    self_owned.close()
    assert self_owned._client.is_closed is True


def test_expect_response_raises_explicit_error_on_none():
    with pytest.raises(GitHubAPIError, match="unexpectedly returned no response"):
        _expect_response(None)


def test_expect_response_returns_response_unchanged():
    response = httpx.Response(200, json={"ok": True})
    assert _expect_response(response) is response


@respx.mock
def test_get_branch_protection_returns_none_when_unprotected(client):
    respx.get("https://api.github.com/repos/acme/widgets/branches/main/protection").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    assert client.get_branch_protection("main") is None


@respx.mock
def test_get_branch_protection_returns_payload(client):
    respx.get("https://api.github.com/repos/acme/widgets/branches/main/protection").mock(
        return_value=httpx.Response(200, json={"enforce_admins": {"enabled": False}})
    )
    assert client.get_branch_protection("main") == {"enforce_admins": {"enabled": False}}


@respx.mock
def test_get_branch_protection_raises_clean_error_on_non_json_response(client):
    """A 2xx response with a truncated/non-JSON body (proxy interstitial, network hiccup after
    headers sent) previously raised an uncaught json.JSONDecodeError instead of a clean
    GitHubAPIError -- the same ambiguous-exit-code problem the transport-error retry wrapping in
    _request() already solves for connection failures."""
    respx.get("https://api.github.com/repos/acme/widgets/branches/main/protection").mock(
        return_value=httpx.Response(200, content=b"<html>not json</html>")
    )
    with pytest.raises(GitHubAPIError):
        client.get_branch_protection("main")


@respx.mock
def test_put_branch_protection_sends_payload(client):
    route = respx.put("https://api.github.com/repos/acme/widgets/branches/main/protection").mock(
        return_value=httpx.Response(200, json={"enforce_admins": {"enabled": False}})
    )
    result = client.put_branch_protection("main", {"enforce_admins": False})
    assert result == {"enforce_admins": {"enabled": False}}
    assert route.calls[0].request.content == b'{"enforce_admins":false}'


@respx.mock
def test_get_required_signatures_false_when_never_enabled(client):
    respx.get(
        "https://api.github.com/repos/acme/widgets/branches/main/protection/required_signatures"
    ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    assert client.get_required_signatures("main") is False


@respx.mock
def test_get_required_signatures_true_when_enabled(client):
    respx.get(
        "https://api.github.com/repos/acme/widgets/branches/main/protection/required_signatures"
    ).mock(return_value=httpx.Response(200, json={"enabled": True}))
    assert client.get_required_signatures("main") is True


@respx.mock
def test_set_required_signatures_posts_to_enable(client):
    route = respx.post(
        "https://api.github.com/repos/acme/widgets/branches/main/protection/required_signatures"
    ).mock(return_value=httpx.Response(200, json={"enabled": True}))
    client.set_required_signatures("main", True)
    assert route.called


@respx.mock
def test_set_required_signatures_deletes_to_disable(client):
    route = respx.delete(
        "https://api.github.com/repos/acme/widgets/branches/main/protection/required_signatures"
    ).mock(return_value=httpx.Response(204))
    client.set_required_signatures("main", False)
    assert route.called


@respx.mock
def test_list_rulesets_returns_summaries(client):
    respx.get("https://api.github.com/repos/acme/widgets/rulesets").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "repo-policy:main"}])
    )
    assert client.list_rulesets() == [{"id": 1, "name": "repo-policy:main"}]


@respx.mock
def test_list_rulesets_follows_pagination_link_header(client):
    next_url = "https://api.github.com/repos/acme/widgets/rulesets?per_page=100&page=2"
    route = respx.get("https://api.github.com/repos/acme/widgets/rulesets")
    route.side_effect = [
        httpx.Response(
            200,
            json=[{"id": 1, "name": "page-one"}],
            headers={"Link": f'<{next_url}>; rel="next"'},
        ),
        httpx.Response(200, json=[{"id": 2, "name": "page-two"}]),
    ]
    assert client.list_rulesets() == [{"id": 1, "name": "page-one"}, {"id": 2, "name": "page-two"}]
    assert route.call_count == 2


@respx.mock
def test_get_ruleset_returns_full_detail(client):
    respx.get("https://api.github.com/repos/acme/widgets/rulesets/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "repo-policy:main", "rules": []})
    )
    assert client.get_ruleset(1) == {"id": 1, "name": "repo-policy:main", "rules": []}


@respx.mock
def test_find_ruleset_by_name_returns_full_detail_when_present(client):
    respx.get("https://api.github.com/repos/acme/widgets/rulesets").mock(
        return_value=httpx.Response(
            200, json=[{"id": 1, "name": "repo-policy:main"}, {"id": 2, "name": "other"}]
        )
    )
    respx.get("https://api.github.com/repos/acme/widgets/rulesets/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "repo-policy:main", "rules": []})
    )
    result = client.find_ruleset_by_name("repo-policy:main")
    assert result == {"id": 1, "name": "repo-policy:main", "rules": []}


@respx.mock
def test_find_ruleset_by_name_returns_none_when_absent(client):
    respx.get("https://api.github.com/repos/acme/widgets/rulesets").mock(
        return_value=httpx.Response(200, json=[{"id": 2, "name": "other"}])
    )
    assert client.find_ruleset_by_name("repo-policy:main") is None


@respx.mock
def test_find_ruleset_by_name_reuses_prefetched_list_without_a_new_get(client):
    list_route = respx.get("https://api.github.com/repos/acme/widgets/rulesets")
    respx.get("https://api.github.com/repos/acme/widgets/rulesets/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "repo-policy:main", "rules": []})
    )
    prefetched = [{"id": 1, "name": "repo-policy:main"}, {"id": 2, "name": "other"}]
    result = client.find_ruleset_by_name("repo-policy:main", rulesets=prefetched)
    assert result == {"id": 1, "name": "repo-policy:main", "rules": []}
    assert not list_route.called


@respx.mock
def test_create_ruleset_posts_payload(client):
    route = respx.post("https://api.github.com/repos/acme/widgets/rulesets").mock(
        return_value=httpx.Response(201, json={"id": 5, "name": "repo-policy:main"})
    )
    result = client.create_ruleset({"name": "repo-policy:main"})
    assert result["id"] == 5
    assert route.called


@respx.mock
def test_update_ruleset_puts_payload(client):
    route = respx.put("https://api.github.com/repos/acme/widgets/rulesets/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "name": "repo-policy:main"})
    )
    client.update_ruleset(5, {"name": "repo-policy:main"})
    assert route.called


@respx.mock
def test_get_rules_for_branch_returns_empty_list_when_nothing_applies(client):
    respx.get("https://api.github.com/repos/acme/widgets/rules/branches/main").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert client.get_rules_for_branch("main") == []


@respx.mock
def test_get_rules_for_branch_returns_repository_and_organization_rules(client):
    payload = [
        {
            "type": "pull_request",
            "ruleset_source_type": "Repository",
            "ruleset_source": "acme/widgets",
            "ruleset_id": 7,
        },
        {
            "type": "deletion",
            "ruleset_source_type": "Organization",
            "ruleset_source": "acme",
            "ruleset_id": 42,
        },
    ]
    respx.get("https://api.github.com/repos/acme/widgets/rules/branches/main").mock(
        return_value=httpx.Response(200, json=payload)
    )
    assert client.get_rules_for_branch("main") == payload


@respx.mock
def test_get_rules_for_branch_raises_clean_error_on_non_json_response(client):
    respx.get("https://api.github.com/repos/acme/widgets/rules/branches/main").mock(
        return_value=httpx.Response(200, content=b"<html>not json</html>")
    )
    with pytest.raises(GitHubAPIError):
        client.get_rules_for_branch("main")


@respx.mock
def test_delete_ruleset_calls_delete(client):
    route = respx.delete("https://api.github.com/repos/acme/widgets/rulesets/5").mock(
        return_value=httpx.Response(204)
    )
    client.delete_ruleset(5)
    assert route.called


@respx.mock
def test_get_repo(client):
    respx.get("https://api.github.com/repos/acme/widgets").mock(
        return_value=httpx.Response(200, json={"default_branch": "main", "delete_branch_on_merge": False})
    )
    data = client.get_repo()
    assert data["default_branch"] == "main"


@respx.mock
def test_get_repo_raises_clean_error_on_non_json_response(client):
    respx.get("https://api.github.com/repos/acme/widgets").mock(
        return_value=httpx.Response(200, content=b"<html>not json</html>")
    )
    with pytest.raises(GitHubAPIError):
        client.get_repo()


@respx.mock
def test_list_rulesets_raises_clean_error_on_non_json_response(client):
    respx.get("https://api.github.com/repos/acme/widgets/rulesets").mock(
        return_value=httpx.Response(200, content=b"<html>not json</html>")
    )
    with pytest.raises(GitHubAPIError):
        client.list_rulesets()


@respx.mock
def test_update_repo_settings(client):
    route = respx.patch("https://api.github.com/repos/acme/widgets").mock(
        return_value=httpx.Response(200, json={"delete_branch_on_merge": True})
    )
    data = client.update_repo_settings({"delete_branch_on_merge": True})
    assert data["delete_branch_on_merge"] is True
    assert route.calls[0].request.content == b'{"delete_branch_on_merge":true}'


@respx.mock
def test_get_vulnerability_alerts_enabled(client):
    respx.get("https://api.github.com/repos/acme/widgets/vulnerability-alerts").mock(
        return_value=httpx.Response(204)
    )
    assert client.get_vulnerability_alerts() is True


@respx.mock
def test_get_vulnerability_alerts_disabled(client):
    respx.get("https://api.github.com/repos/acme/widgets/vulnerability-alerts").mock(
        return_value=httpx.Response(404)
    )
    assert client.get_vulnerability_alerts() is False


@respx.mock
def test_enable_vulnerability_alerts(client):
    route = respx.put("https://api.github.com/repos/acme/widgets/vulnerability-alerts").mock(
        return_value=httpx.Response(204)
    )
    client.enable_vulnerability_alerts()
    assert route.called


@respx.mock
def test_disable_vulnerability_alerts(client):
    route = respx.delete("https://api.github.com/repos/acme/widgets/vulnerability-alerts").mock(
        return_value=httpx.Response(204)
    )
    client.disable_vulnerability_alerts()
    assert route.called


@respx.mock
def test_get_automated_security_fixes_enabled(client):
    respx.get("https://api.github.com/repos/acme/widgets/automated-security-fixes").mock(
        return_value=httpx.Response(200, json={"enabled": True})
    )
    assert client.get_automated_security_fixes() is True


@respx.mock
def test_get_automated_security_fixes_disabled_via_404(client):
    respx.get("https://api.github.com/repos/acme/widgets/automated-security-fixes").mock(
        return_value=httpx.Response(404)
    )
    assert client.get_automated_security_fixes() is False


@respx.mock
def test_enable_automated_security_fixes(client):
    route = respx.put("https://api.github.com/repos/acme/widgets/automated-security-fixes").mock(
        return_value=httpx.Response(204)
    )
    client.enable_automated_security_fixes()
    assert route.called


@respx.mock
def test_disable_automated_security_fixes(client):
    route = respx.delete("https://api.github.com/repos/acme/widgets/automated-security-fixes").mock(
        return_value=httpx.Response(204)
    )
    client.disable_automated_security_fixes()
    assert route.called


@respx.mock
def test_request_allow_422_returns_none_on_422(client):
    respx.get("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(422, json={"message": "not eligible"})
    )
    response = client._request(
        "GET", "/repos/acme/widgets/private-vulnerability-reporting", allow_422=True
    )
    assert response is None


@respx.mock
def test_request_without_allow_422_raises_on_422(client):
    respx.get("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(422, json={"message": "not eligible"})
    )
    with pytest.raises(GitHubAPIError):
        client._request("GET", "/repos/acme/widgets/private-vulnerability-reporting")


@respx.mock
def test_get_private_vulnerability_reporting_enabled(client):
    respx.get("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(200, json={"enabled": True})
    )
    assert client.get_private_vulnerability_reporting() is True


@respx.mock
def test_get_private_vulnerability_reporting_unavailable_via_404(client):
    respx.get("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(404)
    )
    assert client.get_private_vulnerability_reporting() is None


@respx.mock
def test_get_private_vulnerability_reporting_defaults_false_when_enabled_key_missing(client):
    respx.get("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(200, json={})
    )
    assert client.get_private_vulnerability_reporting() is False


@respx.mock
def test_get_private_vulnerability_reporting_unavailable_via_422(client):
    respx.get("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(422, json={"message": "not eligible"})
    )
    assert client.get_private_vulnerability_reporting() is None


@respx.mock
def test_enable_private_vulnerability_reporting_succeeds(client):
    respx.put("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(204)
    )
    assert client.enable_private_vulnerability_reporting() is True


@respx.mock
def test_enable_private_vulnerability_reporting_unavailable_via_422(client):
    respx.put("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(422, json={"message": "not eligible"})
    )
    assert client.enable_private_vulnerability_reporting() is False


@respx.mock
def test_disable_private_vulnerability_reporting_succeeds(client):
    respx.delete("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(204)
    )
    assert client.disable_private_vulnerability_reporting() is True


@respx.mock
def test_disable_private_vulnerability_reporting_unavailable_via_422(client):
    respx.delete("https://api.github.com/repos/acme/widgets/private-vulnerability-reporting").mock(
        return_value=httpx.Response(422, json={"message": "not eligible"})
    )
    assert client.disable_private_vulnerability_reporting() is False


@respx.mock
def test_update_security_and_analysis_succeeds(client):
    respx.patch("https://api.github.com/repos/acme/widgets").mock(
        return_value=httpx.Response(200, json={"security_and_analysis": {"secret_scanning": {"status": "enabled"}}})
    )
    data = client.update_security_and_analysis({"secret_scanning": {"status": "enabled"}})
    assert data is not None


@respx.mock
def test_update_security_and_analysis_unavailable_via_422(client):
    respx.patch("https://api.github.com/repos/acme/widgets").mock(
        return_value=httpx.Response(422, json={"message": "GHAS not enabled"})
    )
    assert client.update_security_and_analysis({"secret_scanning": {"status": "enabled"}}) is None

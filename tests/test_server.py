import json

import pytest
from fastmcp.exceptions import ToolError

from repo_finder.models import RateLimitError
from repo_finder.server import _build_search_query, _format_error, _parse_url


def test_parse_url_slug():
    owner, repo = _parse_url("psf/requests")
    assert owner == "psf"
    assert repo == "requests"


def test_parse_url_full_github():
    owner, repo = _parse_url("https://github.com/psf/requests")
    assert owner == "psf"
    assert repo == "requests"


def test_parse_url_with_trailing_slash():
    owner, repo = _parse_url("psf/requests/")
    assert owner == "psf"
    assert repo == "requests"


def test_parse_url_invalid():
    with pytest.raises(ToolError, match="Invalid repo reference"):
        _parse_url("not-a-repo")


def test_parse_url_empty():
    with pytest.raises(ToolError):
        _parse_url("")


def test_parse_url_single_word():
    with pytest.raises(ToolError):
        _parse_url("justone")


def test_build_search_query_basic():
    query = _build_search_query("fastapi backend", None, None, None, None)
    assert "fastapi backend" in query
    assert "archived:false" in query


def test_build_search_query_with_language():
    query = _build_search_query("task", "Python", None, None, None)
    assert "language:Python" in query


def test_build_search_query_with_min_stars():
    query = _build_search_query("task", None, 100, None, None)
    assert "stars:>=100" in query


def test_build_search_query_with_license():
    query = _build_search_query("task", None, None, None, "mit")
    assert "license:mit" in query


def test_build_search_query_with_max_age_days():
    query = _build_search_query("task", None, None, 30, None)
    assert "pushed:>=" in query


def test_build_search_query_all_filters():
    query = _build_search_query("api", "TypeScript", 50, 90, "apache-2.0")
    assert "api" in query
    assert "language:TypeScript" in query
    assert "stars:>=50" in query
    assert "license:apache-2.0" in query
    assert "pushed:>=" in query
    assert "archived:false" in query


def test_format_error_rate_limit():
    exc = RateLimitError("Rate limited", retry_after=30)
    result = _format_error(exc)
    parsed = json.loads(result)
    assert parsed["error"] == "Rate limited"
    assert parsed["recoverable"] is True
    assert parsed["retry_after"] == 30


def test_format_error_generic():
    exc = RuntimeError("Something went wrong")
    result = _format_error(exc)
    parsed = json.loads(result)
    assert parsed["error"] == "Something went wrong"
    assert parsed["recoverable"] is False
    assert parsed["retry_after"] is None

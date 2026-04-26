import os

import pytest


@pytest.fixture
def requires_github_token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        pytest.skip("GITHUB_TOKEN not set")
    return token

"""Shared pytest fixtures for the llm-budget-gateway test suite."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"

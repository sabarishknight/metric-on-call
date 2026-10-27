"""Shared fixtures. The full dataset is built once per test session (a few seconds)."""

from __future__ import annotations

import pytest

from generator.build import build_tables


@pytest.fixture(scope="session")
def built():
    tables, truth = build_tables()
    return tables, truth


@pytest.fixture(scope="session")
def tables(built):
    return built[0]


@pytest.fixture(scope="session")
def truth(built):
    return built[1]


@pytest.fixture(scope="session")
def wh(tables):
    from tools.data import Warehouse

    return Warehouse(tables)


@pytest.fixture(scope="session")
def by_id(truth):
    return {i["id"]: i for i in truth["incidents"]}

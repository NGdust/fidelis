"""Shared fixtures and target models for the fidelis tests."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel

import fidelis as fp


class User(BaseModel):
    """Target model for most of the tests."""

    email: str
    full_name: str
    signup_date: date


class Product(BaseModel):
    sku: str
    price: float
    in_stock: bool


# Mappings an LLM would "agree on" for a source with E-mail/Name/Date fields.
USER_MAPPINGS = [
    {"source": "E-mail", "target": "email", "transform": "strip_lower", "confidence": 0.98},
    {"source": "Name", "target": "full_name", "transform": "strip", "confidence": 0.91},
    {"source": "Date", "target": "signup_date", "transform": "parse_date:%d.%m.%Y", "confidence": 0.62},
]


@pytest.fixture
def user_model():
    return User


@pytest.fixture
def product_model():
    return Product


@pytest.fixture
def spec_dir(tmp_path):
    """Isolated spec directory for each test."""

    d = tmp_path / "specs"
    d.mkdir()
    return d


@pytest.fixture
def fake_user_provider():
    """FakeProvider that returns mappings for the User model."""

    def _make(**overrides):
        return fp.FakeProvider(mappings=overrides.get("mappings", USER_MAPPINGS))

    return _make


@pytest.fixture
def user_records():
    return [
        {"E-mail": " A@B.COM ", "Name": " Alice ", "Date": "01.02.2026"},
        {"E-mail": "bob@x.com", "Name": "Bob", "Date": "15.03.2026"},
    ]


@pytest.fixture
def user_csv_text():
    return "E-mail;Name;Date\n A@B.COM ; Alice ;01.02.2026\nbob@x.com;Bob;15.03.2026\n"

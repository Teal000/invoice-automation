"""Configuration.

Two layers on purpose:

* :class:`Settings` reads the environment (database credentials, tolerances).
  It is the only place that touches the environment.
* :class:`RuleConfig` is a plain frozen dataclass holding just the numbers the
  validation rules need. Rules take a ``RuleConfig``, never ``Settings``, so
  every rule can be unit tested without an environment or a database.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .money import ALLOWED_ROUNDING_MODES

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RuleConfig:
    """Numbers and policy the deterministic rules depend on.

    The tax rate is a fictional test configuration for synthetic documents. It
    is not tax guidance and no rule in this project asserts tax compliance.
    """

    currency: str = "TND"
    money_decimal_places: int = 3
    money_rounding: str = decimal.ROUND_HALF_UP
    arithmetic_tolerance: Decimal = Decimal("0.001")
    po_absolute_tolerance: Decimal = Decimal("1.000")
    po_relative_tolerance: Decimal = Decimal("0.02")
    configured_tax_rate: Decimal = Decimal("0.19")

    @property
    def supported_currencies(self) -> tuple[str, ...]:
        """Only one currency is configured in this version, by design."""
        return (self.currency,)


class Settings(BaseSettings):
    """Environment-backed settings. Prefix every variable with ``INVAUT_``."""

    model_config = SettingsConfigDict(
        env_prefix="INVAUT_",
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "invoice_automation"
    db_user: str = "invoice_app"
    db_password: str = ""
    test_db_name: str = "invoice_automation_test"

    currency: str = "TND"
    money_decimal_places: int = 3
    money_rounding: str = "ROUND_HALF_UP"
    arithmetic_tolerance: Decimal = Decimal("0.001")
    po_absolute_tolerance: Decimal = Decimal("1.000")
    po_relative_tolerance: Decimal = Decimal("0.02")
    configured_tax_rate: Decimal = Decimal("0.19")

    storage_root: Path = Path("data/storage")

    #: Single internal service credential for the API. Not a user auth
    #: system: it exists so that processing and review actions are not open
    #: to anything that can reach the port, and so n8n can authenticate to
    #: the API. The service refuses to start when it is empty, which is
    #: deliberate: an API that quietly runs without a credential is worse
    #: than one that will not start.
    api_key: str = ""

    @field_validator("money_rounding")
    @classmethod
    def _check_rounding(cls, value: str) -> str:
        if value not in ALLOWED_ROUNDING_MODES:
            raise ValueError(
                f"INVAUT_MONEY_ROUNDING must be one of {ALLOWED_ROUNDING_MODES}"
            )
        return value

    def dsn(self, database: str | None = None) -> str:
        """libpq connection string. ``database`` overrides the configured name."""
        name = database or self.db_name
        parts = [
            f"host={self.db_host}",
            f"port={self.db_port}",
            f"dbname={name}",
            f"user={self.db_user}",
        ]
        if self.db_password:
            parts.append(f"password={self.db_password}")
        return " ".join(parts)

    def rule_config(self) -> RuleConfig:
        return RuleConfig(
            currency=self.currency,
            money_decimal_places=self.money_decimal_places,
            money_rounding=self.money_rounding,
            arithmetic_tolerance=self.arithmetic_tolerance,
            po_absolute_tolerance=self.po_absolute_tolerance,
            po_relative_tolerance=self.po_relative_tolerance,
            configured_tax_rate=self.configured_tax_rate,
        )

    def absolute_storage_root(self) -> Path:
        root = self.storage_root
        return root if root.is_absolute() else PROJECT_ROOT / root


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

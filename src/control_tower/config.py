from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://control_tower:control_tower@localhost:5433/control_tower"
)
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True)
class Settings:
    database_url: str
    document_dir: Path
    supervisor_model: str
    specialist_model: str
    shipment_model: str
    inventory_model: str
    supplier_risk_model: str
    contracts_model: str
    embedding_model: str
    embedding_dimensions: int
    risk_mcp_enabled: bool
    risk_mcp_url: str
    risk_mcp_connect_timeout_seconds: float
    host: str
    port: int

    @property
    def openai_configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    @property
    def agent_models(self) -> dict[str, str]:
        return {
            "supervisor": self.supervisor_model,
            "shipments": self.shipment_model,
            "inventory": self.inventory_model,
            "supplier_risk": self.supplier_risk_model,
            "contracts_compliance": self.contracts_model,
        }


def get_settings() -> Settings:
    load_dotenv(override=False)
    supervisor_model = _model_setting(
        "CONTROL_TOWER_SUPERVISOR_MODEL",
        default="gpt-5.6-terra",
    )
    specialist_model = _model_setting(
        "CONTROL_TOWER_SPECIALIST_MODEL",
        default="gpt-5.6-luna",
    )
    return Settings(
        database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        document_dir=Path(os.getenv("CONTROL_TOWER_DOCUMENT_DIR", "synthetic_documents")),
        supervisor_model=supervisor_model,
        specialist_model=specialist_model,
        shipment_model=_model_setting(
            "CONTROL_TOWER_SHIPMENT_MODEL",
            default=specialist_model,
        ),
        inventory_model=_model_setting(
            "CONTROL_TOWER_INVENTORY_MODEL",
            default=specialist_model,
        ),
        supplier_risk_model=_model_setting(
            "CONTROL_TOWER_SUPPLIER_RISK_MODEL",
            default=specialist_model,
        ),
        contracts_model=_model_setting(
            "CONTROL_TOWER_CONTRACTS_MODEL",
            default=specialist_model,
        ),
        embedding_model=_model_setting(
            "CONTROL_TOWER_EMBEDDING_MODEL",
            default="text-embedding-3-small",
        ),
        embedding_dimensions=int(os.getenv("CONTROL_TOWER_EMBEDDING_DIMENSIONS", "384")),
        risk_mcp_enabled=_env_bool("CONTROL_TOWER_RISK_MCP_ENABLED", default=True),
        risk_mcp_url=os.getenv(
            "CONTROL_TOWER_RISK_MCP_URL",
            "http://127.0.0.1:8010/mcp",
        ),
        risk_mcp_connect_timeout_seconds=float(
            os.getenv("CONTROL_TOWER_RISK_MCP_CONNECT_TIMEOUT_SECONDS", "2")
        ),
        host=os.getenv("CONTROL_TOWER_HOST", "127.0.0.1"),
        port=int(os.getenv("CONTROL_TOWER_PORT", "8000")),
    )


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _model_setting(name: str, *, default: str) -> str:
    model = os.getenv(name, "").strip() or default
    if not MODEL_NAME_PATTERN.fullmatch(model):
        raise ValueError(
            f"{name} must be a model identifier containing only letters, numbers, "
            "periods, underscores, colons, slashes, or hyphens."
        )
    return model

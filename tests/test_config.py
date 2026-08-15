from __future__ import annotations

import pytest

from control_tower.config import get_settings

PER_AGENT_MODEL_VARIABLES = {
    "CONTROL_TOWER_SHIPMENT_MODEL": "shipments",
    "CONTROL_TOWER_INVENTORY_MODEL": "inventory",
    "CONTROL_TOWER_SUPPLIER_RISK_MODEL": "supplier_risk",
    "CONTROL_TOWER_CONTRACTS_MODEL": "contracts_compliance",
}


def test_per_agent_models_fall_back_to_shared_specialist_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTROL_TOWER_SPECIALIST_MODEL", "shared-specialist")
    for variable in PER_AGENT_MODEL_VARIABLES:
        monkeypatch.setenv(variable, "")

    settings = get_settings()

    assert settings.agent_models == {
        "supervisor": settings.supervisor_model,
        "shipments": "shared-specialist",
        "inventory": "shared-specialist",
        "supplier_risk": "shared-specialist",
        "contracts_compliance": "shared-specialist",
    }


def test_per_agent_models_accept_server_side_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTROL_TOWER_SUPERVISOR_MODEL", "supervisor-model")
    for index, (variable, agent) in enumerate(PER_AGENT_MODEL_VARIABLES.items(), start=1):
        monkeypatch.setenv(variable, f"{agent}-model-{index}")

    settings = get_settings()

    assert settings.agent_models == {
        "supervisor": "supervisor-model",
        "shipments": "shipments-model-1",
        "inventory": "inventory-model-2",
        "supplier_risk": "supplier_risk-model-3",
        "contracts_compliance": "contracts_compliance-model-4",
    }


def test_malformed_model_name_fails_with_setting_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTROL_TOWER_SHIPMENT_MODEL", "not a model")

    with pytest.raises(ValueError, match="CONTROL_TOWER_SHIPMENT_MODEL"):
        get_settings()

"""Tests for CitrineConfigFlow and CitrineOptionsFlow schema rendering."""

from unittest.mock import MagicMock
import pytest
from custom_components.citrine_ha.config_flow import CitrineConfigFlow, CitrineOptionsFlow
from custom_components.citrine_ha.const import (
    CONF_BASE_URL,
    CONF_CONTROLLER_MODE,
    CONF_EMS_INTENT_MODE,
    CONF_MAIN_FUSE_LIMIT_W,
    CONF_NAME,
    CONF_TENANT_ID,
    DEFAULT_CONTROLLER_MODE,
    DEFAULT_MAIN_FUSE_LIMIT_W,
    DEFAULT_NAME,
    DEFAULT_TENANT_ID,
)


@pytest.mark.asyncio
async def test_user_schema_rendering_with_empty_and_custom_values():
    """Verify _user_schema constructs clean schemas without TypeError."""
    # Test with empty dict
    schema_empty = CitrineConfigFlow._user_schema({})
    assert schema_empty is not None
    assert CONF_NAME in [k.schema for k in schema_empty.schema.keys()]

    # Test with existing values including None
    schema_with_vals = CitrineConfigFlow._user_schema({
        CONF_NAME: "CustomCitrine",
        CONF_BASE_URL: "http://citrine.local:8080",
        CONF_TENANT_ID: "2",
        "auth_token": None,
    })
    assert schema_with_vals is not None


@pytest.mark.asyncio
async def test_load_control_schema_rendering():
    """Verify _load_control_schema constructs without crashing."""
    schema = CitrineConfigFlow._load_control_schema({
        CONF_CONTROLLER_MODE: "Solar Only",
        CONF_EMS_INTENT_MODE: "ExternalLimits",
        CONF_MAIN_FUSE_LIMIT_W: "15000",
        "grid_power_sensor": "sensor.grid_power",
        "doe_import_limit_sensor": None,
    })
    assert schema is not None
    assert CONF_EMS_INTENT_MODE in [key.schema for key in schema.schema.keys()]


@pytest.mark.asyncio
async def test_options_flow_schema_rendering_with_mixed_entry_data():
    """Verify CitrineOptionsFlow._options_schema constructs safely with missing/None options."""
    entry_mock = MagicMock()
    entry_mock.data = {
        CONF_NAME: DEFAULT_NAME,
        CONF_BASE_URL: "http://localhost:8080",
        CONF_TENANT_ID: DEFAULT_TENANT_ID,
    }
    entry_mock.options = {
        CONF_CONTROLLER_MODE: DEFAULT_CONTROLLER_MODE,
        CONF_MAIN_FUSE_LIMIT_W: None,
        "grid_phase_a_current_sensor": "",
        "max_phase_unbalance_a": "25.0",
    }

    flow = CitrineOptionsFlow(entry_mock)
    current_values = {**entry_mock.data, **entry_mock.options}
    schema = flow._options_schema(current_values)
    assert schema is not None

    # Test async_step_init form rendering
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    result = await flow.async_step_init(None)
    assert result["type"] == "form"

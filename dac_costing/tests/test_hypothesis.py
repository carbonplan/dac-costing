import pytest
from hypothesis import given
from hypothesis.strategies import fixed_dictionaries, floats

from dac_costing.model import BatterySection, DacModel, DacSection, EnergySection, NgThermalSection

strats = {
    "Base Energy Requierement [MW]": floats(
        min_value=0, max_value=1e12, allow_infinity=False, allow_nan=False
    ),
    "Required Thermal Energy [GJ/tCO2]": floats(
        min_value=0, max_value=100, allow_infinity=False, allow_nan=False
    ),
    "Total Capex [$]": floats(min_value=1, max_value=1e12, allow_infinity=False, allow_nan=False),
}


@given(fixed_dictionaries(strats))
def test_ng_model(hparams):
    electric = EnergySection("NGCC w/ CCS", battery=None, **hparams)

    thermal = NgThermalSection("Advanced NGCC", battery=None, **hparams)

    dac = DacSection(**hparams)

    dac_all = DacModel(electric, thermal, dac, **hparams)

    assert len(dac_all.compute().series)
    assert dac_all.values["Total Cost [$/tCO2]"] > 0


@pytest.mark.parametrize("tech", ["Solar", "Wind"])
@pytest.mark.parametrize("use_bat", [True, False])
@given(fixed_dictionaries(strats))
def test_renewable_models(tech, use_bat, hparams):
    if use_bat:
        ebattery = BatterySection(**hparams)
        tbattery = BatterySection(**hparams)
    else:
        ebattery = None
        tbattery = None

    electric = EnergySection(tech, battery=ebattery, **hparams)
    thermal = NgThermalSection(tech, battery=tbattery, **hparams)
    dac = DacSection(**hparams)
    dac_all = DacModel(electric, thermal, dac, **hparams)

    assert len(dac_all.compute().series)
    assert dac_all.values["Total Cost [$/tCO2]"] > 0

import pytest

from dac_costing.model import BatterySection, DacModel, DacSection, EnergySection, NgThermalSection


def test_default_parameters():
    dac = DacSection()
    assert dac._params["Scale [tCO2/year]"] == 1000000


def test_update_parameters():
    dac = DacSection(**{"Scale [tCO2/year]": 10})
    assert dac._params["Scale [tCO2/year]"] == 10


def test_to_pandas():
    dac = DacSection()
    assert dac.values == {}
    dac.compute()
    assert dac.values
    assert len(dac.series)


def test_c1_natural_gas():
    params = {"Base Energy Requierement [MW]": 47}
    electric = EnergySection("NGCC w/ CCS", battery=None, **params)

    params = {"Required Thermal Energy [GJ/tCO2]": 6.64}
    thermal = NgThermalSection("Advanced NGCC", battery=None, **params)

    params = {"Total Capex [$]": 1029}
    dac = DacSection(**params)

    dac_all = DacModel(electric, thermal, dac, **params)

    assert len(dac_all.compute().series)
    assert 220 <= dac_all.values["Total Cost [$/tCO2]"] <= 230


def test_c2_solar():
    params = {"Base Energy Requierement [MW]": 38}
    ebattery = BatterySection(**params)
    electric = EnergySection("Solar", battery=ebattery, **params)

    params = {"Base Energy Requierement [MW]": 234}
    tbattery = BatterySection(**params)
    thermal = EnergySection("Solar", battery=tbattery, **params)

    params = {"Total Capex [$]": 936.01}
    dac = DacSection(**params)

    dac_all = DacModel(electric, thermal, dac, **params)

    assert len(dac_all.compute().series)
    assert 470 <= dac_all.values["Total Cost [$/tCO2]"] <= 490


@pytest.mark.xfail(reason="Need custom electric/thermal blocks for nuclear to match spreadsheet")
def test_c3_nuclear():
    params = {"Base Energy Requierement [MW]": 38}
    electric = EnergySection("Advanced Nuclear", battery=None, **params)

    params = {"Base Energy Requierement [MW]": 234}
    thermal = EnergySection("Advanced Nuclear", battery=None, **params)

    params = {"Total Capex [$]": 936.01}
    dac = DacSection(**params)

    dac_all = DacModel(electric, thermal, dac, **params)

    assert len(dac_all.compute().series)
    assert 395 <= dac_all.values["Total Cost [$/tCO2]"] <= 405


def test_c2_wind():
    params = {"Base Energy Requierement [MW]": 38}
    ebattery = BatterySection(**params)
    electric = EnergySection("Wind", battery=ebattery, **params)

    params = {"Base Energy Requierement [MW]": 234}
    tbattery = BatterySection(**params)
    thermal = EnergySection("Wind", battery=tbattery, **params)

    params = {"Total Capex [$]": 936.01}
    dac = DacSection(**params)

    dac_all = DacModel(electric, thermal, dac, **params)

    assert len(dac_all.compute().series)
    assert 385 <= dac_all.values["Total Cost [$/tCO2]"] <= 395

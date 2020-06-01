import pytest

from dac_costing.model import BatterySection, DacModel, DacSection, EnergySection, NgThermalSection


def test_default_parameters():
    dac = DacSection()
    assert dac.params["Scale [tCO2/year]"] == 1000000


def test_update_parameters():
    dac = DacSection(params={"Scale [tCO2/year]": 10})
    assert dac.params["Scale [tCO2/year]"] == 10


def test_to_pandas():
    dac = DacSection()
    assert dac.values == {}
    dac.compute()
    assert dac.values
    assert len(dac.series)


def test_invalid_source_raises():

    with pytest.raises(ValueError) as excinfo:
        EnergySection(source="foo")
    assert "Invalid Energy Source: foo" in str(excinfo.value)


def test_invalid_ngsource_raises():

    with pytest.raises(ValueError) as excinfo:
        NgThermalSection(source="Wind")
    assert "Invalid Natural Gas Source" in str(excinfo.value)


def test_c1_natural_gas():
    params = {"Base Energy Requirement [MW]": 47}
    electric = EnergySection(source="NGCC w/ CCS", battery=None, params=params)
    print(electric.compute().values)

    params = {"Required Thermal Energy [GJ/tCO2]": 6.64}
    thermal = NgThermalSection(source="Advanced NGCC", battery=None, params=params)
    print(thermal.compute().values)

    params = {"Total Capex [$]": 1029}
    dac = DacSection(params=params)
    print(dac.compute().values)

    dac_all = DacModel(electric=electric, thermal=thermal, dac=dac, params=params)
    print(dac_all.compute().values)

    assert len(dac_all.compute().series)
    assert 220 <= dac_all.values["Total Cost [$/tCO2]"] <= 230


def test_c2_solar():
    params = {"Base Energy Requirement [MW]": 38}
    ebattery = BatterySection(params=params)
    electric = EnergySection(source="Solar", battery=ebattery, params=params)

    params = {"Base Energy Requirement [MW]": 234}
    tbattery = BatterySection(params=params)
    thermal = EnergySection(source="Solar", battery=tbattery, params=params)

    params = {"Total Capex [$]": 936.01}
    dac = DacSection(params=params)

    dac_all = DacModel(electric=electric, thermal=thermal, dac=dac, params=params)

    assert len(dac_all.compute().series)
    assert 470 <= dac_all.values["Total Cost [$/tCO2]"] <= 490


@pytest.mark.xfail(reason="Need custom electric/thermal blocks for nuclear to match spreadsheet")
def test_c3_nuclear():
    params = {"Base Energy Requirement [MW]": 38}
    electric = EnergySection(source="Advanced Nuclear", battery=None, params=params)

    params = {"Base Energy Requirement [MW]": 234}
    thermal = EnergySection(source="Advanced Nuclear", battery=None, params=params)

    params = {"Total Capex [$]": 936.01}
    dac = DacSection(params=params)

    dac_all = DacModel(electric=electric, thermal=thermal, dac=dac, params=params)

    assert len(dac_all.compute().series)
    assert 395 <= dac_all.values["Total Cost [$/tCO2]"] <= 405


def test_c2_wind():
    params = {"Base Energy Requirement [MW]": 38}
    ebattery = BatterySection(params=params)
    electric = EnergySection(source="Wind", battery=ebattery, params=params)

    params = {"Base Energy Requirement [MW]": 234}
    tbattery = BatterySection(params=params)
    thermal = EnergySection(source="Wind", battery=tbattery, params=params)

    params = {"Total Capex [$]": 936.01}
    dac = DacSection(params=params)

    dac_all = DacModel(electric=electric, thermal=thermal, dac=dac, params=params)

    assert len(dac_all.compute().series)
    assert 385 <= dac_all.values["Total Cost [$/tCO2]"] <= 395

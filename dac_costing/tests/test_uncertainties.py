import json
import os

import pytest

from dac_costing.model import DacModel, DacSection, EnergySection, NgThermalSection
from dac_costing.uncertainties import cast_params_to_ufloat

uncertainties = pytest.importorskip("uncertainties")


@pytest.fixture
def uparams():
    dir_path = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(dir_path, "../", "data", "parameters.json")) as f:
        params = cast_params_to_ufloat(json.load(f))
    return params


def test_uncertainties(uparams):
    uparams["Base Energy Requirement [MW]"] = 47
    electric = EnergySection(source="NGCC w/ CCS", battery=None, params=uparams)

    uparams["Required Thermal Energy [GJ/tCO2]"] = 6.64
    thermal = NgThermalSection(source="Advanced NGCC", battery=None, params=uparams)

    uparams["Total Capex [$]"] = 1029
    dac = DacSection(params=uparams)

    dac_all = DacModel(electric=electric, thermal=thermal, dac=dac, params=uparams)

    assert len(dac_all.compute().series)
    assert 220 <= dac_all.values["Total Cost [$/tCO2]"] <= 230
    assert isinstance(dac_all.values["Total Cost [$/tCO2]"], uncertainties.core.AffineScalarFunc)

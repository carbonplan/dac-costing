import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, DefaultDict, Dict, Optional, Type

import numpy as np
import numpy_financial as npf
import pandas as pd

VALID_ENERGYSOURCES = ["NGCC w/ CCS", "Advanced NGCC", "Solar", "Wind", "Advanced Nuclear"]

# Constants
HOURS_PER_DAY = 24
DAYS_PER_YEAR = 365
HOURS_PER_YEAR = DAYS_PER_YEAR * HOURS_PER_DAY
MILLION = 1e6
KW_TO_MW = 1000
LB_TO_METRIC_TON = 0.000453592
GJ_TO_MMBTU = 0.94709


def default_params() -> Dict:
    """ load default parameters """
    dir_path = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(dir_path, "data", "parameters.json")) as f:
        return json.load(f)


def nan() -> float:
    return np.nan


def values_factory() -> DefaultDict:
    return defaultdict(nan)


@dataclass
class DacComponent(object):
    """
    Base DAC Component Class

    Parameters
    ----------
    params: dict
        Model parameters
    """

    params: Dict[str, Any] = field(default_factory=dict, repr=False)

    values: DefaultDict[str, float] = field(default_factory=values_factory, init=False, repr=False)

    def __post_init__(self):
        # update default parameters with those supplied by init
        self.params = {**default_params(), **self.params}

    @property
    def series(self):
        """ return a pandas.Series view of the components values """
        if not self.values:
            self.compute()
        return pd.Series(self.values)

    def compute(self):
        """ compute this components values """
        raise NotImplementedError()
        return self

    def lead_time_mult(self, time) -> float:
        """replaces cells =Q5:AB158 in `WACC Table Project Lead Time`

        TODO: needs better doc and review from Noah.
        """
        rate = self.params["WACC [%]"]
        time = int(time)

        values = [(1 + rate) * (1 / time)]
        for t in range(1, time):
            values.append(np.sum(values[:t]) * rate + (1 + rate) * (1 / time))
        return np.array(values).sum()

    def recovery_factor(self):
        """ calculate the capital recovery factor """
        return -npf.pmt(self.params["WACC [%]"], self.params["Economic Lifetime [years]"], 1)


@dataclass
class BatterySection(DacComponent):
    """
    Batter Section Component

    Parameters
    ----------
    params: dict
        Model parameters
    """

    def compute(self, e_vals):
        """compute the battery section values

        Parameters
        ----------
        e_vals : dict
            Values from the energy section that will use this battery.
        """

        tech = self.params["Technology"]["Battery Storage"]

        # Battery Capacity [MWh]
        self.values["Battery Capacity [MWh]"] = e_vals["Base Energy Requirement [MW]"] * (
            HOURS_PER_DAY * (1 - e_vals["Planned Capacity Factor"])
        )

        # Round Trip Efficiency
        self.values["Round Trip Efficiency"] = tech["Efficiency (Thermal or Round Trip)"]

        # Battery Capacity Needed [MWh]
        self.values["Battery Capacity Needed [MWh]"] = (
            self.values["Battery Capacity [MWh]"] / self.values["Round Trip Efficiency"]
        )

        # Increased [MWh]
        self.values["Increased [MWh]"] = (
            self.values["Battery Capacity Needed [MWh]"] - self.values["Battery Capacity [MWh]"]
        )

        # Increased Solar/Wind Need
        self.values["Increased Need [MW]"] = self.values["Increased [MWh]"] / (
            HOURS_PER_DAY * e_vals["Planned Capacity Factor"]
        )

        # Battery Capital Cost [M$]
        self.values["Battery Capital Cost [M$]"] = (
            tech["Base Plant Cost [M$]"]
            * (self.values["Battery Capacity Needed [MWh]"] / tech["Battery Capacity [MWhr]"])
            ** tech["Scaling Factor"]
        )

        # Battery Fixed O&M [$/tCO2eq]
        self.values["Battery Fixed O&M [$/tCO2eq]"] = (
            (
                tech["Base Plant Annual Fixed O&M [$M]"]
                * (self.values["Battery Capacity Needed [MWh]"] / tech["Battery Capacity [MWhr]"])
                ** tech["Scaling Factor"]
            )
            * MILLION
            / self.params["Scale [tCO2/year]"]
        )

        # Battery Variable O&M [$/tCO2eq]
        self.values["Battery Variable O&M [$/tCO2eq]"] = (
            tech["Variable O&M [$/MWhr]"]
            * self.values["Battery Capacity [MWh]"]
            / self.params["Scale [tCO2/year]"]
            * DAYS_PER_YEAR
        )

        return self


@dataclass
class EnergySection(DacComponent):
    """
    Energy Section Component

    This section can be used for either electric or thermal energy demand

    Parameters
    ----------
    params: dict
        Model parameters
    battery : BatterySection
        Battery component.
    source : str
        Energy source. Valid values include: {'NGCC w/ CCS', 'Advanced NGCC', 'Solar', 'Wind', 'Advanced Nuclear'}
    """

    source: str = "NGCC w/ CCS"
    battery: Optional[Type[BatterySection]] = None

    def __post_init__(self):
        if self.source not in VALID_ENERGYSOURCES:
            raise ValueError(
                f"Invalid Energy Source: {self.source}, expected one of {VALID_ENERGYSOURCES}"
            )

        super().__post_init__()

    def compute(self):
        """ compute the energy section values """

        tech = self.params["Technology"][self.source]

        # Operational Hours [h/yr]
        operational_hours = self.params["DAC Capacity Factor"] * HOURS_PER_YEAR

        # Planned Capacity Factor
        self.values["Planned Capacity Factor"] = tech["Availability"]

        # Electric Power Requirement [MW] (aka low value case in C1)
        self.values["Base Energy Requirement [MW]"] = self.params["Base Energy Requirement [MW]"]

        # calculate battery params now
        if self.battery:
            self.battery.compute(self.values)
            self.values.update(self.battery.values)

        # Plant Size [MW]
        self.values["Plant Size [MW]"] = (
            self.values["Base Energy Requirement [MW]"] / self.values["Planned Capacity Factor"]
        )
        if self.battery:
            self.values["Plant Size [MW]"] += self.values["Increased Need [MW]"]

        # Overnight Cost [M$]
        self.values["Overnight Cost [M$]"] = (
            tech["Base Plant Cost [M$]"]
            * (self.values["Plant Size [MW]"] / tech["Plant Size [MW]"]) ** tech["Scaling Factor"]
        )

        # Lead Time Multiplier
        self.values["Lead Time Multiplier"] = self.lead_time_mult(tech["Lead Time [Years]"])

        # Capital Cost [M$]
        self.values["Capital Cost [M$]"] = (
            self.values["Overnight Cost [M$]"] * self.values["Lead Time Multiplier"]
        )

        # Total Capital Cost [M$]
        self.values["Total Capital Cost [M$]"] = self.values["Capital Cost [M$]"]
        if self.battery:
            self.values["Total Capital Cost [M$]"] += self.values["Battery Capital Cost [M$]"]

        # Annual Capital Recovery Factor
        annual_capital_recovery_factor = self.recovery_factor()

        # Capital Recovery [$/tCO2eq]
        self.values["Capital Recovery [$/tCO2eq]"] = (
            self.values["Total Capital Cost [M$]"]
            * annual_capital_recovery_factor
            * MILLION
            / self.params["Scale [tCO2/year]"]
        )

        # Power Fixed O&M [$/tCO2eq]
        self.values["Power Fixed O&M [$/tCO2eq]"] = (
            (
                tech["Base Plant Annual Fixed O&M [$M]"]
                * (self.values["Plant Size [MW]"] / tech["Plant Size [MW]"])
                ** tech["Scaling Factor"]
            )
            * MILLION
            / self.params["Scale [tCO2/year]"]
        )

        # Power Variable O&M [$/tCO2eq]
        self.values["Power Variable O&M [$/tCO2eq]"] = (
            tech["Variable O&M [$/MWhr]"]
            * self.values["Plant Size [MW]"]
            * operational_hours
            / self.params["Scale [tCO2/year]"]
        )

        # Total Fixed O&M [$/tCO2eq]
        self.values["Total Fixed O&M [$/tCO2eq]"] = self.values["Power Fixed O&M [$/tCO2eq]"]
        if self.battery:
            self.values["Total Fixed O&M [$/tCO2eq]"] += self.values["Battery Fixed O&M [$/tCO2eq]"]

        # Total Variable O&M [$/tCO2eq]
        self.values["Total Variable O&M [$/tCO2eq]"] = self.values["Power Variable O&M [$/tCO2eq]"]
        if self.battery:
            self.values["Total Variable O&M [$/tCO2eq]"] += self.values[
                "Battery Variable O&M [$/tCO2eq]"
            ]

        # Natural Gas Use [mmBTU/tCO2eq]
        # TODO: need to handle natural gas use in thermal block when plant size is 0.
        heat_rate = tech["Final Heat Rate [BTU/kWh]"]
        if pd.notnull(heat_rate):
            self.values["Natural Gas Use [mmBTU/tCO2eq]"] = (
                operational_hours
                * self.values["Plant Size [MW]"]
                * KW_TO_MW
                * tech["Final Heat Rate [BTU/kWh]"]
                / MILLION
                / self.params["Scale [tCO2/year]"]
            )
        else:
            self.values["Natural Gas Use [mmBTU/tCO2eq]"] = 0.0

        # Natural Gas Cost [$/tCO2eq]
        self.values["Natural Gas Cost [$/tCO2eq]"] = (
            self.values["Natural Gas Use [mmBTU/tCO2eq]"]
            * self.params["Natural Gas Cost [$/mmBTU]"]
        )

        # Emitted [tCO2eq/tCO2]
        self.values["Emitted [tCO2eq/tCO2]"] = (
            self.values["Natural Gas Use [mmBTU/tCO2eq]"]
            * tech["Total CO2 eq [lb/mmbtu]"]
            * LB_TO_METRIC_TON
            * (1 - tech["Capture Efficiency"])
        )

        # Total Cost [$/tCO2eq gross]
        self.values["Total Cost [$/tCO2eq gross]"] = (
            self.values["Capital Recovery [$/tCO2eq]"]
            + self.values["Total Fixed O&M [$/tCO2eq]"]
            + self.values["Total Variable O&M [$/tCO2eq]"]
        )

        # Total Cost [$/tCO2eq net]
        self.values["Total Cost [$/tCO2eq net]"] = self.values["Total Cost [$/tCO2eq gross]"] / (
            1 - self.values["Emitted [tCO2eq/tCO2]"]
        )

        return self


@dataclass
class NgThermalSection(EnergySection):
    def __post_init__(self):
        if self.source not in ["NGCC w/ CCS", "Advanced NGCC"]:
            raise ValueError(f"Invalid Natural Gas Source: {self.source}")
        super().__post_init__()

    def compute(self):

        for key in [
            "Plant Size [MW]",
            "Total Capital Cost [M$]",
            "Capital Recovery [$/tCO2eq]",
            "Total Fixed O&M [$/tCO2eq]",
            "Total Variable O&M [$/tCO2eq]",
        ]:
            self.values[key] = 0

        nat_gas_mmbtu = (
            self.params["Required Thermal Energy [GJ/tCO2]"]
            * GJ_TO_MMBTU
            * self.params["Scale [tCO2/year]"]
        )
        capacity = 1  # hardcoded
        self.values["Natural Gas Use [mmBTU/tCO2eq]"] = nat_gas_mmbtu / (
            capacity * self.params["Scale [tCO2/year]"]
        )

        # same as above
        # Natural Gas Cost [$/tCO2eq]
        self.values["Natural Gas Cost [$/tCO2eq]"] = (
            self.values["Natural Gas Use [mmBTU/tCO2eq]"]
            * self.params["Natural Gas Cost [$/mmBTU]"]
        )

        # Assume 100% capture from oxy fired kiln
        self.values["Emitted [tCO2eq/tCO2]"] = 0.0

        return self


@dataclass
class DacSection(DacComponent):
    """
    DAC Section Component

    This section represents the non-energy costs associated with a DAC facility

    Parameters
    ----------
    params: dict
        Model parameters
    """

    def compute(self):
        """ compute the DAC section values """

        # Total Overnight Capital Cost [M$]
        self.values["Total Capital Cost [M$]"] = self.params["Total Capex [$]"]

        # Lead Time Multiplier
        self.values["Lead Time Multiplier"] = self.lead_time_mult(
            self.params["DAC Section Lead Time [years]"]
        )

        # Capital Cost (including Lead Time) [M$]
        self.values["Capital Cost (including Lead Time) [M$]"] = (
            self.values["Total Capital Cost [M$]"] * self.values["Lead Time Multiplier"]
        )

        # Capital Recovery [$/tCO2eq]
        self.values["Capital Recovery [$/tCO2eq]"] = (
            self.values["Total Capital Cost [M$]"]
            * self.recovery_factor()
            * MILLION
            / self.params["Scale [tCO2/year]"]
        )

        # Fixed O&M [$/tCO2eq]
        self.values["Fixed O&M [$/tCO2eq]"] = self.params["Fixed O&M Costs [$/tCO2]"]

        # Variable O&M [$/tCO2eq]
        self.values["Variable O&M [$/tCO2eq]"] = self.params["Variable O&M Cost [$/tCO2]"]

        # Total Cost [$/tCO2]
        self.values["Total Cost [$/tCO2]"] = (
            self.values["Capital Recovery [$/tCO2eq]"]
            + self.values["Fixed O&M [$/tCO2eq]"]
            + self.values["Variable O&M [$/tCO2eq]"]
        )

        # # Total Cost [$/tCO2 net removed]
        # v['Total Cost [$/tCO2 net removed]'] = v['Total Cost [$/tCO2]'] / (
        #     1 - (ev['Emitted [tCO2eq/tCO2]'] + tv['Emitted [tCO2eq/tCO2]'])
        # )

        return self


@dataclass
class DacModel(DacComponent):
    """
    Composite DAC Model Component

    Parameters
    ----------
    electric : EnergySection
        Electric energy component.
    thermal : EnergySection
        Thermal energy component.
    dac : DacSection
        DAC section component.
    params: dict
        Model parameters
    """

    electric: Optional[Type[EnergySection]] = None
    thermal: Optional[Type[EnergySection]] = None
    dac: Optional[Type[DacSection]] = None

    def _combined_power_block_requirements(self, source, ev, tv) -> DefaultDict[str, float]:
        """compute the combined power block requirements

        Parameters
        ----------
        source : str
            Energy source.
        ev : dict
            Electric section values
        tv : dict
            Thermal section values

        Returns
        -------
        v : dict
            Combined power block values
        """

        tech = self.params["Technology"][source]  # type: Dict
        bat_tech = self.params["Technology"]["Battery Storage"]  # type: Dict
        v = values_factory()

        # Operational Hours [h/yr]
        operational_hours = self.params["DAC Capacity Factor"] * HOURS_PER_YEAR

        # Power Plant Size
        v["Plant Size [MW]"] = ev["Plant Size [MW]"] + tv["Plant Size [MW]"]

        # Overnight Cost [M$]
        v["Overnight Cost [M$]"] = (
            tech["Base Plant Cost [M$]"]
            * (v["Plant Size [MW]"] / tech["Plant Size [MW]"]) ** tech["Scaling Factor"]
        )

        # Lead Time Multiplier
        v["Lead Time Multiplier"] = self.lead_time_mult(tech["Lead Time [Years]"])

        # Capital Cost [M$]
        v["Capital Cost [M$]"] = v["Overnight Cost [M$]"] * v["Lead Time Multiplier"]

        # Power Fixed O&M [$/tCO2eq]
        v["Power Fixed O&M [$/tCO2eq]"] = (
            (
                tech["Base Plant Annual Fixed O&M [$M]"]
                * (v["Plant Size [MW]"] / tech["Plant Size [MW]"]) ** tech["Scaling Factor"]
            )
            * MILLION
            / self.params["Scale [tCO2/year]"]
        )

        # Power Variable O&M [$/tCO2eq]
        v["Power Variable O&M [$/tCO2eq]"] = (
            tech["Variable O&M [$/MWhr]"]
            * v["Plant Size [MW]"]
            * operational_hours
            / self.params["Scale [tCO2/year]"]
        )

        if "Battery Capacity Needed [MWh]" in ev:
            # Battery Capacity [MWh]
            v["Battery Capacity [MWh]"] = (
                ev["Battery Capacity Needed [MWh]"] + tv["Battery Capacity Needed [MWh]"]
            )

            # Battery Capital Cost [M$]
            v["Battery Capital Cost [M$]"] = (
                bat_tech["Base Plant Cost [M$]"]
                * (v["Battery Capacity [MWh]"] / bat_tech["Battery Capacity [MWhr]"])
                ** bat_tech["Scaling Factor"]
            )

            # Battery Fixed O&M [$/tCO2eq]
            v["Battery Fixed O&M [$/tCO2eq]"] = (
                (
                    bat_tech["Base Plant Annual Fixed O&M [$M]"]
                    * (v["Battery Capacity [MWh]"] / bat_tech["Battery Capacity [MWhr]"])
                    ** bat_tech["Scaling Factor"]
                )
                * MILLION
                / self.params["Scale [tCO2/year]"]
            )

            # Battery Variable O&M [$/tCO2eq]
            v["Battery Variable O&M [$/tCO2eq]"] = (
                bat_tech["Variable O&M [$/MWhr]"]
                * v["Battery Capacity [MWh]"]
                / self.params["Scale [tCO2/year]"]
                * DAYS_PER_YEAR
            )
        else:
            v["Battery Capacity [MWh]"] = 0
            v["Battery Capital Cost [M$]"] = 0
            v["Battery Fixed O&M [$/tCO2eq]"] = 0
            v["Battery Variable O&M [$/tCO2eq]"] = 0

        # Total Capital Cost [M$]
        v["Total Capital Cost [M$]"] = v["Capital Cost [M$]"] + v["Battery Capital Cost [M$]"]

        # Capital Recovery [$/tCO2eq]
        v["Capital Recovery [$/tCO2eq]"] = (
            v["Total Capital Cost [M$]"]
            * self.recovery_factor()
            * MILLION
            / self.params["Scale [tCO2/year]"]
        )

        # Fixed O&M [$/tCO2eq]
        v["Fixed O&M [$/tCO2eq]"] = (
            v["Power Fixed O&M [$/tCO2eq]"] + v["Battery Fixed O&M [$/tCO2eq]"]
        )

        # Variable O&M [$/tCO2eq]
        v["Variable O&M [$/tCO2eq]"] = (
            v["Power Variable O&M [$/tCO2eq]"] + v["Battery Variable O&M [$/tCO2eq]"]
        )

        return v

    def _total_energy_block_costs(self, ev, tv) -> DefaultDict[str, float]:
        """compute the total energy block costs

        Parameters
        ----------
        ev : dict
            Electric section values
        tv : dict
            Thermal section values

        Returns
        -------
        v : dict
            Total energy block values
        """
        v = values_factory()

        # Total Power Capacity Required [MW]
        v["Total Power Capacity Required [MW]"] = ev["Plant Size [MW]"] + tv["Plant Size [MW]"]

        # Total Battery Capacity Required [MWh]
        if "Battery Capacity Needed [MWh]" in ev:
            v["Total Battery Capacity Required [MWh]"] = (
                ev["Battery Capacity Needed [MWh]"] + tv["Battery Capacity Needed [MWh]"]
            )
        else:
            v["Total Battery Capacity Required [MWh]"] = 0.0

        # Total Capital Cost [M$]
        v["Total Capital Cost [M$]"] = ev["Total Capital Cost [M$]"] + tv["Total Capital Cost [M$]"]

        # Capital Recovery [$/tCO2eq]
        v["Capital Recovery [$/tCO2eq]"] = (
            ev["Capital Recovery [$/tCO2eq]"] + tv["Capital Recovery [$/tCO2eq]"]
        )

        # Fixed O&M [$/tCO2eq]
        v["Fixed O&M [$/tCO2eq]"] = (
            ev["Total Fixed O&M [$/tCO2eq]"] + tv["Total Fixed O&M [$/tCO2eq]"]
        )

        # Variable O&M [$/tCO2eq]
        v["Variable O&M [$/tCO2eq]"] = (
            ev["Total Variable O&M [$/tCO2eq]"] + tv["Total Variable O&M [$/tCO2eq]"]
        )

        # NG Cost [$/tCO2eq]
        v["Natural Gas Cost [$/tCO2eq]"] = (
            ev["Natural Gas Cost [$/tCO2eq]"] + tv["Natural Gas Cost [$/tCO2eq]"]
        )

        # Total Cost [$/tCO2]
        v["Total Cost [$/tCO2]"] = (
            v["Capital Recovery [$/tCO2eq]"]
            + v["Fixed O&M [$/tCO2eq]"]
            + v["Variable O&M [$/tCO2eq]"]
            + v["Natural Gas Cost [$/tCO2eq]"]
        )

        # Net Capture [tCO2/yr]
        v["Net Capture [tCO2/yr]"] = self.params["Scale [tCO2/year]"] - self.params[
            "Scale [tCO2/year]"
        ] * (ev["Emitted [tCO2eq/tCO2]"] + tv["Emitted [tCO2eq/tCO2]"])

        # Total Cost [$/tCO2 net removed]
        v["Total Cost [$/tCO2 net removed]"] = v["Total Cost [$/tCO2]"] / (
            1 - (ev["Emitted [tCO2eq/tCO2]"] + tv["Emitted [tCO2eq/tCO2]"])
        )

        return v

    def _total_energy_block_costs_combined(self, ev, tv, cv) -> DefaultDict[str, float]:
        """compute the total energy block costs

        Parameters
        ----------
        ev : dict
            Electric section values
        tv : dict
            Thermal section values
        cv : dict
            Combined energy block values

        Returns
        -------
        v : dict
            Total energy block values
        """
        v = values_factory()

        # Total Power Capacity Required [MW]
        v["Total Power Capacity Required [MW]"] = ev["Plant Size [MW]"] + tv["Plant Size [MW]"]

        # Total Battery Capacity Required [MWh]
        if "Battery Capacity Needed [MWh]" in ev:
            v["Total Battery Capacity Required [MWh]"] = (
                ev["Battery Capacity Needed [MWh]"] + tv["Battery Capacity Needed [MWh]"]
            )
        else:
            v["Total Battery Capacity Required [MWh]"] = 0

        # Total Capital Cost [M$]
        v["Total Capital Cost [M$]"] = cv["Total Capital Cost [M$]"]

        # Capital Recovery [$/tCO2eq]
        v["Capital Recovery [$/tCO2eq]"] = cv["Capital Recovery [$/tCO2eq]"]

        # Fixed O&M [$/tCO2eq]
        v["Fixed O&M [$/tCO2eq]"] = cv["Fixed O&M [$/tCO2eq]"]

        # Variable O&M [$/tCO2eq]
        v["Variable O&M [$/tCO2eq]"] = cv["Variable O&M [$/tCO2eq]"]

        # NG Cost [$/tCO2eq]
        v["Natural Gas Cost [$/tCO2eq]"] = (
            ev["Natural Gas Cost [$/tCO2eq]"] + tv["Natural Gas Cost [$/tCO2eq]"]
        )

        # Total Cost [$/tCO2]
        v["Total Cost [$/tCO2]"] = (
            v["Capital Recovery [$/tCO2eq]"]
            + v["Fixed O&M [$/tCO2eq]"]
            + v["Variable O&M [$/tCO2eq]"]
            + v["Natural Gas Cost [$/tCO2eq]"]
        )

        # Net Capture [tCO2/yr]
        v["Net Capture [tCO2/yr]"] = self.params["Scale [tCO2/year]"] - self.params[
            "Scale [tCO2/year]"
        ] * (ev["Emitted [tCO2eq/tCO2]"] + tv["Emitted [tCO2eq/tCO2]"])

        # Total Cost [$/tCO2 net removed]
        v["Total Cost [$/tCO2 net removed]"] = v["Total Cost [$/tCO2]"] / (
            1 - (ev["Emitted [tCO2eq/tCO2]"] + tv["Emitted [tCO2eq/tCO2]"])
        )

        return v

    def compute(self):
        """ compute the composite DAC model's values """

        ev = self.electric.compute().values
        tv = self.thermal.compute().values
        if self.electric.source == self.thermal.source:
            cv = self._combined_power_block_requirements(self.electric.source, ev, tv)
            tev = self._total_energy_block_costs_combined(ev, tv, cv)
        else:
            tev = self._total_energy_block_costs(ev, tv)

        dv = self.dac.compute().values

        # Total Capital Cost [M$]
        self.values["Total Capital Cost [M$]"] = (
            tev["Total Capital Cost [M$]"] + dv["Capital Cost (including Lead Time) [M$]"]
        )

        # Capital Recovery [$/tCO2eq]
        # =K86*'Economic Parameters'!C6*10^6/'Report Data'!C3
        self.values["Capital Recovery [$/tCO2eq]"] = (
            self.values["Total Capital Cost [M$]"]
            * self.recovery_factor()
            * MILLION
            / self.params["Scale [tCO2/year]"]
        )

        # Fixed O&M [$/tCO2eq]
        self.values["Fixed O&M [$/tCO2eq]"] = (
            tev["Fixed O&M [$/tCO2eq]"] + dv["Fixed O&M [$/tCO2eq]"]
        )

        # Variable O&M [$/tCO2eq]
        self.values["Variable O&M [$/tCO2eq]"] = (
            tev["Variable O&M [$/tCO2eq]"] + dv["Variable O&M [$/tCO2eq]"]
        )

        # Natural Gas Cost [$/tCO2]
        self.values["Natural Gas Cost [$/tCO2]"] = tev["Natural Gas Cost [$/tCO2eq]"]

        # Total Cost [$/tCO2]
        self.values["Total Cost [$/tCO2]"] = (
            self.values["Capital Recovery [$/tCO2eq]"]
            + self.values["Fixed O&M [$/tCO2eq]"]
            + self.values["Variable O&M [$/tCO2eq]"]
            + self.values["Natural Gas Cost [$/tCO2]"]
        )

        #         # Total Cost [$/tCO2 Net Removed]
        #         self.values['Total Cost [$/tCO2 Net Removed]'] = (
        #             tev['Total Cost [$/tCO2 net removed]'] + dv['Total Cost [$/tCO2 net removed]']
        #         )

        return self

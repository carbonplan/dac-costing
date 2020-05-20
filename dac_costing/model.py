import json
import os
from collections import defaultdict

import numpy as np
import numpy_financial as npf
import pandas as pd

VALID_ENERGY_SOURCES = ["NGCC w/ CCS", "Advanced NGCC", "Solar", "Wind", "Advanced Nuclear"]

# Constants
HOURS_PER_DAY = 24
DAYS_PER_YEAR = 365
HOURS_PER_YEAR = DAYS_PER_YEAR * HOURS_PER_DAY
MILLION = 1e6
KW_TO_MW = 1000
LB_TO_METRIC_TON = 0.000453592
GJ_TO_MMBTU = 0.94709


class DacComponent(object):
    """
    Base DAC Component Class

    Parameters
    ----------
    **params
        Model parameters
    """

    def __init__(self, **params):

        self._params = self._default_params()
        self._params.update(params)

        self.values = defaultdict(float)

    def _default_params(self):
        """ load default parameters """
        dir_path = os.path.dirname(os.path.realpath(__file__))
        with open(os.path.join(dir_path, "data", "parameters.json")) as f:
            return json.load(f)

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

    def lead_time_mult(self, time):
        """ replaces cells =Q5:AB158 in `WACC Table Project Lead Time`

        TODO: needs better doc and review from Noah.
        """
        rate = self._params["WACC [%]"]
        time = int(time)

        vals = [(1 + rate) * (1 / time)]
        for t in range(1, time):
            vals.append(np.sum(vals[:t]) * rate + (1 + rate) * (1 / time))
        return np.array(vals).sum()

    def recovery_factor(self):
        """ calculate the capital recovery factor """
        return -npf.pmt(self._params["WACC [%]"], self._params["Economic Lifetime [years]"], 1)


class BatterySection(DacComponent):
    """
    Batter Section Component

    Parameters
    ----------
    **params
        Model parameters
    """

    def __init__(self, **params):

        super().__init__(**params)

        self._tech = self._params["Technology"]["Battery Storage"]

    def compute(self, e_vals):
        """ compute the battery section values

        Parameters
        ----------
        e_vals : dict
            Values from the energy section that will use this battery.
        """
        v = defaultdict(float)

        # Battery Capacity [MWh]
        v["Battery Capacity [MWh]"] = e_vals["Base Energy Requierement [MW]"] * (
            HOURS_PER_DAY * (1 - e_vals["Planned Capacity Factor"])
        )

        # Round Trip Effciency
        v["Round Trip Effciency"] = self._tech["Efficiency (Thermal or Round Trip)"]

        # Battery Capacity Needed [MWh]
        v["Battery Capacity Needed [MWh]"] = v["Battery Capacity [MWh]"] / v["Round Trip Effciency"]

        # Increased [MWh]
        v["Increased [MWh]"] = v["Battery Capacity Needed [MWh]"] - v["Battery Capacity [MWh]"]

        # Increased Solar/Wind Need
        v["Increased Need [MW]"] = v["Increased [MWh]"] / (
            HOURS_PER_DAY * e_vals["Planned Capacity Factor"]
        )

        # Battery Capital Cost [M$]
        v["Battery Capital Cost [M$]"] = (
            self._tech["Base Plant Cost [M$]"]
            * (v["Battery Capacity Needed [MWh]"] / self._tech["Battery Capacity [MWhr]"])
            ** self._tech["Scaling Factor"]
        )

        # Battery Fixed O&M [$/tCO2eq]
        v["Battery Fixed O&M [$/tCO2eq]"] = (
            (
                self._tech["Base Plant Annual Fixed O&M [$M]"]
                * (v["Battery Capacity Needed [MWh]"] / self._tech["Battery Capacity [MWhr]"])
                ** self._tech["Scaling Factor"]
            )
            * MILLION
            / self._params["Scale [tCO2/year]"]
        )

        # Battery Variable O&M [$/tCO2eq]
        v["Battery Variable O&M [$/tCO2eq]"] = (
            self._tech["Variable O&M [$/MWhr]"]
            * v["Battery Capacity [MWh]"]
            / self._params["Scale [tCO2/year]"]
            * DAYS_PER_YEAR
        )

        self.values.update(v)

        return self


class EnergySection(DacComponent):
    """
    Energy Section Component

    This section can be used for either electric or thermal energy demand

    Parameters
    ----------
    source : str
        Energy source. Valid values include: {'NGCC w/ CCS', 'Advanced NGCC', 'Solar', 'Wind', 'Advanced Nuclear'}
    battery : BatterySection, optional
        Battery component.
    **params
        Model parameters
    """

    def __init__(self, source, battery=None, **params):

        if source not in VALID_ENERGY_SOURCES:
            raise ValueError(
                f"Invalid Energy Source: {source}, expected one of {VALID_ENERGY_SOURCES}"
            )
        self._source = source

        if not (isinstance(battery, BatterySection) or battery is None):
            TypeError(f"Expected a BatterySection, got {battery}")
        self.battery = battery

        super().__init__(**params)

        self._tech = self._params["Technology"][self._source]

    def compute(self):
        """ compute the energy section values """

        v = defaultdict(float)

        # Operational Hours [h/yr]
        operational_hours = self._params["DAC Capacity Factor"] * HOURS_PER_YEAR

        # Planned Capacity Factor
        v["Planned Capacity Factor"] = self._tech["Availability"]

        # Electric Power Requierement [MW] (aka low value case in C1)
        v["Base Energy Requierement [MW]"] = self._params["Base Energy Requierement [MW]"]

        # calcuate battery params now
        if self.battery:
            self.battery.compute(v)
            v.update(self.battery.values)

        # Plant Size [MW]
        v["Plant Size [MW]"] = v["Base Energy Requierement [MW]"] / v["Planned Capacity Factor"]
        if self.battery:
            v["Plant Size [MW]"] += v["Increased Need [MW]"]

        # Overnight Cost [M$]
        v["Overnight Cost [M$]"] = (
            self._tech["Base Plant Cost [M$]"]
            * (v["Plant Size [MW]"] / self._tech["Plant Size [MW]"]) ** self._tech["Scaling Factor"]
        )

        # Lead Time Multiplier
        v["Lead Time Multiplier"] = self.lead_time_mult(self._tech["Lead Time [Years]"])

        # Capital Cost [M$]
        v["Capital Cost [M$]"] = v["Overnight Cost [M$]"] * v["Lead Time Multiplier"]

        # Total Capital Cost [M$]
        v["Total Capital Cost [M$]"] = v["Capital Cost [M$]"]
        if self.battery:
            v["Total Capital Cost [M$]"] += v["Battery Capital Cost [M$]"]

        # Annual Capital Recovery Factor
        annual_capital_recovery_factor = self.recovery_factor()

        # Capital Recovery [$/tCO2eq]
        v["Capital Recovery [$/tCO2eq]"] = (
            v["Total Capital Cost [M$]"]
            * annual_capital_recovery_factor
            * MILLION
            / self._params["Scale [tCO2/year]"]
        )

        # Power Fixed O&M [$/tCO2eq]
        v["Power Fixed O&M [$/tCO2eq]"] = (
            (
                self._tech["Base Plant Annual Fixed O&M [$M]"]
                * (v["Plant Size [MW]"] / self._tech["Plant Size [MW]"])
                ** self._tech["Scaling Factor"]
            )
            * MILLION
            / self._params["Scale [tCO2/year]"]
        )

        # Power Variable O&M [$/tCO2eq]
        v["Power Variable O&M [$/tCO2eq]"] = (
            self._tech["Variable O&M [$/MWhr]"]
            * v["Plant Size [MW]"]
            * operational_hours
            / self._params["Scale [tCO2/year]"]
        )

        # Total Fixed O&M [$/tCO2eq]
        v["Total Fixed O&M [$/tCO2eq]"] = v["Power Fixed O&M [$/tCO2eq]"]
        if self.battery:
            v["Total Fixed O&M [$/tCO2eq]"] += v["Battery Fixed O&M [$/tCO2eq]"]

        # Total Variable O&M [$/tCO2eq]
        v["Total Variable O&M [$/tCO2eq]"] = v["Power Variable O&M [$/tCO2eq]"]
        if self.battery:
            v["Total Variable O&M [$/tCO2eq]"] += v["Battery Variable O&M [$/tCO2eq]"]

        # Natural Gas Use [mmBTU/tCO2eq]
        # TODO: need to handle natural gas use in thermal block when plant size is 0.
        heat_rate = self._tech["Final Heat Rate [BTU/kWh]"]
        if pd.notnull(heat_rate):
            v["Natural Gas Use [mmBTU/tCO2eq]"] = (
                operational_hours
                * v["Plant Size [MW]"]
                * KW_TO_MW
                * self._tech["Final Heat Rate [BTU/kWh]"]
                / MILLION
                / self._params["Scale [tCO2/year]"]
            )
        else:
            v["Natural Gas Use [mmBTU/tCO2eq]"] = 0.0

        # Natural Gas Cost [$/tCO2eq]
        v["Natural Gas Cost [$/tCO2eq]"] = (
            v["Natural Gas Use [mmBTU/tCO2eq]"] * self._params["Natural Gas Cost [$/mmBTU]"]
        )

        # Emitted [tCO2eq/tCO2]
        v["Emitted [tCO2eq/tCO2]"] = (
            v["Natural Gas Use [mmBTU/tCO2eq]"]
            * self._tech["Total CO2 eq [lb/mmbtu]"]
            * LB_TO_METRIC_TON
            * (1 - self._tech["Capture Efficiency"])
        )

        # Total Cost [$/tCO2eq gross]
        v["Total Cost [$/tCO2eq gross]"] = (
            v["Capital Recovery [$/tCO2eq]"]
            + v["Total Fixed O&M [$/tCO2eq]"]
            + v["Total Variable O&M [$/tCO2eq]"]
        )

        # Total Cost [$/tCO2eq net]
        v["Total Cost [$/tCO2eq net]"] = v["Total Cost [$/tCO2eq gross]"] / (
            1 - v["Emitted [tCO2eq/tCO2]"]
        )

        self.values.update(v)

        return self


class NgThermalSection(EnergySection):
    def compute(self):

        v = defaultdict(float)

        nat_gas_mmbtu = (
            self._params["Required Thermal Energy [GJ/tCO2]"]
            * GJ_TO_MMBTU
            * self._params["Scale [tCO2/year]"]
        )
        capacity = 1  # hardcoded
        v["Natural Gas Use [mmBTU/tCO2eq]"] = nat_gas_mmbtu / (
            capacity * self._params["Scale [tCO2/year]"]
        )

        # same as above
        # Natural Gas Cost [$/tCO2eq]
        v["Natural Gas Cost [$/tCO2eq]"] = (
            v["Natural Gas Use [mmBTU/tCO2eq]"] * self._params["Natural Gas Cost [$/mmBTU]"]
        )

        # Assume 100% capture from oxy fired kiln
        v["Emitted [tCO2eq/tCO2]"] = 0.0

        self.values.update(v)

        return self


class DacSection(DacComponent):
    """
    DAC Section Component

    This section represents the non-energy costs associated with a DAC facility

    Parameters
    ----------
    **params
        Model parameters
    """

    def compute(self):
        """ compute the DAC section values """

        v = defaultdict(float)

        # Total Overnight Capital Cost [M$]
        v["Total Capital Cost [M$]"] = self._params["Total Capex [$]"]

        # Lead Time Multiplier
        v["Lead Time Multiplier"] = self.lead_time_mult(
            self._params["DAC Section Lead Time [years]"]
        )

        # Capital Cost (including Lead Time) [M$]
        v["Capital Cost (including Lead Time) [M$]"] = (
            v["Total Capital Cost [M$]"] * v["Lead Time Multiplier"]
        )

        # Capital Recovery [$/tCO2eq]
        v["Capital Recovery [$/tCO2eq]"] = (
            v["Total Capital Cost [M$]"]
            * self.recovery_factor()
            * MILLION
            / self._params["Scale [tCO2/year]"]
        )

        # Fixed O&M [$/tCO2eq]
        v["Fixed O&M [$/tCO2eq]"] = self._params["Fixed O&M Costs [$/tCO2]"]

        # Variable O&M [$/tCO2eq]
        v["Variable O&M [$/tCO2eq]"] = self._params["Varible O&M Cost [$/tCO2]"]

        # Total Cost [$/tCO2]
        v["Total Cost [$/tCO2]"] = (
            v["Capital Recovery [$/tCO2eq]"]
            + v["Fixed O&M [$/tCO2eq]"]
            + v["Variable O&M [$/tCO2eq]"]
        )

        # # Total Cost [$/tCO2 net removed]
        # v['Total Cost [$/tCO2 net removed]'] = v['Total Cost [$/tCO2]'] / (
        #     1 - (ev['Emitted [tCO2eq/tCO2]'] + tv['Emitted [tCO2eq/tCO2]'])
        # )

        self.values.update(v)

        return self


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
    **params
        Model parameters
    """

    def __init__(self, electric, thermal, dac, **params):

        self._electric = electric
        self._thermal = thermal
        self._dac = dac

        super().__init__(**params)

    def _combined_power_block_requirements(self, source, ev, tv):
        """ compute the combined power block requirements

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

        v = defaultdict(float)

        tech = self._params["Technology"][source]

        # Operational Hours [h/yr]
        operational_hours = self._params["DAC Capacity Factor"] * HOURS_PER_YEAR

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
            / self._params["Scale [tCO2/year]"]
        )

        # Power Variable O&M [$/tCO2eq]
        v["Power Variable O&M [$/tCO2eq]"] = (
            tech["Variable O&M [$/MWhr]"]
            * v["Plant Size [MW]"]
            * operational_hours
            / self._params["Scale [tCO2/year]"]
        )

        if "Battery Capacity Needed [MWh]" in ev:
            # Battery Capacity [MWh]
            v["Battery Capacity [MWh]"] = (
                ev["Battery Capacity Needed [MWh]"] + tv["Battery Capacity Needed [MWh]"]
            )

            # Battery Capital Cost [M$]
            v["Battery Capital Cost [M$]"] = (
                self._params["Technology"]["Battery Storage"]["Base Plant Cost [M$]"]
                * (
                    v["Battery Capacity [MWh]"]
                    / self._params["Technology"]["Battery Storage"]["Battery Capacity [MWhr]"]
                )
                ** self._params["Technology"]["Battery Storage"]["Scaling Factor"]
            )

            # Battery Fixed O&M [$/tCO2eq]
            v["Battery Fixed O&M [$/tCO2eq]"] = (
                (
                    self._params["Technology"]["Battery Storage"][
                        "Base Plant Annual Fixed O&M [$M]"
                    ]
                    * (
                        v["Battery Capacity [MWh]"]
                        / self._params["Technology"]["Battery Storage"]["Battery Capacity [MWhr]"]
                    )
                    ** self._params["Technology"]["Battery Storage"]["Scaling Factor"]
                )
                * MILLION
                / self._params["Scale [tCO2/year]"]
            )

            # Battery Variable O&M [$/tCO2eq]
            v["Battery Variable O&M [$/tCO2eq]"] = (
                self._params["Technology"]["Battery Storage"]["Variable O&M [$/MWhr]"]
                * v["Battery Capacity [MWh]"]
                / self._params["Scale [tCO2/year]"]
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
            / self._params["Scale [tCO2/year]"]
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

    def _total_energy_block_costs(self, ev, tv):
        """ compute the total energy block costs

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
        v = defaultdict(float)

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
        v["Net Capture [tCO2/yr]"] = self._params["Scale [tCO2/year]"] - self._params[
            "Scale [tCO2/year]"
        ] * (ev["Emitted [tCO2eq/tCO2]"] + tv["Emitted [tCO2eq/tCO2]"])

        # Total Cost [$/tCO2 net removed]
        v["Total Cost [$/tCO2 net removed]"] = v["Total Cost [$/tCO2]"] / (
            1 - (ev["Emitted [tCO2eq/tCO2]"] + tv["Emitted [tCO2eq/tCO2]"])
        )

        return v

    def _total_energy_block_costs_combined(self, ev, tv, cv):
        """ compute the total energy block costs

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
        v = defaultdict(float)

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
        v["Net Capture [tCO2/yr]"] = self._params["Scale [tCO2/year]"] - self._params[
            "Scale [tCO2/year]"
        ] * (ev["Emitted [tCO2eq/tCO2]"] + tv["Emitted [tCO2eq/tCO2]"])

        # Total Cost [$/tCO2 net removed]
        v["Total Cost [$/tCO2 net removed]"] = v["Total Cost [$/tCO2]"] / (
            1 - (ev["Emitted [tCO2eq/tCO2]"] + tv["Emitted [tCO2eq/tCO2]"])
        )

        return v

    def compute(self):
        """ compute the composite DAC model's values """

        ev = self._electric.compute().values
        tv = self._thermal.compute().values
        if self._electric._source == self._thermal._source:
            cv = self._combined_power_block_requirements(self._electric._source, ev, tv)
            tev = self._total_energy_block_costs_combined(ev, tv, cv)
        else:
            tev = self._total_energy_block_costs(ev, tv)

        dv = self._dac.compute().values

        v = defaultdict(float)

        # Total Capital Cost [M$]
        v["Total Capital Cost [M$]"] = (
            tev["Total Capital Cost [M$]"] + dv["Capital Cost (including Lead Time) [M$]"]
        )

        # Capital Recovery [$/tCO2eq]
        # =K86*'Economic Parameters'!C6*10^6/'Report Data'!C3
        v["Capital Recovery [$/tCO2eq]"] = (
            v["Total Capital Cost [M$]"]
            * self.recovery_factor()
            * MILLION
            / self._params["Scale [tCO2/year]"]
        )

        # Fixed O&M [$/tCO2eq]
        v["Fixed O&M [$/tCO2eq]"] = tev["Fixed O&M [$/tCO2eq]"] + dv["Fixed O&M [$/tCO2eq]"]

        # Variable O&M [$/tCO2eq]
        v["Variable O&M [$/tCO2eq]"] = (
            tev["Variable O&M [$/tCO2eq]"] + dv["Variable O&M [$/tCO2eq]"]
        )

        # Natural Gas Cost [$/tCO2]
        v["Natural Gas Cost [$/tCO2]"] = tev["Natural Gas Cost [$/tCO2eq]"]

        # Total Cost [$/tCO2]
        v["Total Cost [$/tCO2]"] = (
            v["Capital Recovery [$/tCO2eq]"]
            + v["Fixed O&M [$/tCO2eq]"]
            + v["Variable O&M [$/tCO2eq]"]
            + v["Natural Gas Cost [$/tCO2]"]
        )

        #         # Total Cost [$/tCO2 Net Removed]
        #         v['Total Cost [$/tCO2 Net Removed]'] = (
        #             tev['Total Cost [$/tCO2 net removed]'] + dv['Total Cost [$/tCO2 net removed]']
        #         )

        self.values.update(v)

        return self

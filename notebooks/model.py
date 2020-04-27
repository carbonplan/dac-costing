HOURS_PER_DAY = 24
DAYS_PER_YEAR = 365
MILLION = 1e6

class DacComponent(object):
    
    def __init__(self, **params):

        self._params = params
        
        self.values = {}
    
    @property
    def series(self):
        if not self.values:
            self.compute()
        return pd.Series(self.values)


class BatterySection(DacComponent):
    
    def compute(self):
        
        v = {}
        
        # Battery Capacity [MWh]
        # TODO: move to params (sheets['report_data']['C64'])
        v['Battery Capacity [MWh]'] =  base_energy_req * (HOURS_PER_DAY * (1 - e_vals['Planned Capacity Factor']) )  

        # Round Trip Effciency
        v['Round Trip Effciency'] = eai_df.loc['Efficiency (Thermal or Round Trip)', 'Battery Storage']

        # Battery Capacity Needed [MWh]
        v['Battery Capacity Needed [MWh]'] = v['Battery Capacity [MWh]'] / v['Round Trip Effciency']

        # Increased [MWh]
        v['Increased [MWh]'] = v['Battery Capacity Needed [MWh]'] - v['Battery Capacity [MWh]']

        # Increased Solar/Wind Need
        v['Increased Need [MW]'] = v['Increased [MWh]'] / (HOURS_PER_DAY * e_vals['Planned Capacity Factor'])

        # Battery Capital Cost [M$]
        v['Battery Capital Cost [M$]'] = eai_df.loc['Base Plant Cost [M$]', 'Battery Storage'] * (v['Battery Capacity Needed [MWh]'] / eai_df.loc['Battery Capacity [MWhr]', 'Battery Storage']) ** scaling_factors.loc['Battery Storage', 'Scaling Factor']

        # Battery Fixed O&M [$/tCO2eq]
        v['Battery Fixed O&M [$/tCO2eq]'] = (eai_df.loc['Base Plant Annual Fixed O&M [$M]', 'Battery Storage'] * (v['Battery Capacity Needed [MWh]'] / eai_df.loc['Battery Capacity [MWhr]', 'Battery Storage']) ** scaling_factors.loc['Battery Storage', 'Scaling Factor']) * MILLION / self._params['Scale [tCO2/year]']

        # Battery Variable O&M [$/tCO2eq]
        v['Battery Variable O&M [$/tCO2eq]'] = eai_df.loc['Variable O&M [$/MWhr]', 'Battery Storage'] * v['Battery Capacity [MWh]'] / self._params['Scale [tCO2/year]'] * DAYS_PER_YEAR
        
        self.values.update(v)
        
        return self


class EnergySection(DacComponent):
    
    def __init__(self, source, battery=None, **params):
        
        self.source = source
        
        if battery is None or isinstance(battery, BatterySection):
            self.battery = battery
        else:
            raise TypeError('Expected a BatterySection ')
        
        super().__init__(**params)

    def compute(self, base_energy_requirement, params, scenario='NGCC w/ CCS'):
        '''
        Calculate the energy requirements of a single component of a DAC system (e.g. electric or thermal)

        Parameters
        ----------
        base_energy_requirement : float
            Baseline energy demand [MW].
            This parameter is refered to as `Electric Power Requierement [MW]` or `Thermal [MW]` or `Low Value Case`.

        params : dict
            Dictionary of parameters.

        scenario : str
            Generation technology.
        '''

        v = {}

        # Operational Hours [h/yr]
        operational_hours = params['DAC Capacity Factor'] * 365 * 24

        # Planned Capacity Factor
        v['Planned Capacity Factor'] = eai_df.loc['Availability', scenario]

        # Electric Power Requierement [MW]
        # aka low value case in C1
        v['Base Energy Requierement [MW]'] = base_energy_requirement

        # calcuate battery params now
        # For now, I'm just computing the battery requirements when the capacity is less than 1. Will need to revisit for C2C.
        if v['Planned Capacity Factor'] < 1:
            v.update(calc_battery_requirements(v['Base Energy Requierement [MW]'], v, self._params))

        # Plant Size [MW]
        v['Plant Size [MW]'] = (v['Base Energy Requierement [MW]'] / v['Planned Capacity Factor']) + v.get('Increased Need [MW]', 0.)

        # Overnight Cost [M$]
        scaling_key = scaling_scenarios[scenario]
        v['Overnight Cost [M$]'] = eai_df.loc['Base Plant Cost [M$]', scenario] * (v['Plant Size [MW]'] / eai_df.loc['Plant Size [MW]', scenario])**scaling_factors.loc[scaling_key, 'Scaling Factor']

        # Lead Time Multiplier
        v['Lead Time Multiplier'] = calc_lead_time_mult(self._params['WACC [%]'], int(eai_df.loc['Lead Time [Years]', scenario]))  # Note: this leads to rounding differences that are non-trivial   

        # Capital Cost [M$] 
        v['Capital Cost [M$]'] = v['Overnight Cost [M$]'] * v['Lead Time Multiplier']

        # Total Capital Cost [M$]
        v['Total Capital Cost [M$]'] = v['Capital Cost [M$]'] + v.get('Battery Capital Cost [M$]', 0.)

        # Annual Capital Recovery Factor
        annual_capital_recovery_factor = -npf.pmt(self._params['WACC [%]'], self._params['Economic Lifetime [years]'], 1)

        # Capital Recovery [$/tCO2eq]
        v['Capital Recovery [$/tCO2eq]'] = v['Total Capital Cost [M$]'] * annual_capital_recovery_factor * 1e6 / self._params['Scale [tCO2/year]']

        # Power Fixed O&M [$/tCO2eq]
        v['Power Fixed O&M [$/tCO2eq]'] = (eai_df.loc['Base Plant Annual Fixed O&M [$M]', scenario] * (v['Plant Size [MW]'] / eai_df.loc['Plant Size [MW]', scenario]) ** scaling_factors.loc[scaling_key, 'Scaling Factor']) * 1e6 / self._params['Scale [tCO2/year]']

        # Power Variable O&M [$/tCO2eq]
        v['Power Variable O&M [$/tCO2eq]'] = eai_df.loc['Variable O&M [$/MWhr]', scenario] * v['Plant Size [MW]'] * operational_hours / self._params['Scale [tCO2/year]']

        # Total Fixed O&M [$/tCO2eq]
        v['Total Fixed O&M [$/tCO2eq]'] = v['Power Fixed O&M [$/tCO2eq]'] + v.get('Battery Fixed O&M [$/tCO2eq]', 0.)

        # Total Variable O&M [$/tCO2eq]
        v['Total Variable O&M [$/tCO2eq]'] = v['Power Variable O&M [$/tCO2eq]'] + v.get('Battery Variable O&M [$/tCO2eq]', 0.)

        # Natural Gas Use [mmBTU/tCO2eq]
        heat_rate = eai_df.loc['Final Heat Rate [BTU/kWh]', scenario]
        if pd.notnull(heat_rate):
            v['Natural Gas Use [mmBTU/tCO2eq]'] = operational_hours * v['Plant Size [MW]'] * 1000 * eai_df.loc['Final Heat Rate [BTU/kWh]', scenario] / 1e6 / self._params['Scale [tCO2/year]']
        else:
            v['Natural Gas Use [mmBTU/tCO2eq]'] = 0.

        # Natural Gas Cost [$/tCO2eq]
        v['Natural Gas Cost [$/tCO2eq]'] = v['Natural Gas Use [mmBTU/tCO2eq]'] * self._params['Natural Gas Cost [$/mmBTU]']

        # Emitted tCO2eq/tCO2
        v['Emitted tCO2eq/tCO2'] = v['Natural Gas Use [mmBTU/tCO2eq]'] * eai_df.loc['Total CO2 eq [lb/mmbtu]', scenario] * 0.454 / 1000 * (1 - percent_capture.get(scenario, 0.))

        # Total Cost [$/tCO2eq gross]
        v['Total Cost [$/tCO2eq gross]'] = v['Capital Recovery [$/tCO2eq]'] + v['Total Fixed O&M [$/tCO2eq]'] + v['Total Variable O&M [$/tCO2eq]']

        # Total Cost [$/tCO2eq net]
        v['Total Cost [$/tCO2eq net]'] = v['Total Cost [$/tCO2eq gross]'] / (1 - v['Emitted tCO2eq/tCO2'])  # TODO: K62 is the tCO2eq/tCO2 field from the thermal section

        self.values.update(v)
        
        return self


class DacSection(DacComponent):
    
    def compute(self):
        '''This section needs some spot checking. Not sure if we just have some propagating round off differences or something bigger...'''

        v = {}

        # Total Overnight Capital Cost [M$]
        v['Total Capital Cost [M$]'] = self._params['Total Capex [$]']

        # Lead Time Multiplier
        v['Lead Time Multiplier'] = calc_lead_time_mult(self._params['WACC [%]'], self._params['DAC Section Lead Time [years]'])  # Note: this leads to rounding differences that are non-trivial
        v['Lead Time Multiplier'] = 1.16
        # Capital Cost (including Lead Time) [M$]
        v['Capital Cost (including Lead Time) [M$]'] = v['Total Capital Cost [M$]'] * v['Lead Time Multiplier']

        # Annual Capital Recovery Factor
        recovery_factor = -npf.pmt(self._params['WACC [%]'], self._params['Economic Lifetime [years]'], 1)

        # Capital Recovery [$/tCO2eq]
        v['Capital Recovery [$/tCO2eq]'] =  v['Total Capital Cost [M$]'] * recovery_factor * 1e6 / self._params['Scale [tCO2/year]']

        # Fixed O+M [$/tCO2eq]
        v['Fixed O+M [$/tCO2eq]'] = self._params['Fixed O+M Costs [$/tCO2]']

        # Variable O+M [$/tCO2eq]
        v['Variable O+M [$/tCO2eq]'] = self._params['Varible O+M Cost [$/tCO2]']

        # Total Cost [$/tCO2]
        v['Total Cost [$/tCO2]'] = v['Capital Recovery [$/tCO2eq]'] + v['Fixed O+M [$/tCO2eq]'] + v['Variable O+M [$/tCO2eq]']

        # Total Cost [$/tCO2 net removed]
        v['Total Cost [$/tCO2 net removed]'] = v['Total Cost [$/tCO2]'] / (1 - (ev['Emitted tCO2eq/tCO2'] + tv['Emitted tCO2eq/tCO2']))

        self.values.update(v)
        
        return self    
    

class DacModel(object):
    
    def __init__(self, electric, thermal, dac, **params):
        
        self._electric = electric
        self._thermal = thermal
        self._dac = thermal
        self._params = params
        

    def _combined_power_block_requirements(self, ev, tv):
        
        '''this is probably only useful when the electric/thermal blocks are from the same source'''

        v = {}

        # Operational Hours [h/yr]
        operational_hours = self._params['DAC Capacity Factor'] * 365 * 24

        # Power Plant Size
        v['Plant Size [MW]'] = ev['Plant Size [MW]'] + tv['Plant Size [MW]']

        # Overnight Cost [M$]
        scaling_key = scaling_scenarios[scenario]
        v['Overnight Cost [M$]'] = eai_df.loc['Base Plant Cost [M$]', scenario] * (v['Plant Size [MW]'] / eai_df.loc['Plant Size [MW]', scenario])**scaling_factors.loc[scaling_key, 'Scaling Factor']

        # Lead Time Multiplier
        v['Lead Time Multiplier'] = calc_lead_time_mult(self._params['WACC [%]'], int(eai_df.loc['Lead Time [Years]', scenario]))

        # Capital Cost [M$] 
        v['Capital Cost [M$]'] = v['Overnight Cost [M$]'] * v['Lead Time Multiplier']

        # Power Fixed O&M [$/tCO2eq]
        v['Power Fixed O&M [$/tCO2eq]'] = (eai_df.loc['Base Plant Annual Fixed O&M [$M]', scenario] * (v['Plant Size [MW]'] / eai_df.loc['Plant Size [MW]', scenario]) ** scaling_factors.loc[scaling_key, 'Scaling Factor']) * 1e6 / self._params['Scale [tCO2/year]']

        # Power Variable O&M [$/tCO2eq]
        v['Power Variable O&M [$/tCO2eq]'] = eai_df.loc['Variable O&M [$/MWhr]', scenario] * v['Plant Size [MW]'] * operational_hours / self._params['Scale [tCO2/year]']

        # Battery Capacity [MWh]
        v['Battery Capacity [MWh]'] = ev['Battery Capacity Needed [MWh]'] + tv['Battery Capacity Needed [MWh]']

        # Battery Capital Cost [M$]
        v['Battery Capital Cost [M$]'] = eai_df.loc['Base Plant Cost [M$]', 'Battery Storage'] * (v['Battery Capacity [MWh]'] / eai_df.loc['Battery Capacity [MWhr]', 'Battery Storage']) ** scaling_factors.loc['Battery Storage', 'Scaling Factor']

        # Battery Fixed O&M [$/tCO2eq]
        v['Battery Fixed O&M [$/tCO2eq]'] = (eai_df.loc['Base Plant Annual Fixed O&M [$M]', 'Battery Storage'] * (v['Battery Capacity [MWh]'] / eai_df.loc['Battery Capacity [MWhr]', 'Battery Storage']) ** scaling_factors.loc['Battery Storage', 'Scaling Factor']) *1e6 / self._params['Scale [tCO2/year]']

        # Battery Variable O&M [$/tCO2eq]
        v['Battery Variable O&M [$/tCO2eq]'] = eai_df.loc['Variable O&M [$/MWhr]', 'Battery Storage'] * v['Battery Capacity [MWh]'] / self._params['Scale [tCO2/year]'] * 365

        # Total Capital Cost [M$]
        v['Total Capital Cost [M$]'] = v['Capital Cost [M$]'] +  v['Battery Capital Cost [M$]']

        # Annual Capital Recovery Factor
        annual_capital_recovery_factor = -npf.pmt(self._params['WACC [%]'], self._params['Economic Lifetime [years]'], 1)

        # Capital Recovery [$/tCO2eq]
        v['Capital Recovery [$/tCO2eq]'] = v['Total Capital Cost [M$]'] * annual_capital_recovery_factor * 1e6 / self._params['Scale [tCO2/year]']

        # Fixed O+M [$/tCO2eq]
        v['Fixed O+M [$/tCO2eq]'] = v['Power Fixed O&M [$/tCO2eq]'] + v['Battery Fixed O&M [$/tCO2eq]']

        # Variable O+M [$/tCO2eq]
        v['Variable O+M [$/tCO2eq]'] = v['Power Variable O&M [$/tCO2eq]'] + v['Battery Variable O&M [$/tCO2eq]']

        return v

    def _total_energy_block_costs(self, ev, tv, cv):

        v = {}

        # Total Power Capacity Required [MW]
        v['Total Power Capacity Required [MW]'] = ev['Plant Size [MW]'] + tv['Plant Size [MW]']

        # Total Battery Capacity Required [MWh]
        v['Total Battery Capacity Required [MWh]'] = ev['Battery Capacity Needed [MWh]'] + tv['Battery Capacity Needed [MWh]']

        # Total Capital Cost [M$]
        v['Total Capital Cost [M$]'] = cv['Total Capital Cost [M$]']

        # Capital Recovery [$/tCO2eq]
        v['Capital Recovery [$/tCO2eq]'] = cv['Capital Recovery [$/tCO2eq]']

        # Fixed O+M [$/tCO2eq]
        v['Fixed O+M [$/tCO2eq]'] = cv['Fixed O+M [$/tCO2eq]']

        # Variable O+M [$/tCO2eq]
        v['Variable O+M [$/tCO2eq]'] = cv['Variable O+M [$/tCO2eq]']

        # NG Cost [$/tCO2eq]
        v['Natural Gas Cost [$/tCO2eq]'] = ev['Natural Gas Cost [$/tCO2eq]'] + tv['Natural Gas Cost [$/tCO2eq]']

        # Total Cost [$/tCO2]
        v['Total Cost [$/tCO2]'] = v['Capital Recovery [$/tCO2eq]'] + v['Fixed O+M [$/tCO2eq]'] + v['Variable O+M [$/tCO2eq]'] + v['Natural Gas Cost [$/tCO2eq]']

        # Net Capture [tCO2/yr]
        v['Net Capture [tCO2/yr]'] = self._params['Scale [tCO2/year]'] - self._params['Scale [tCO2/year]'] * (ev['Emitted tCO2eq/tCO2'] + tv['Emitted tCO2eq/tCO2'])

        # Total Cost [$/tCO2 net removed]
        v['Total Cost [$/tCO2 net removed]'] = v['Total Cost [$/tCO2]'] / (1 - (ev['Emitted tCO2eq/tCO2'] + tv['Emitted tCO2eq/tCO2']))    

        return v
    
    
    def compute(self):

        ev = self._electric.compute()
        tv = self._thermal.compute()
        if self._electric.kind == self.self._thermal.kind:
            cv = combined_power_block_requirements(ev, tv, self._params, scenario)
            tev = total_energy_block_costs(ev, tv, cv, self._params)
        else:
            raise ValueError('TODO: handle case with mismatched energy sources')
        
        v = {}       

        # Total Capital Cost [M$]
        v['Total Capital Cost [M$]'] = tev['Total Capital Cost [M$]'] + dv['Capital Cost (including Lead Time) [M$]']

        # Annual Capital Recovery Factor
        recovery_factor = -npf.pmt(self._params['WACC [%]'], self._params['Economic Lifetime [years]'], 1)

        # Capital Recovery [$/tCO2eq]
        # =K86*'Economic Parameters'!C6*10^6/'Report Data'!C3
        v['Capital Recovery [$/tCO2eq]'] = v['Total Capital Cost [M$]'] * recovery_factor * 1e6 / self._params['Scale [tCO2/year]']

        # Fixed O+M [$/tCO2eq]
        v['Fixed O+M [$/tCO2eq]'] = tev['Fixed O+M [$/tCO2eq]'] + dv['Fixed O+M [$/tCO2eq]']

        # Variable O+M [$/tCO2eq]
        v['Variable O+M [$/tCO2eq]'] = tev['Variable O+M [$/tCO2eq]'] + dv['Variable O+M [$/tCO2eq]']

        # Natural Gas Cost [$/tCO2]
        v['Natural Gas Cost [$/tCO2]'] = tev['Natural Gas Cost [$/tCO2eq]']

        # Total Cost [$/tCO2]
        v['Total Cost [$/tCO2]'] = v['Capital Recovery [$/tCO2eq]'] + v['Fixed O+M [$/tCO2eq]'] + v['Variable O+M [$/tCO2eq]'] + v['Natural Gas Cost [$/tCO2]']

        # Total Cost [$/tCO2 Net Removed]
        v['Total Cost [$/tCO2 Net Removed]'] = tev['Total Cost [$/tCO2 net removed]'] + dv['Total Cost [$/tCO2 net removed]']

        self.values.update(v)
        
        return self
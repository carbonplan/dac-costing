import ast
import functools

import numpy as np
import pandas as pd

from oauth2client.service_account import ServiceAccountCredentials
import gspread


def clean_string(ser):
    return pd.to_numeric(ser.str.replace('[\$,%]', '', regex=True), errors='ignore')


class MySheet(object):
    def __init__(self, sheet):
        self._sheet = sheet
    
    @functools.lru_cache()
    def __getitem__(self, key):
        '''returns a numpy array'''
        if ':' in key:
            cells = self._sheet.range(key)
            cols = [c.col for c in cells]
            rows = [c.row for c in cells]
            vals = [c.value for c in cells]
            c0 = np.min(cols)
            ncols = np.max(cols) - c0 + 1
            r0 = np.min(rows)
            nrows = np.max(rows) - r0 +1
            data = np.reshape(vals, (nrows, ncols))

            return pd.DataFrame(data)
        else:
            s = self._sheet.acell(key).value.strip('$').replace(',', '')
            if '%' in s:
                return ast.literal_eval(s.replace('%', '')) / 100.
            return ast.literal_eval(s)
    
    def __repr__(self):
        return repr(self._sheet)
    
    
def get_sheet(sheet_name):
    '''helper function to open a specific google sheet'''
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']

    credentials = ServiceAccountCredentials.from_json_keyfile_name(
             '/home/jupyter-joe@carbonplan.org-683b8/carbonplan-520763ca1ef0.json', scope) # Your json file here

    gc = gspread.authorize(credentials)

    wks = gc.open_by_key('1Dl3gJvxA-532Mnbo_hWcKelFU4TZ0NSjiort84j-6jk',)

    sheet = wks.worksheet(sheet_name)

    return MySheet(sheet)


def default_params(sheets, scenario):

    if scenario == 'low':
        return {
            # report data
            'scale': sheets['report_data']['C3'],
            'dac_capacity_factor': sheets['report_data']['C4'],
            'lead_time': sheets['report_data']['C6'],
            'overnight_capex': sheets['report_data']['C21'],  # TODO: this references another sheet
            'low_value_case': sheets['report_data']['C58'],
            'thermal_requirement': sheets['report_data']['E67'],
            'fixed_o_m_cost': sheets['report_data']['H32'],
            'variable_o_m_cost': sheets['report_data']['H33'],
            # economic parameters
            'lifetime': sheets['economic_parameters']['C4'],
            'wacc': sheets['economic_parameters']['C5'],
            'ng_cost': sheets['economic_parameters']['C7'],
            'natural_gas_energy_scaling_factor': sheets['economic_parameters']['F5'],
            # WACC
            'npv': 1,  # TODO: WACC table
        }
    else:
        return {
            # report data
            'scale': sheets['report_data']['C3'],
            'dac_capacity_factor': sheets['report_data']['C4'],
            'lead_time': sheets['report_data']['C6'],
            'overnight_capex': sheets['report_data']['D21'],  # this changed, TODO: this references another sheet
            'low_value_case': sheets['report_data']['D58'],  # this changed
            'thermal_requirement': sheets['report_data']['F67'],  # this changed 
            'fixed_o_m_cost': sheets['report_data']['I32'],  # this changed 
            'variable_o_m_cost': sheets['report_data']['I33'],  # this changed
            # economic parameters
            'lifetime': sheets['economic_parameters']['C4'],
            'wacc': sheets['economic_parameters']['C5'],
            'ng_cost': sheets['economic_parameters']['C7'],
            'natural_gas_energy_scaling_factor': sheets['economic_parameters']['F5'],
            # WACC
            'npv': 1,  # TODO: WACC table
        }
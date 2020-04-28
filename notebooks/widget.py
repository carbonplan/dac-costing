import functools

from ipywidgets import (
    HTML,
    AppLayout,
    Dropdown,
    FloatLogSlider,
    FloatSlider,
    HBox,
    IntSlider,
    Label,
    VBox,
    fixed,
    interact,
    interact_manual,
    interactive,
)


class CostWidget(object):
    def __init__(self, model):

        self._model = model

    @property
    def app(self):

        app_params = self._model._params.copy()
        cost = self._model.compute().values["Total Cost [$/tCO2]"]

        rsliders = {}
        esliders = {}
        all_sliders = {}

        labels = {
            "Scale [tCO2/year]": "Scale [tCO2/year]",
            "DAC Capacity Factor": "DAC Capacity Factor",
            "DAC Section Lead Time [years]": "DAC Section Lead Time [years]",
            "Total Capex [$]": "Overnight Capex [M$] *",
            "Electric Power Requierement [MW]": "Electric Power Requierement [MW] *",
            "Thermal [GJ/tCO2]": "Thermal [GJ/tCO2] *",
            "Fixed O+M Costs [$/tCO2]": "Fixed O+M Costs [$/tCO2]*",
            "Varible O+M Cost [$/tCO2]": "Varible O+M Cost [$/tCO2] *",
            "Economic Lifetime [years]": "Economic Lifetime [years]",
            "WACC [%]": "WACC [%]",
            "Natural Gas Cost [$/mmBTU]": "Natural Gas Cost [$/mmBTU]",
        }

        # --------- callbacks --------- #
        def update_cost(app_params):
            cost = self._model.compute().values["Total Cost [$/tCO2]"]
            result.value = f"<h1>${cost:0.2f}<h1/>"

        def on_value_change(param, change):
            app_params[param] = change["new"]
            update_cost(app_params)

        def set_defaults(change):
            case = change["new"]
            p = default_params(sheets, case.lower())  # TODO
            app_params.update(p)
            for k, v in app_params.items():
                if k in all_sliders:
                    all_sliders[k].value = v

        # --------- callbacks --------- #

        header = HTML(
            """
        <h1> DAC Cost Estimator </h1>

        <b>By NOAH MCQUEEN and JOE HAMMAN</b>

        <div style="width:800px"
            <p>
                How much does it cost to build a Direct Air Capture facility? To help answer this question, we've built a calculator that takes the most important variables that drive the cost of building and operating a DAC plant. To find out more about the fundementals and assumptions in the calcuator, check out Noah's paper...
            </p>
        </div>
        """
        )

        # presets
        presets = Dropdown(description="Preset Scenario", options=["Low", "High"], value="Low")
        presets.observe(set_defaults, names="value")

        # report data

        result = HTML(value=f"<h1>${cost:0.2f}<h1/>")
        right = VBox([HTML("<b>You can build this DAC plant for ... </b>"), result])

        rsliders["Scale [tCO2/year]"] = FloatLogSlider(
            min=1, max=12, step=0.1, value=app_params["Scale [tCO2/year]"]
        )
        rsliders["DAC Capacity Factor"] = FloatSlider(
            min=0, max=1, step=0.01, readout_format=".2%", value=app_params["DAC Capacity Factor"]
        )
        rsliders["DAC Section Lead Time [years]"] = IntSlider(
            min=1, max=6, value=app_params["DAC Section Lead Time [years]"]
        )
        rsliders["Total Capex [$]"] = FloatSlider(value=app_params["Total Capex [$]"])
        rsliders["Electric Power Requierement [MW]"] = FloatSlider(
            value=app_params["Electric Power Requierement [MW]"]
        )
        rsliders["Thermal [GJ/tCO2]"] = FloatSlider(value=app_params["Thermal [GJ/tCO2]"])
        rsliders["Fixed O+M Costs [$/tCO2]"] = FloatSlider(
            value=app_params["Fixed O+M Costs [$/tCO2]"]
        )
        rsliders["Varible O+M Cost [$/tCO2]"] = FloatSlider(
            value=app_params["Varible O+M Cost [$/tCO2]"]
        )

        for key, slider in rsliders.items():
            slider.observe(functools.partial(on_value_change, key), names="value")

        details = HTML(
            """
        <h2>Report Data</h2>

        <p>Parameters from the <em>Report Data</em> worksheet...</p>
        """
        )
        report_data = VBox(
            [details]
            + [HBox([Label(labels[k], layout={"width": "250px"}), s]) for k, s in rsliders.items()]
        )

        details = HTML(
            """
        <h2>Economic Data</h2>

        <p>Parameters from the <em>Economic Parameters</em> worksheet...</p>
        """
        )

        # economic parameters
        esliders["Economic Lifetime [years]"] = IntSlider(
            min=1, max=50, value=app_params["Economic Lifetime [years]"]
        )
        esliders["WACC [%]"] = FloatSlider(
            min=0, max=1, step=0.01, readout_format=".2%", value=app_params["WACC [%]"]
        )
        esliders["Natural Gas Cost [$/mmBTU]"] = FloatSlider(
            min=0, max=10, step=0.1, value=app_params["Natural Gas Cost [$/mmBTU]"]
        )

        for key, slider in esliders.items():
            slider.observe(functools.partial(on_value_change, key), names="value")

        econ_data = VBox(
            [details]
            + [HBox([Label(labels[k], layout={"width": "250px"}), s]) for k, s in esliders.items()]
        )

        all_sliders = {**rsliders, **esliders}

        center = VBox([presets, report_data, econ_data])

        return AppLayout(header=header, center=center, right_sidebar=right, width="900px")

def cast_params_to_ufloat(params, stdev=0.1):
    from uncertainties import ufloat

    u = {}

    for p, val in params.items():
        if isinstance(val, dict):
            u[p] = cast_params_to_ufloat(val)
        if isinstance(val, float):
            u[p] = ufloat(val, val * stdev, tag=p)
        else:
            u[p] = val
    return u

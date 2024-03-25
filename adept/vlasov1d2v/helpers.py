#  Copyright (c) Ergodic LLC 2023
#  research@ergodic.io
from typing import Dict
import os

from time import time


import numpy as np
import xarray, mlflow
from astropy import units as u, constants as const
from jax import numpy as jnp
from diffrax import ODETerm, SubSaveAt
from matplotlib import pyplot as plt

from adept.vlasov1d2v.integrator import VlasovMaxwell, Stepper
from adept.vlasov1d2v.storage import store_f, store_fields, get_save_quantities
from adept.tf1d.pushers import get_envelope
from adept.vfp1d.helpers import write_units
from adept.vlasov1d.helpers import get_1D_n_and_T_profile, process_driver_units


def _initialize_distribution_(
    nx: int,
    nv: int,
    v0=0.0,
    m=2.0,
    vth=1.0,
    vmax=6.0,
    n_prof=np.ones(1),
    T_prof=np.ones(1),
    noise_val=0.0,
    noise_seed=42,
    noise_type="Uniform",
):
    """

    :param nxs:
    :param nvs:
    :param n_prof:
    :param vmax:
    :return:
    """
    """
    Initializes a Maxwell-Boltzmann distribution

    :param nx: size of grid in x (single int)
    :param nv: size of grid in v (single int)
    :param vmax: maximum absolute value of v (single float)
    :return:
    """

    # noise_generator = np.random.default_rng(seed=noise_seed)

    dv = 2.0 * vmax / nv
    vax = np.linspace(-vmax + dv / 2.0, vmax - dv / 2.0, nv)

    # alpha = np.sqrt(3.0 * gamma_3_over_m(m) / gamma_5_over_m(m))
    # cst = m / (4 * np.pi * alpha**3.0 * gamma(3.0 / m))

    # single_dist = -(np.power(np.abs((vax[None, :] - v0) / alpha / np.sqrt(T0)), m))

    # single_dist = np.exp(single_dist)
    # single_dist = np.exp(-(vax[None, :, None] ** 2.0 + vax[None, None, :] ** 2.0) / 2 / T0)

    # # for ix in range(nx):
    # f = np.repeat(single_dist, nx, axis=0)
    # # normalize
    # f = f / np.sum(np.sum(f, axis=2), axis=1)[:, None, None] / dv**2.0

    f = np.zeros([nx, nv, nv])
    for ix, (tn, tt) in enumerate(zip(n_prof, T_prof)):
        # cst = m / (4 * np.pi * alpha**3.0 * gamma_3_over_m(m)) / (2 * tt * vth**2.0) ** 1.5
        # single_dist = cst * np.exp(-(np.power(np.abs((vax[None, :] - v0) / (alpha * vth * np.sqrt(2 * tt))), m)))
        single_dist = (
            1.0
            / (2 * np.pi * tt * (vth**2.0))
            * np.exp(-(vax[None, :, None] ** 2.0 + vax[None, None, :] ** 2.0) / (2 * tt * (vth**2.0)))
        )
        # single_dist = single_dist / np.sum(single_dist) / dv**2.0
        f[ix, :] = tn * single_dist

    # if noise_type.casefold() == "uniform":
    #     f = (1.0 + noise_generator.uniform(-noise_val, noise_val, nx)[:, None]) * f
    # elif noise_type.casefold() == "gaussian":
    #     f = (1.0 + noise_generator.normal(-noise_val, noise_val, nx)[:, None]) * f

    return f, vax


def _initialize_total_distribution_(cfg, cfg_grid):
    params = cfg["density"]
    prof_total = {"n": np.zeros([cfg_grid["nx"]]), "T": np.zeros([cfg_grid["nx"]])}
    f = np.zeros([cfg_grid["nx"], cfg_grid["nv"], cfg_grid["nv"]])
    species_found = False
    for name, species_params in cfg["density"].items():
        if name.startswith("species-"):
            profs = {}
            v0 = species_params["v0"]
            m = species_params["m"]
            if name in params:
                if "v0" in params[name]:
                    v0 = params[name]["v0"]

                if "m" in params[name]:
                    m = params[name]["m"]

            profs = get_1D_n_and_T_profile(profs, species_params, cfg)
            profs["n"] *= (cfg["units"]["derived"]["ne"] / cfg["units"]["derived"]["n0"]).value

            # Distribution function
            temp_f, _ = _initialize_distribution_(
                nx=int(cfg_grid["nx"]),
                nv=int(cfg_grid["nv"]),
                v0=v0,
                m=m,
                vth=cfg_grid["beta"],
                vmax=cfg_grid["vmax"],
                n_prof=profs["n"],
                T_prof=profs["T"],
                noise_val=species_params["noise_val"],
                noise_seed=int(species_params["noise_seed"]),
                noise_type=species_params["noise_type"],
            )
            actual_n = np.sum(np.sum(temp_f, axis=2), axis=1) * cfg_grid["dv"] ** 2.0
            prof_total["n"] += actual_n
            f += temp_f
            species_found = True

        else:
            pass

    if not species_found:
        raise ValueError("No species found! Check the config")

    return f, prof_total["n"]


def get_derived_quantities(cfg: Dict) -> Dict:
    """
    This function just updates the config with the derived quantities that are only integers or strings.

    This is run prior to the log params step

    :param cfg_grid:
    :return:
    """
    cfg_grid = cfg["grid"]

    cfg_grid["dx"] = cfg_grid["xmax"] / cfg_grid["nx"]
    cfg_grid["vmax"] = (
        cfg_grid["vmax"]
        * np.sqrt(
            (u.Quantity(cfg["units"]["reference electron temperature"]) / (const.m_e * const.c**2.0)).to("")
        ).value
    )
    cfg_grid["dv"] = 2.0 * cfg_grid["vmax"] / cfg_grid["nv"]

    if len(cfg["drivers"]["ey"].keys()) > 0:
        print("overriding dt to ensure wave solver stability")
        cfg_grid["dt"] = 0.95 * cfg_grid["dx"] / cfg["units"]["derived"]["c_light"]

    cfg_grid["nt"] = int(cfg_grid["tmax"] / cfg_grid["dt"] + 1)

    if cfg_grid["nt"] > 1e6:
        cfg_grid["max_steps"] = int(1e6)
        print(r"Only running $10^6$ steps")
    else:
        cfg_grid["max_steps"] = cfg_grid["nt"] + 4

    cfg_grid["tmax"] = cfg_grid["dt"] * cfg_grid["nt"]
    cfg["grid"] = cfg_grid

    return cfg


def get_solver_quantities(cfg: Dict) -> Dict:
    """
    This function just updates the config with the derived quantities that are arrays

    This is run after the log params step

    :param cfg_grid:
    :return:
    """
    cfg_grid = cfg["grid"]

    cfg_grid = {
        **cfg_grid,
        **{
            "x": jnp.linspace(
                cfg_grid["xmin"] + cfg_grid["dx"] / 2, cfg_grid["xmax"] - cfg_grid["dx"] / 2, cfg_grid["nx"]
            ),
            "t": jnp.linspace(0, cfg_grid["tmax"], cfg_grid["nt"]),
            "v": jnp.linspace(
                -cfg_grid["vmax"] + cfg_grid["dv"] / 2, cfg_grid["vmax"] - cfg_grid["dv"] / 2, cfg_grid["nv"]
            ),
            "kx": jnp.fft.fftfreq(cfg_grid["nx"], d=cfg_grid["dx"]) * 2.0 * np.pi,
            "kxr": jnp.fft.rfftfreq(cfg_grid["nx"], d=cfg_grid["dx"]) * 2.0 * np.pi,
            "kv": jnp.fft.fftfreq(cfg_grid["nv"], d=cfg_grid["dv"]) * 2.0 * np.pi,
            "kvr": jnp.fft.rfftfreq(cfg_grid["nv"], d=cfg_grid["dv"]) * 2.0 * np.pi,
        },
    }

    # config axes
    one_over_kx = np.zeros_like(cfg_grid["kx"])
    one_over_kx[1:] = 1.0 / cfg_grid["kx"][1:]
    cfg_grid["one_over_kx"] = jnp.array(one_over_kx)

    one_over_kxr = np.zeros_like(cfg_grid["kxr"])
    one_over_kxr[1:] = 1.0 / cfg_grid["kxr"][1:]
    cfg_grid["one_over_kxr"] = jnp.array(one_over_kxr)

    # velocity axes
    one_over_kv = np.zeros_like(cfg_grid["kv"])
    one_over_kv[1:] = 1.0 / cfg_grid["kv"][1:]
    cfg_grid["one_over_kv"] = jnp.array(one_over_kv)

    one_over_kvr = np.zeros_like(cfg_grid["kvr"])
    one_over_kvr[1:] = 1.0 / cfg_grid["kvr"][1:]
    cfg_grid["one_over_kvr"] = jnp.array(one_over_kvr)

    cfg_grid["nuprof"] = 1.0
    # get_profile_with_mask(cfg["nu"]["time-profile"], t, cfg["nu"]["time-profile"]["bump_or_trough"])
    cfg_grid["ktprof"] = 1.0
    # get_profile_with_mask(cfg["krook"]["time-profile"], t, cfg["krook"]["time-profile"]["bump_or_trough"])

    cfg_grid["kprof"] = np.ones(cfg_grid["nx"])
    # get_profile_with_mask(cfg["krook"]["space-profile"], xs, cfg["krook"]["space-profile"]["bump_or_trough"])

    cfg_grid["x_a"] = np.concatenate(
        [[cfg_grid["x"][0] - cfg_grid["dx"]], cfg_grid["x"], [cfg_grid["x"][-1] + cfg_grid["dx"]]]
    )

    return cfg_grid


def init_state(cfg: Dict, td=None) -> Dict:
    """
    This function initializes the state

    :param cfg:
    :return:
    """
    f, ne_prof = _initialize_total_distribution_(cfg, cfg["grid"])

    state = {}
    for species in ["electron"]:
        state[species] = f

    for field in ["e", "de"]:
        state[field] = jnp.zeros(cfg["grid"]["nx"])

    for field in ["a", "da", "prev_a"]:
        state[field] = jnp.zeros(cfg["grid"]["nx"] + 2)  # need boundary cells

    state["Z"] = jnp.ones(cfg["grid"]["nx"]) * cfg["units"]["Z"]
    state["ni"] = ne_prof / cfg["units"]["Z"]

    return state


def get_diffeqsolve_quants(cfg):
    cfg = process_driver_units(cfg)

    return dict(
        terms=ODETerm(VlasovMaxwell(cfg)),
        solver=Stepper(),
        saveat=dict(subs={k: SubSaveAt(ts=v["t"]["ax"], fn=v["func"]) for k, v in cfg["save"].items()}),
        args={"drivers": cfg["drivers"], "terms": cfg["terms"]},
    )


def post_process(result, cfg: Dict, td: str):
    t0 = time()
    os.makedirs(os.path.join(td, "plots"), exist_ok=True)
    os.makedirs(os.path.join(td, "plots", "fields"), exist_ok=True)
    os.makedirs(os.path.join(td, "plots", "fields", "lineouts"), exist_ok=True)
    os.makedirs(os.path.join(td, "plots", "fields", "logplots"), exist_ok=True)

    os.makedirs(os.path.join(td, "plots", "scalars"), exist_ok=True)

    binary_dir = os.path.join(td, "binary")
    os.makedirs(binary_dir)
    # merge
    # flds_paths = [os.path.join(flds_path, tf) for tf in flds_list]
    # arr = xarray.open_mfdataset(flds_paths, combine="by_coords", parallel=True)
    for k in result.ys.keys():
        if k.startswith("field"):
            fields_xr = store_fields(cfg, binary_dir, result.ys[k], result.ts[k], k)
            t_skip = int(fields_xr.coords["t"].data.size // 8)
            t_skip = t_skip if t_skip > 1 else 1
            tslice = slice(0, -1, t_skip)

            for nm, fld in fields_xr.items():
                fld.plot()
                plt.savefig(os.path.join(td, "plots", "fields", f"spacetime-{nm[7:]}.png"), bbox_inches="tight")
                plt.close()

                np.log10(np.abs(fld)).plot()
                plt.savefig(
                    os.path.join(td, "plots", "fields", "logplots", f"spacetime-log-{nm[7:]}.png"), bbox_inches="tight"
                )
                plt.close()

                fld[tslice].T.plot(col="t", col_wrap=4)
                plt.savefig(os.path.join(td, "plots", "fields", "lineouts", f"{nm[7:]}.png"), bbox_inches="tight")
                plt.close()

        elif k.startswith("default"):
            scalars_xr = xarray.Dataset(
                {k: xarray.DataArray(v, coords=(("t", result.ts["default"]),)) for k, v in result.ys["default"].items()}
            )
            scalars_xr.to_netcdf(os.path.join(binary_dir, f"scalars-t={round(scalars_xr.coords['t'].data[-1], 4)}.nc"))

            for nm, srs in scalars_xr.items():
                fig, ax = plt.subplots(1, 2, figsize=(10, 4), tight_layout=True)
                srs.plot(ax=ax[0])
                ax[0].grid()
                np.log10(np.abs(srs)).plot(ax=ax[1])
                ax[1].grid()
                ax[1].set_ylabel("$log_{10}$(|" + nm + "|)")
                fig.savefig(os.path.join(td, "plots", "scalars", f"{nm}.png"), bbox_inches="tight")
                plt.close()

    f_xr = store_f(cfg, result.ts, binary_dir, result.ys)

    mlflow.log_metrics({"postprocess_time_min": round((time() - t0) / 60, 3)})

    return {"fields": fields_xr, "dists": f_xr, "scalars": scalars_xr}

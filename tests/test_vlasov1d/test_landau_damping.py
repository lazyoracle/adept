#  Copyright (c) Ergodic LLC 2023
#  research@ergodic.io
import yaml, pytest

import numpy as np
from jax.config import config

config.update("jax_enable_x64", True)
# config.update("jax_disable_jit", True)

import mlflow

from theory import electrostatic
from utils.runner import run


def _modify_defaults_(defaults, rng):
    rand_k0 = np.round(rng.uniform(0.25, 0.4), 3)

    root = electrostatic.get_roots_to_electrostatic_dispersion(1.0, 1.0, rand_k0)

    defaults["drivers"]["ex"]["0"]["k0"] = float(rand_k0)
    defaults["drivers"]["ex"]["0"]["w0"] = float(np.real(root))
    xmax = float(2.0 * np.pi / rand_k0)
    defaults["grid"]["xmax"] = xmax
    defaults["mlflow"]["experiment"] = "vlasov1d-test-resonance"

    return defaults, root


@pytest.mark.parametrize("real_or_imag", ["real", "imag"])
def test_single_resonance(real_or_imag):
    with open("tests/test_vlasov1d/configs/resonance.yaml", "r") as file:
        defaults = yaml.safe_load(file)

    # modify config
    rng = np.random.default_rng()
    mod_defaults, root = _modify_defaults_(defaults, rng)

    actual_damping_rate = 2 * np.imag(root)
    actual_resonance = np.real(root)
    # run
    mlflow.set_experiment(mod_defaults["mlflow"]["experiment"])
    # modify config
    with mlflow.start_run(run_name=mod_defaults["mlflow"]["run"]) as mlflow_run:
        result, datasets = run(mod_defaults)
        efs = result.ys["fields"]["e"]
        ek1 = 2.0 / mod_defaults["grid"]["nx"] * np.fft.fft(efs, axis=1)[:, 1]

        if real_or_imag == "imag":
            frslc = slice(-100, -50)
            measured_damping_rate = np.mean(
                np.gradient(ek1[frslc], (result.ts["fields"][1] - result.ts["fields"][0])) / ek1[frslc]
            )
            print(
                f"Landau Damping rate check \n"
                f"measured: {np.round(measured_damping_rate, 5)}, "
                f"actual: {np.round(actual_damping_rate, 5)}, "
            )
            mlflow.log_metrics(
                {
                    "actual damping rate": float(actual_damping_rate),
                    "measured damping rate": float(measured_damping_rate),
                }
            )

            np.testing.assert_almost_equal(measured_damping_rate, actual_damping_rate, decimal=2)
        else:
            env, freq = electrostatic.get_nlfs(ek1, result.ts["fields"][1] - result.ts["fields"][0])
            frslc = slice(-480, -240)
            print(
                f"Frequency check \n"
                f"measured: {np.round(np.mean(freq[frslc]), 5)}, "
                f"desired: {np.round(actual_resonance, 5)}, "
            )
            measured_resonance = np.mean(freq[frslc])
            np.testing.assert_almost_equal(measured_resonance, actual_resonance, decimal=2)


if __name__ == "__main__":
    test_single_resonance(real_or_imag="real")

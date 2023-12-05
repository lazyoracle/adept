#  Copyright (c) Ergodic LLC 2023
#  research@ergodic.io
from functools import partial
from typing import Dict, Tuple

import numpy as np
from jax import numpy as jnp

from adept.vlasov2d.solver.tridiagonal import TridiagonalSolver


class Collisions:
    def __init__(self, cfg):
        self.cfg = cfg
        self.fp = self.__init_fp_operator__()
        self.krook = Krook(self.cfg)
        self.td_solver = TridiagonalSolver(self.cfg)

    def __init_fp_operator__(self):
        # if self.cfg["solver"]["fp_operator"] == "lenard_bernstein":
        #     return LenardBernstein(self.cfg)
        # elif self.cfg["solver"]["fp_operator"] == "dougherty":
        return Dougherty(self.cfg)
        # else:
        #     raise NotImplementedError

    def __call__(self, nu_fp: jnp.float64, nu_K: jnp.float64, f: jnp.ndarray, dt: jnp.float64) -> jnp.ndarray:
        if np.any(self.cfg["nuee"] > 0.0):
            # The three diagonals representing collision operator for all x
            cee_a, cee_b, cee_c = self.fp(nu=nu_fp, f_xv=f, dt=dt)
            # Solve over all x
            f = self.td_solver(cee_a, cee_b, cee_c, f)

        # if (np.any(self.cfg["grid"]["kr_prof"] > 0.0)) and (np.any(self.cfg["grid"]["kt_prof"] > 0.0)):
        #     f = self.krook(nu_K, f, dt)
        return f


class Krook:
    def __init__(self, cfg):
        self.cfg = cfg
        f_mx = np.exp(-self.cfg["grid"]["v"][None, :] ** 2.0 / 2.0)
        self.f_mx = f_mx / np.trapz(f_mx, dx=self.cfg["grid"]["dv"], axis=1)[:, None]
        self.dv = self.cfg["grid"]["dv"]
        self.vx_moment = partial(jnp.trapz, axis=1, dx=self.dv)

    def __call__(self, nu_K, f_xv, dt) -> jnp.ndarray:
        nu_Kxdt = dt * nu_K[:, None]
        exp_nuKxdt = jnp.exp(-nu_Kxdt)
        n_prof = self.vx_moment(f_xv)

        return f_xv * exp_nuKxdt + n_prof[:, None] * self.f_mx * (1.0 - exp_nuKxdt)


class LenardBernstein:
    def __init__(self, cfg):
        self.cfg = cfg
        self.v = self.cfg["grid"]["v"]
        self.dv = self.cfg["grid"]["dv"]
        self.ones = jnp.ones((self.cfg["grid"]["nx"], self.cfg["grid"]["nv"]))
        self.vx_moment = partial(jnp.trapz, axis=1, dx=self.dv)

    def __call__(
        self, nu: jnp.float64, f_xv: jnp.ndarray, dt: jnp.float64
    ) -> Tuple[jnp.ndarray, jnp.ndarray, jnp.ndarray]:
        """

        :param nu:
        :param f_xv:
        :param dt:
        :return:
        """

        v0t_sq = self.vx_moment(f_xv * self.v[None, :] ** 2.0)
        a = nu * dt * (-v0t_sq[:, None] / self.dv**2.0 + jnp.roll(self.v, 1)[None, :] / 2 / self.dv)
        b = 1.0 + nu * dt * self.ones * (2.0 * v0t_sq[:, None] / self.dv**2.0)
        c = nu * dt * (-v0t_sq[:, None] / self.dv**2.0 - jnp.roll(self.v, -1)[None, :] / 2 / self.dv)
        return a, b, c


class Dougherty:
    def __init__(self, cfg):
        self.cfg = cfg
        self.v = self.cfg["grid"]["v"]
        self.dv = self.cfg["grid"]["dv"]
        self.ones = jnp.ones((self.cfg["grid"]["nx"], self.cfg["grid"]["nv"]))
        self.vx_moment = partial(jnp.trapz, axis=1, dx=self.dv)

    def __call__(
        self, nu: jnp.float64, f_xv: jnp.ndarray, dt: jnp.float64
    ) -> Tuple[jnp.ndarray, jnp.ndarray, jnp.ndarray]:
        """

        :param nu:
        :param f_xv:
        :param dt:
        :return:
        """

        vbar = self.vx_moment(f_xv * self.v[None, :])
        v0t_sq = self.vx_moment(f_xv * (self.v[None, :] - vbar[:, None]) ** 2.0)

        a = (
            nu
            * dt
            * (-v0t_sq[:, None] / self.dv**2.0 + (jnp.roll(self.v, 1)[None, :] - vbar[:, None]) / 2.0 / self.dv)
        )
        b = 1.0 + nu * dt * self.ones * (2.0 * v0t_sq[:, None] / self.dv**2.0)
        c = (
            nu
            * dt
            * (-v0t_sq[:, None] / self.dv**2.0 - (jnp.roll(self.v, -1)[None, :] - vbar[:, None]) / 2.0 / self.dv)
        )
        return a, b, c

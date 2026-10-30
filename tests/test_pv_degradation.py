"""Panel warranty milestones through the actual lifetime dispatch kernel."""
import test_support
import numpy as np
import pytest

from feasibility import assumptions
from feasibility.physical_dispatch import dispatch_lifetime


def test_aiko_degradation_defaults():
    settings = assumptions.defaults()
    assert settings['pv_first_year_loss'] == .01
    assert settings['pv_annual_fade'] == .0035


@pytest.mark.parametrize('first_loss,annual_loss,horizon,milestones', [
    (.01, .0035, 30, {1: .99, 2: .9865, 25: .906, 30: .8885}),
    (.02, .005, 30, {1: .98, 10: .935}),
    (.10, .03, 40, {30: .03, 36: 0.}),
])
def test_lifetime_pv_degradation(first_loss, annual_loss, horizon, milestones):
    pv = np.ones((1, 24))
    zero = np.zeros_like(pv)
    annual = dispatch_lifetime(
        pv, zero, zero, np.ones(pv.shape, dtype=np.bool_), zero,
        horizon, 100, 100., 0., 0., 0., 1.,
        first_loss, annual_loss, 0., 0., 0,
    )
    for year, fraction in milestones.items():
        assert annual[year - 1, 0, 1] / pv.sum() == pytest.approx(fraction)
    assert np.all(annual[:, 0, 1] >= 0.)

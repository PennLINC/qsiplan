"""The method axes, the capability registries, and the bridge from qsiprep's
config vocabulary (``hmc_model``/``pepolar_method``) to a MethodSelection."""

import itertools

import pytest

from qsiplan.cohort import COHORT_METHODS
from qsiplan.methods import (
    HMC_CAPABILITIES,
    SDC_CAPABILITIES,
    HmcMethod,
    MethodSelection,
    SdcTool,
    selection_for_config,
)
from qsiplan.models import CorrectionMethod

LEGACY_HMC_MODELS = ('eddy', 'tortoise', '3dSHORE', 'tensor', 'none')
LEGACY_PEPOLAR_METHODS = ('TOPUP', 'DRBUDDI', 'TOPUP+DRBUDDI')


@pytest.mark.parametrize(
    ('hmc_model', 'hmc', 'shoreline_model'),
    [
        ('eddy', HmcMethod.EDDY, None),
        ('tortoise', HmcMethod.TORTOISE, None),
        ('shoreline', HmcMethod.SHORELINE, '3dshore'),
        ('3dSHORE', HmcMethod.SHORELINE, '3dshore'),
        ('tensor', HmcMethod.SHORELINE, 'tensor'),
        ('none', HmcMethod.SHORELINE, 'none'),
    ],
)
def test_hmc_vocabulary_old_and_new(hmc_model, hmc, shoreline_model):
    selection = selection_for_config(hmc_model, 'auto')
    assert selection.hmc is hmc
    assert selection.shoreline_model == shoreline_model


@pytest.mark.parametrize('pepolar_method', [None, 'auto', 'AUTO'])
def test_auto_resolves_per_hmc_method(pepolar_method):
    assert selection_for_config('eddy', pepolar_method).pepolar_tools == (SdcTool.TOPUP,)
    assert selection_for_config('shoreline', pepolar_method).pepolar_tools == (SdcTool.DRBUDDI,)
    assert selection_for_config('tortoise', pepolar_method).pepolar_tools == (SdcTool.DRBUDDI,)


def test_pepolar_vocabulary_old_and_new():
    for value in ('TOPUP+DRBUDDI', 'topup+drbuddi'):
        assert selection_for_config('eddy', value).pepolar_tools == (
            SdcTool.TOPUP,
            SdcTool.DRBUDDI,
        )
    assert selection_for_config('eddy', 'drbuddi').pepolar_tools == (SdcTool.DRBUDDI,)


def test_legacy_values_round_trip():
    for hmc_model, pepolar_method in itertools.product(LEGACY_HMC_MODELS, LEGACY_PEPOLAR_METHODS):
        selection = selection_for_config(hmc_model, pepolar_method)
        rebuilt = selection_for_config(selection.legacy_hmc_model, selection.legacy_pepolar_method)
        assert rebuilt == selection


def test_unknown_values_raise():
    with pytest.raises(ValueError, match='hmc'):
        selection_for_config('bogus', 'TOPUP')
    with pytest.raises(ValueError, match='pepolar'):
        selection_for_config('eddy', 'bogus')


def test_selection_validation():
    with pytest.raises(ValueError, match='shoreline_model'):
        MethodSelection(
            hmc=HmcMethod.EDDY, pepolar_tools=(SdcTool.TOPUP,), shoreline_model='3dshore'
        )
    with pytest.raises(ValueError, match='shoreline_model'):
        MethodSelection(hmc=HmcMethod.SHORELINE, pepolar_tools=(SdcTool.DRBUDDI,))
    with pytest.raises(ValueError, match='model'):
        MethodSelection(
            hmc=HmcMethod.SHORELINE, pepolar_tools=(SdcTool.DRBUDDI,), shoreline_model='dti'
        )
    with pytest.raises(ValueError, match='Duplicate'):
        MethodSelection(hmc=HmcMethod.EDDY, pepolar_tools=(SdcTool.TOPUP, SdcTool.TOPUP))
    with pytest.raises(ValueError, match='not a PEPOLAR tool'):
        MethodSelection(hmc=HmcMethod.EDDY, pepolar_tools=(SdcTool.FIELDMAP,))


@pytest.mark.parametrize(
    ('hmc', 'sdc', 'expected_label'),
    [
        ('eddy', 'topup', 'eddy + TOPUP'),
        ('eddy', 'topup+drbuddi', 'eddy + TOPUP→DRBUDDI'),
        ('tortoise', 'drbuddi', 'TORTOISE + DRBUDDI'),
        ('shoreline', 'drbuddi', 'SHORELine + DRBUDDI'),
    ],
)
def test_selection_labels_name_the_method_and_tool_chain(hmc, sdc, expected_label):
    assert selection_for_config(hmc, sdc).label() == expected_label


def test_cohort_labels_are_the_selections_own():
    for _key, label, selection in COHORT_METHODS:
        assert isinstance(selection, MethodSelection)
        assert label == selection.label()


def test_registries_are_total():
    assert set(HMC_CAPABILITIES) == set(HmcMethod)
    assert set(SDC_CAPABILITIES) == set(SdcTool)
    consumable = frozenset().union(*(cap.consumes for cap in SDC_CAPABILITIES.values()))
    assert consumable == frozenset(CorrectionMethod)


def test_integrated_pepolar_is_a_capable_tool():
    for capabilities in HMC_CAPABILITIES.values():
        if capabilities.integrated_pepolar is not None:
            assert capabilities.integrated_pepolar in capabilities.pepolar_tools

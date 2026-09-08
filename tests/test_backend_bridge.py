"""The legacy backend names are a boundary vocabulary, never an internal one.

``'fsl'``/``'tortoise'``/``'mixed'`` predate :class:`~qsiplan.methods.MethodSelection`
and are a lossy projection of it (every non-eddy method collapses to
``'tortoise'``). They survive only at the public boundary, normalized once by
:func:`~qsiplan.methods.as_selection`; everything behind that boundary reasons
about the selection. These tests pin the bridge - a string and its canonical
selection must be indistinguishable to every public entry point - and pin
that decomposition is driven by the capability table rather than a name, using
the one case the string projection used to smuggle through: SHORELine.
"""

import pytest
from grouping_scenarios import load_scenario

from qsiplan import (
    BACKENDS,
    canonical_selection,
    check_backend,
    concatenation_scheme,
    to_preproc_units,
)
from qsiplan.cohort import COHORT_METHODS
from qsiplan.methods import (
    HMC_CAPABILITIES,
    HmcMethod,
    MethodSelection,
    as_selection,
    selection_for_config,
)


def test_as_selection_is_the_single_bridge():
    selection = selection_for_config('shoreline', 'drbuddi')
    assert as_selection(selection) is selection  # a selection passes through untouched
    for backend in BACKENDS:
        assert as_selection(backend) == canonical_selection(backend)
    with pytest.raises(ValueError, match='Unknown backend'):
        as_selection('bogus')


@pytest.mark.parametrize('backend', BACKENDS)
@pytest.mark.parametrize('scenario', ['hcp_style', 'cross_axis_b0field', 'partial_pair'])
def test_public_entry_points_treat_a_name_and_its_selection_identically(
    tmp_path, scenario, backend
):
    grouping = load_scenario(scenario, tmp_path, strict=False)
    selection = canonical_selection(backend)
    assert check_backend(grouping, backend) == check_backend(grouping, selection)
    assert to_preproc_units(grouping, backend) == to_preproc_units(grouping, selection)
    assert concatenation_scheme(grouping, backend) == concatenation_scheme(grouping, selection)


def test_decomposition_is_driven_by_the_capability_table_not_a_name(tmp_path):
    # SHORELine has no legacy backend name of its own (it projects to
    # 'tortoise'); it must still decompose a cross-axis PEPOLAR unit per blip
    # pair purely because its capability row says so.
    grouping = load_scenario('cross_axis_b0field', tmp_path, strict=False)
    shoreline = selection_for_config('shoreline', 'drbuddi')
    eddy = selection_for_config('eddy', 'topup')
    assert HMC_CAPABILITIES[HmcMethod.SHORELINE].decomposes_pepolar_pairs
    assert not HMC_CAPABILITIES[HmcMethod.EDDY].decomposes_pepolar_pairs
    assert len(to_preproc_units(grouping, shoreline)) > len(to_preproc_units(grouping, eddy))


def test_cohort_labels_are_the_selections_own():
    for _key, label, selection in COHORT_METHODS:
        assert isinstance(selection, MethodSelection)
        assert label == selection.label()

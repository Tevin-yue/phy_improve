# -*- coding: utf-8 -*-

"""Tests of history structure."""

#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

import os

import numpy as np
from numpy.testing import assert_array_equal
from pytest import raises

from ....datasets.mock import artificial_spike_clusters
from .._history import History


#------------------------------------------------------------------------------
# Tests
#------------------------------------------------------------------------------

def test_history():
    history = History()
    assert history.current_item is None

    def _assert_current(item):
        assert id(history.current_item) == id(item)

    item0 = np.zeros(3)
    item1 = np.ones(4)
    item2 = 2 * np.ones(5)

    history.add(item0)
    _assert_current(item0)

    history.add(item1)
    _assert_current(item1)

    assert history.back() is not None
    _assert_current(item0)

    assert history.forward() is not None
    _assert_current(item1)

    assert history.forward() is None
    _assert_current(item1)

    assert history.back() is not None
    _assert_current(item0)
    assert history.back() is None
    assert history.back() is None
    assert len(history) == 3

    history.add(item2)
    assert len(history) == 2
    _assert_current(item2)
    assert history.forward() is None
    assert history.back() is None
    assert history.back() is None


def test_iter_history():
    history = History()

    # Wrong arguments to iter().
    assert len([_ for _ in history.iter(0, 0)]) == 0
    assert len([_ for _ in history.iter(2, 1)]) == 0

    item0 = np.zeros(3)
    item1 = np.ones(4)
    item2 = 2 * np.ones(5)

    history.add(item0)
    history.add(item1)
    history.add(item2)

    for i, item in enumerate(history):
        # Assert item<i>
        if i > 0:
            assert id(item) == id(locals()['item{0:d}'.format(i - 1)])

    for i, item in enumerate(history.iter(1, 2)):
        assert i == 0
        # Assert item<i>
        assert history.current_position == 3
        assert id(item) == id(locals()['item{0:d}'.format(i)])

    for i, item in enumerate(history.iter(2, 3)):
        assert i == 0
        # Assert item<i>
        assert history.current_position == 3
        assert id(item) == id(locals()['item{0:d}'.format(i + 1)])

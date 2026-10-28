# -*- coding: utf-8 -*-
"""Contract test for utility.util.gen_res_dict. Runs under pytest or plain python."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utility.util import gen_res_dict


def test_gen_res_dict_basic():
    assert gen_res_dict(1, 'x') == {'ret': 1, 'msg': 'x'}


def test_gen_res_dict_returns_new_dict_and_does_not_mutate_input():
    d = {'a': 1}
    r = gen_res_dict(0, 'ok', d)
    assert r == {'a': 1, 'ret': 0, 'msg': 'ok'}
    assert d == {'a': 1}, "gen_res_dict must not mutate the caller's dict"


def test_gen_res_dict_no_mutable_default_leak():
    # two calls with no dt argument must not share state
    assert gen_res_dict(1, 'x') == {'ret': 1, 'msg': 'x'}
    assert gen_res_dict(2, 'y') == {'ret': 2, 'msg': 'y'}


if __name__ == '__main__':
    test_gen_res_dict_basic()
    test_gen_res_dict_returns_new_dict_and_does_not_mutate_input()
    test_gen_res_dict_no_mutable_default_leak()
    print('utility.util.gen_res_dict OK')
# -*- coding: utf-8 -*-

def gen_res_dict(ret: int, msg: str, dt=None):
    d = {}
    if dt:
        d.update(dt)
    d['ret'] = ret
    d['msg'] = msg
    return d
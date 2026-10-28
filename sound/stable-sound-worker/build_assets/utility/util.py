# -*- coding: utf-8 -*-
import numpy as np

def genid(userid:int, timestamp:int):
    seed = np.random.randint(low=1, high=100)
    id = ((int(userid<<10) ^ int(timestamp)) << 2) | int(seed) 
    #print(f'id:{id}, userid:{userid}, timestamp:{timestamp}')
    return id

def gen_res_dict(ret: int, msg: str, dt={}):
    d = {}
    d.update(dt)
    dt['ret'] = ret
    dt['msg'] = msg
    return dt

if __name__ == "__main__":
    userid = 1
    timestamp = time.time()
    id = genid(userid, timestamp)
    print(f'id:{id}')

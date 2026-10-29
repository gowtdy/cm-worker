from utility.module_enum import ModuleType

def can_use_service(domain: str, generate_module: ModuleType, is_login: bool, day_count: int, month_count: int) -> tuple[int, str]:
    ret = 0
    msg = 'ok'
    if domain == "caluma.ai":
        ret, msg = caluma_use_strategy(generate_module, is_login, day_count, month_count)
    else:
        pass # todo 其他域名的限制
    return ret, msg

def caluma_use_strategy(generate_module: ModuleType, is_login: bool, day_count: int, month_count: int) -> tuple[int, str]:
    """
    验证用户是否可以继续使用服务
    Args:
        generate_module: 生成模块
        is_login: 是否登录
        day_count: 当天生成次数
        month_count: 当月生成次数
    Returns:
        tuple[int, str]: (错误码, 错误信息)。错误码为0表示验证通过, 1表示需要登录, 2表示需要订阅(免费次数超出限制)
    """
    ret = 0
    msg = 'ok'
    if generate_module == ModuleType.TTS:
        if is_login:
            # 当用户登录时，当月最多免费生成10次，当天最多免费生成5次
            if day_count >= 5 or month_count >= 10:
                ret = 2
                msg = 'Free usage limit exceeded, please upgrade to a paid plan!'
                return ret, msg
        else:
            # 当未登录时，当月最多免费生成8次，当天最多免费生成4次  
            if day_count >= 4 or month_count >= 8:
                ret = 1
                msg = 'Please login to continue!'
                return ret, msg
    else:
        pass # todo 其他模块的限制
    return ret, msg
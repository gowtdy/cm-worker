# -*- coding: utf-8 -*-

DEFAULT_USER_FIRST_IP = "100.100.100.100"

ALLOWED_DOMAINS = frozenset({"caluma.ai", "mogofun.com", "aivoicelab.net"})

def get_allowed_domains():
    return ALLOWED_DOMAINS

def get_white_list() :
    white_list = []
    return white_list

def get_limit_config() :
    limit_config = {
        "day_count": 6,
        "month_count": 15,
    }
    return limit_config

def get_url_secret_key() :
    return "8pf4i9NOKl4o8dhiI-qoy6xzx9T514s6KmcR8kK1dZQ"

def get_download_secret_key() :
    return "DnepI0KkrsA44236qPgC3eIHXO-LTHjH0apOi3L_RDs"

def get_tts_secret_key() :
    return "0x4AAAAAABL7dEKVA5-OlS1S"

def get_sound_secret_key() :
    return "Kvy49gPaoDbiIojFmENTIYKgzE35DiqaTzZegEh5z+M="

def get_cover_secret_key() :
    return "l4AnBbVDNIardm33PyFP0Ki29uLAn9a6xrzNL/6FPuQ="

def get_transcribe_secret_key() :
    return "BU_DgJr-uTKs3LqcgcBOal0gpi_tLRdnuxBAly2Zss4"

def get_echobeat_user_key() :
  return "VR9qk9GIPqL-1qPcv4PrtKzjVPsdmQLvz68_ukxrzv0"

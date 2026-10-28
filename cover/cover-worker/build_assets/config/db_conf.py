import os

def get_mysql_conf() :
    #mysql_conf = {
    #               'username': 'aigc',
    #               'password': 'password',
    #               #'address': 'host.docker.internal',
    #               #'address': '192.168.3.83',
    #               'address': '192.168.3.9',
    #               'database': 'tiktokvoice'
    #             }
    mysql_conf = {
      "username": os.getenv("MYSQL_USERNAME", "aigc"),
      "password": os.getenv("MYSQL_PASSWORD", "password"),
      "address": os.getenv("MYSQL_ADDRESS", "127.0.0.1"),
      "database": os.getenv("MYSQL_DATABASE", "tiktokvoice"),
      "port": os.getenv("MYSQL_PORT", "3306"),
    }
    print('mysql_conf: ', mysql_conf)
    return mysql_conf
import os

def get_mysql_conf() :
    mysql_conf = {
      "username": os.getenv("US_MYSQL_USERNAME", "aigc"),
      "password": os.getenv("US_MYSQL_PASSWORD", "password"),
      "address": os.getenv("US_MYSQL_ADDRESS", "127.0.0.1"),
      "database": os.getenv("US_MYSQL_DATABASE", "tiktokvoice"),
      "port": os.getenv("US_MYSQL_PORT", "3306"),
    }
    print('mysql_conf: ', mysql_conf)
    return mysql_conf

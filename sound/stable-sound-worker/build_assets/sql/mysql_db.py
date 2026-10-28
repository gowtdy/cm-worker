import datetime
from sqlalchemy import create_engine, text, Column, Integer, String, Date, TIMESTAMP, func, DECIMAL, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import SQLAlchemyError
import time

from config.db_conf import get_mysql_conf

mysql_conf = get_mysql_conf()
# 数据库连接配置
#DATABASE_URL = f'mysql+pymysql://{mysql_conf["username"]}:{mysql_conf["password"]}@{mysql_conf["address"]}/{mysql_conf["database"]}'
# 数据库连接配置
DATABASE_URL = (
    f'mysql+pymysql://{mysql_conf["username"]}:{mysql_conf["password"]}'
    f'@{mysql_conf["address"]}:{mysql_conf["port"]}/{mysql_conf["database"]}'
)

# 假设 DATABASE_URL 已在其他地方定义

# 将 Base 的定义移到函数外部
Base = declarative_base()

def create_db_engine(retries=3, delay=5):
    print(f"Creating database engine with URL: {DATABASE_URL}")
    for attempt in range(retries):
        try:
            engine = create_engine(
                DATABASE_URL,
                pool_size=10,
                max_overflow=10,
                pool_recycle=3600,  # 每小时回收连接
                pool_pre_ping=True,  # 在使用连接前进行ping操作
                poolclass=QueuePool,
                connect_args={
                    'connect_timeout': 60,  # 连接超时时间
                },
            )
            # 测试连接
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except SQLAlchemyError as e:
            print(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt < retries - 1:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                raise

engine = None
Session = None

try:
    engine = create_db_engine()
    Session = sessionmaker(bind=engine)
    print("Database engine created successfully.")
except SQLAlchemyError as e:
    print(f"Failed to create database engine: {str(e)}")
    # 在这里可以添加适当的错误处理，比如退出程序或者使用备用数据库

class IpUseRecord(Base):
    __tablename__ = 'ip_use_records'
    id = Column(Integer, primary_key=True)
    firstip_address = Column(String(45), nullable=False)
    user_email = Column(String(255), nullable=False)
    allip_address = Column(String(255), nullable=True)
    date = Column(Date, nullable=False)
    text = Column(Text, nullable=False)
    character_count = Column(Integer, default=0)
    word_count = Column(Integer, default=0)
    modelcat = Column(String(255), nullable=True)
    modelname = Column(String(255), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

# 增加记录
def add_ip_use_record(firstip_address, user_email, allip_address, date, text, textlen, modelcat='', modelname=''):
    session = Session()
    new_record = IpUseRecord(
        firstip_address=firstip_address, 
        user_email=user_email, 
        allip_address=allip_address, 
        date=date, 
        text=text,
        character_count=textlen,
        modelcat=modelcat,
        modelname=modelname)
    session.add(new_record)
    session.commit()
    session.close()

# 查询记录
def get_ip_use_records():
    session = Session()
    records = session.query(IpUseRecord).all()
    session.close()
    return records

def get_ip_use_count(ip_address, date):
    session = Session()
    try:
        # 构建查询
        query = session.query(func.count(IpUseRecord.id)).filter(
            IpUseRecord.firstip_address == ip_address,
            IpUseRecord.date == date
        )
        
        # 打印实际执行的 SQL 语句
        print("get_ip_use_count Executing SQL:", query.statement.compile(compile_kwargs={"literal_binds": True}))
        
        # 执行查询
        count = query.scalar()
        return count
    except SQLAlchemyError as e:
        print(f"Error querying IP use count: {str(e)}")
        return 0
    finally:
        session.close()

def get_email_use_count(email, date):
    session = Session()
    try:
        # 构建查询
        query = session.query(func.count(IpUseRecord.id)).filter(
            IpUseRecord.user_email == email,
            IpUseRecord.date == date
        )
        
        # 打印实际执行的 SQL 语句
        print("get_email_use_count Executing SQL:", query.statement.compile(compile_kwargs={"literal_binds": True}))
        
        # 执行查询
        count = query.scalar()
        return count
    except SQLAlchemyError as e:
        print(f"Error querying email use count: {str(e)}")
        return 0
    finally:
        session.close()

class IpSoundsRecord(Base):
    __tablename__ = 'ip_sounds_records'
    id = Column(Integer, primary_key=True)
    firstip_address = Column(String(45), nullable=False)
    user_email = Column(String(255), nullable=True)
    allip_address = Column(String(255), nullable=True)
    date = Column(Date, nullable=False)
    text = Column(String(255), nullable=False)
    character_count = Column(Integer, default=0)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())
    uid = Column(String(64), nullable=True)
    uri = Column(String(255), nullable=True)
    req_data = Column(String(1024), nullable=True)
    task_status = Column(String(16), nullable=True)
    out_path = Column(String(255), nullable=True)

def add_ip_sounds_record(firstip_address, date, text, uid, req_uri, character_count=0, user_email=None, allip_address=None, 
                         req_data=None, task_status="pending", out_path=None):
    session = Session()
    try:
        new_record = IpSoundsRecord(
            firstip_address=firstip_address,
            user_email=user_email,
            allip_address=allip_address,
            date=date,
            text=text,
            character_count=character_count,
            uid=uid,
            uri=req_uri,
            req_data=req_data,
            task_status=task_status,
            out_path=out_path
        )
        session.add(new_record)
        session.commit()
        return 0
    except Exception as e:
        session.rollback()
        print(f'添加声音记录失败，错误：{e}')
        return 1
    finally:
        session.close()

def update_ip_sounds_record(task_id, out_path, task_status="complete"):
    """Update sound task record. task_status is typically complete or failed."""
    session = Session()
    try:
        session.query(IpSoundsRecord).filter(IpSoundsRecord.uid == task_id).update({
            IpSoundsRecord.task_status: task_status,
            IpSoundsRecord.out_path: out_path
        })
        session.commit()
        return 0
    except Exception as e:
        session.rollback()
        print(f'更新声音记录失败，错误：{e}')
        return 1
    finally:
        session.close()

def get_out_path_by_id(task_id, timeout=30, interval=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with Session() as session:
            record = session.query(IpSoundsRecord).filter(IpSoundsRecord.uid == task_id).first()
            if record.task_status == 'complete':
                return record.out_path
            time.sleep(interval)
    return None

def get_ip_sounds_records():
    session = Session()
    try:
        records = session.query(IpSoundsRecord).all()
        return records
    finally:
        session.close()

def get_ip_sounds_count(ip_address, date):
    session = Session()
    try:
        query = session.query(func.count(IpSoundsRecord.id)).filter(
            IpSoundsRecord.firstip_address == ip_address,
            IpSoundsRecord.date == date
        )
        
        print("get_ip_sounds_count Executing SQL:", query.statement.compile(compile_kwargs={"literal_binds": True}))
        
        count = query.scalar()
        return count
    except SQLAlchemyError as e:
        print(f"查询IP声音使用次数出错: {str(e)}")
        return 0
    finally:
        session.close()

def get_email_sounds_count(email, date):
    session = Session()
    try:
        query = session.query(func.count(IpSoundsRecord.id)).filter(
            IpSoundsRecord.user_email == email,
            IpSoundsRecord.date == date
        )
        
        print("get_email_sounds_count Executing SQL:", query.statement.compile(compile_kwargs={"literal_binds": True}))
        
        count = query.scalar()
        return count
    except SQLAlchemyError as e:
        print(f"查询邮箱声音使用次数出错: {str(e)}")
        return 0
    finally:
        session.close()

class ApiUser(Base):
    __tablename__ = 'api_users'
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    api_key = Column(String(64), nullable=True)
    total_usable = Column(Integer, nullable=False, default=0)
    used_char_count = Column(Integer, nullable=False, default=0)
    usable_firstday = Column(Date, nullable=True)
    usable_lastday = Column(Date, nullable=True)
    from_domain = Column(String(64), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

def add_api_user(email, from_domain, api_key=None, total_usable=0, usable_firstday=None, usable_lastday=None):
    session = Session()
    try:
        new_user = ApiUser(
            email=email,
            api_key=api_key,
            total_usable=total_usable,
            from_domain=from_domain,
            usable_firstday=usable_firstday,
            usable_lastday=usable_lastday
        )
        session.add(new_user)
        session.commit()
        return 0
    except Exception as e:
        session.rollback()
        print(f'add api user failed, error:{e}')
        return 1
    finally:
        session.close()

def get_api_user_by_email(email):
    session = Session()
    try:
        query = session.query(ApiUser).filter(ApiUser.email == email)
        
        # 打印实际执行的 SQL 语句
        print("get_api_user_by_email Executing SQL:", query.statement.compile(compile_kwargs={"literal_binds": True}))
        
        user = query.first()
        return user
    finally:
        session.close()

def update_api_user_by_email(email, **kwargs):
    session = Session()
    try:
        user = session.query(ApiUser).filter(ApiUser.email == email).first()
        if user:
            for key, value in kwargs.items():
                if hasattr(user, key):
                    setattr(user, key, value)
            session.commit()
            return 0
        else:
            print(f"No user found with email: {email}")
            return 1
    except Exception as e:
        session.rollback()
        print(f"Failed to update api user, error: {e}")
        return 1
    finally:
        session.close()

class ApiUserRecord(Base):
    __tablename__ = 'api_user_records'
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False)
    firstip = Column(String(64), nullable=True)
    allip = Column(String(255), nullable=True)
    date = Column(Date, nullable=False)
    text = Column(Text, nullable=True)
    character_count = Column(Integer, nullable=False, default=0)
    modelcat = Column(String(255), nullable=True)
    modelname = Column(String(255), nullable=True)
    user_action = Column(String(64), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

def add_api_user_record(email, date, user_action, firstip=None, allip=None, text=None, 
                       character_count=0, modelcat=None, modelname=None):
    session = Session()
    try:
        new_record = ApiUserRecord(
            email=email,
            firstip=firstip,
            allip=allip,
            date=date,
            text=text,
            character_count=character_count,
            modelcat=modelcat,
            modelname=modelname,
            user_action=user_action
        )
        session.add(new_record)
        session.commit()
        return 0
    except Exception as e:
        session.rollback()
        print(f'add api user record failed, error:{e}')
        return 1
    finally:
        session.close()

def get_api_user_records_by_email(email, start_date=None, end_date=None):
    session = Session()
    try:
        query = session.query(ApiUserRecord).filter(ApiUserRecord.email == email)
        if start_date and end_date:
            query = query.filter(ApiUserRecord.date.between(start_date, end_date))
        
        # 打印实际执行的 SQL 语句
        print("get_api_user_records_by_email Executing SQL:", query.statement.compile(compile_kwargs={"literal_binds": True}))
        
        records = query.all()
        return records
    finally:
        session.close()            
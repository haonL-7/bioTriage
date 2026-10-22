"""SQLAlchemy 2.x 引擎与会话管理。

DATABASE_URL 未设置时回退 sqlite:///./biotriage.db；psycopg2 是惰性依赖，
只有真正使用 postgres URL 时才会被 import。
"""
import importlib.util
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.schema import CreateColumn

DEFAULT_DATABASE_URL = "sqlite:///./biotriage.db"


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


_engine = None
_session_factory = None


def database_url() -> str:
    """归一化 DATABASE_URL（postgres:// → postgresql+psycopg2://）。"""
    url = (os.getenv("DATABASE_URL") or "").strip() or DEFAULT_DATABASE_URL
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def get_engine():
    """惰性创建引擎：import 阶段不触碰驱动，SQLite 测试无需 psycopg2。"""
    global _engine
    if _engine is None:
        url = database_url()
        kwargs = {}
        if url.startswith("sqlite"):
            # TestClient 在线程池里跑请求，需关闭 sqlite 的同线程检查
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            # 数据库挂起/不可达时不要挂住请求（云函数冷启动 + Neon 闲置挂起场景），5 秒放弃后由调用方降级
            kwargs["connect_args"] = {"connect_timeout": 5}
        _engine = create_engine(url, pool_pre_ping=True, **kwargs)
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


# 进程内建表状态：成功后置真，之后不再重复 create_all；
# 失败（冷启动数据库挂起 / 口令错 / 网络不可达）时在真正需要数据库的路径上再试一次。
_schema_ready = False
_last_error_text = None
_last_error_class = None
_last_error_kind = None


def schema_ready() -> bool:
    return _schema_ready


def _classify_error(exc: Exception) -> tuple[str, str]:
    """把数据库异常粗分类，只用于诊断（不暴露原文）。"""
    cls = type(exc).__name__
    raw = str(exc)
    text = raw.lower()
    if isinstance(exc, (ModuleNotFoundError, ImportError)) or "no module named" in text:
        return cls, "driver"
    if "gaierror" in cls.lower() or "translate host name" in text:
        return cls, "dns"
    if "timeout expired" in text or "connection timed out" in text or "could not connect" in text:
        return cls, "timeout"
    if "authentication failed" in text or "password" in text:
        return cls, "auth"
    if "unable to open database file" in text:
        return cls, "config"
    return cls, "unknown"


def _note_failure(exc: Exception) -> None:
    """只在错误信息变化时打印，避免每请求刷屏；同时记录诊断用的类名与粗分类。"""
    global _last_error_text, _last_error_class, _last_error_kind
    _last_error_class, _last_error_kind = _classify_error(exc)
    text = f"{type(exc).__name__}: {exc}"
    if text != _last_error_text:
        _last_error_text = text
        print(
            f"[警告] 账户数据库不可用（{text}）：账户功能暂不可用，"
            "首页/知识库/AI 分析等只读接口不受影响；首个账户请求会自动重试初始化。"
        )


def ensure_schema() -> bool:
    """尝试建表，永不抛异常。已就绪时直接返回 True。"""
    global _schema_ready, _last_error_text, _last_error_class, _last_error_kind
    if _schema_ready:
        return True
    try:
        from . import models  # noqa: F401  确保模型注册到 Base.metadata

        Base.metadata.create_all(get_engine())
    except Exception as exc:  # noqa: BLE001 - 数据库不可用不能拖垮整站
        _note_failure(exc)
        return False
    _schema_ready = True
    _last_error_text = None
    _last_error_class = None
    _last_error_kind = None
    # 建表成功后再补列：失败只警告，仍视为"库可用"（缺列查询会由账户端点降级成 503，
    # 不会变成 500 堆栈；也不该让整站随一次 DDL 失败停摆）。
    migrate_user_columns()
    return True


# ==================== 轻量迁移（users 新增列） ====================
# create_all 只建缺失的表，不会给已存在的表补列；is_banned / quota_limit 因此靠这里补。
# 三条要求：幂等（列已在就跳过）、进程内只跑一次、失败只打警告（不阻止启动、不丢既有数据）。
_schema_migrated = False


def migrate_user_columns(engine=None) -> bool:
    """用 inspector 检查 users 列，缺则 ALTER TABLE ADD COLUMN；永不抛异常。"""
    global _schema_migrated
    if _schema_migrated:
        return True
    try:
        from . import models  # noqa: F401  列定义与 ORM 同源

        engine = engine if engine is not None else get_engine()
        existing = {column["name"] for column in inspect(engine).get_columns("users")}
        missing = [name for name in models.USER_ADDED_COLUMNS if name not in existing]
        if missing:
            with engine.begin() as connection:
                for name in missing:
                    # 用方言编译器生成列 DDL：SQLite 得 DEFAULT 0，Postgres 得 DEFAULT false
                    ddl = str(CreateColumn(models.USER_ADDED_COLUMNS[name]).compile(dialect=engine.dialect))
                    connection.execute(text(f"ALTER TABLE users ADD COLUMN {ddl}"))
            print(f"[启动] users 表轻量迁移完成，已补列：{', '.join(missing)}（既有数据保持不变）")
    except Exception as exc:  # noqa: BLE001 - 迁移失败不能阻止应用启动
        print(
            f"[警告] users 表轻量迁移失败（{type(exc).__name__}: {exc}）："
            "is_banned / quota_limit 可能缺失，管理员功能因此不可用；"
            "请确认数据库账号具备 ALTER TABLE 权限后重启。既有用户数据不受影响。"
        )
        return False
    _schema_migrated = True
    return True


def init_db() -> bool:
    """建表 + 轻量迁移（启动时调用）；失败返回 False，绝不抛出。"""
    return ensure_schema()


def _scheme_of(url: str) -> str:
    lowered = url.strip().lower()
    if lowered.startswith("sqlite"):
        return "sqlite"
    if lowered.startswith("postgres"):
        return "postgres"
    return "other"


def _driver_available(scheme: str) -> bool:
    """只做 find_spec 探测：不 import 驱动、不建立连接。"""
    module = {"sqlite": "sqlite3", "postgres": "psycopg2"}.get(scheme)
    if module is None:
        return False
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def account_db_status() -> dict:
    """诊断快照：只读取已记录的状态，不发起任何连接。

    绝不返回连接串、host、用户名、库名、密码、异常原文或堆栈。
    """
    configured_url = (os.getenv("DATABASE_URL") or "").strip()
    scheme = _scheme_of(configured_url) if configured_url else "sqlite"
    kind = _last_error_kind
    if not configured_url:
        # 未显式配置即回退本地 sqlite：标 config，便于线上区分"环境变量没读到"与"连接被拒"
        kind = "config"
    return {
        "db_configured": bool(configured_url),
        "db_scheme": scheme,
        "schema_ready": schema_ready(),
        "driver_available": _driver_available(scheme),
        "last_error_class": _last_error_class,
        "last_error_kind": kind,
    }


def get_db():
    """FastAPI 依赖：每个请求一个会话；数据库不可用时 yield None，由端点统一降级。"""
    try:
        factory = get_session_factory()
    except Exception as exc:  # noqa: BLE001 - 建 engine 失败同样不能冒成 500
        _note_failure(exc)
        yield None
        return

    db = factory()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

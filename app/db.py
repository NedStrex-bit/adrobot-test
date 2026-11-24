from datetime import datetime, timezone
import hashlib
from sqlalchemy import JSON, DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings

class Base(DeclarativeBase):
    pass

class Record(Base):
    __tablename__ = 'records'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

engine = create_engine(settings.database_url, connect_args={'check_same_thread': False} if settings.database_url.startswith('sqlite') else {})
Session = sessionmaker(engine, expire_on_commit=False)

def scoped_key(key):
    scope = 'demo' if settings.demo_mode else hashlib.sha256(settings.keitaro_url.rstrip('/').encode()).hexdigest()[:12]
    return f'{scope}:{key}'

def read(key):
    key = scoped_key(key)
    with Session() as session:
        row = session.get(Record, key)
        return row.value if row else None

def write(key, value):
    key = scoped_key(key)
    with Session.begin() as session:
        row = session.get(Record, key)
        if row:
            row.value = value
            row.version += 1
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(Record(key=key, value=value))

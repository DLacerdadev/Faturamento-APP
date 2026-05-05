from sqlalchemy import Column, BigInteger, String
from datetime import datetime
from passlib.hash import pbkdf2_sha256
from app.db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    email = Column(String, unique=True, index=True)
    full_name = Column(String)
    is_active = Column(BigInteger, default=1)
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    last_login = Column(String)
    
    def set_password(self, password: str):
        self.password_hash = pbkdf2_sha256.hash(password)
    
    def verify_password(self, password: str) -> bool:
        return pbkdf2_sha256.verify(password, self.password_hash)
    
    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "last_login": self.last_login
        }

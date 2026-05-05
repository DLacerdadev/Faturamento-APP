from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text
from datetime import datetime
from app.db import Base

class Integration(Base):
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    integration_type = Column(String, nullable=False)
    api_endpoint = Column(String)
    api_key_name = Column(String)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    last_sync = Column(DateTime)
    sync_frequency = Column(String)
    config_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "integration_type": self.integration_type,
            "api_endpoint": self.api_endpoint,
            "api_key_name": self.api_key_name,
            "description": self.description,
            "is_active": self.is_active,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "sync_frequency": self.sync_frequency,
            "config_json": self.config_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

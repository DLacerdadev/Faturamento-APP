from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime
from app.db import Base

class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    report_type = Column(String, nullable=False)
    file_path = Column(String)
    file_format = Column(String)
    description = Column(Text)
    status = Column(String, default="pending")
    generated_by = Column(String)
    parameters = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "report_type": self.report_type,
            "file_path": self.file_path,
            "file_format": self.file_format,
            "description": self.description,
            "status": self.status,
            "generated_by": self.generated_by,
            "parameters": self.parameters,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
        }

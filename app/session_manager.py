import uuid
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SECRET_KEY = os.environ.get("SESSION_SECRET", "default-secret-key-change-in-production")

class SessionManager:
    def __init__(self, expiry_hours: int = 24 * 7):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._expiry_hours = expiry_hours
        self._expiry_seconds = expiry_hours * 3600
        self._serializer = URLSafeTimedSerializer(SECRET_KEY)
    
    def create_session(self, user_id: int, username: str) -> str:
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = {
            "user_id": user_id,
            "username": username,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(hours=self._expiry_hours)
        }
        signed_token = self._serializer.dumps(session_id)
        return signed_token
    
    def get_session(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        
        try:
            session_id = self._serializer.loads(token, max_age=self._expiry_seconds)
        except (BadSignature, SignatureExpired):
            return None
        
        if session_id not in self._sessions:
            return None
        
        session = self._sessions[session_id]
        
        if datetime.utcnow() > session["expires_at"]:
            self.delete_session(token)
            return None
        
        return session
    
    def delete_session(self, token: str) -> bool:
        try:
            session_id = self._serializer.loads(token, max_age=self._expiry_seconds)
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
        except (BadSignature, SignatureExpired):
            pass
        return False
    
    def cleanup_expired(self):
        now = datetime.utcnow()
        expired = [sid for sid, data in self._sessions.items() if now > data["expires_at"]]
        for sid in expired:
            del self._sessions[sid]

session_manager = SessionManager()


def validate_token(token: str, db) -> Optional[Any]:
    from app.models.user import User
    session = session_manager.get_session(token)
    if not session:
        return None
    user = db.query(User).filter(User.id == session["user_id"]).first()
    return user

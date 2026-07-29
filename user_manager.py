import os
import json
import hashlib
import secrets
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

class UserManager:
    """Manages user accounts, salted password hashing, and session authentication."""
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.users: Dict[str, Dict[str, Any]] = self._load_users()
        self.active_sessions: Dict[str, str] = {}  # token -> email

    def _load_users(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(USERS_FILE):
            return {}
        try:
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading users.json: {e}")
            return {}

    def _save_users(self) -> None:
        try:
            with open(USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.users, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving users.json: {e}")

    def _hash_password(self, password: str, salt: Optional[str] = None) -> tuple[str, str]:
        if not salt:
            salt = secrets.token_hex(16)
        salted_pwd = (password + salt).encode('utf-8')
        pwd_hash = hashlib.sha256(salted_pwd).hexdigest()
        return pwd_hash, salt

    def register_user(self, email: str, password: str) -> tuple[bool, str, Optional[str]]:
        """Registers a new user account."""
        email = email.strip().lower()

        import re
        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_regex, email):
            return False, "Некоректний формат Email адреси.", None

        if len(password) < 6:
            return False, "Пароль має містити щонайменше 6 символів.", None

        if email in self.users:
            return False, f"Користувач з Email '{email}' вже зареєстрований.", None

        pwd_hash, salt = self._hash_password(password)
        self.users[email] = {
            'email': email,
            'password_hash': pwd_hash,
            'salt': salt,
            'role': 'user',
            'created_at': os.getenv('BUILD_DATE', '2026-07-29')
        }
        self._save_users()
        token = secrets.token_hex(24)
        self.active_sessions[token] = email
        logger.info(f"👤 NEW USER REGISTERED SUCCESSFULLY: {email}")
        return True, "Реєстрація успішна! Ласкаво просимо.", token

    def authenticate_user(self, email: str, password: str) -> tuple[bool, str, Optional[Dict[str, Any]], Optional[str]]:
        """Authenticates user with email and password."""
        email = email.strip().lower()

        if email not in self.users:
            return False, "Користувача з таким Email не знайдено.", None, None

        user = self.users[email]
        pwd_hash, _ = self._hash_password(password, salt=user['salt'])

        if pwd_hash != user['password_hash']:
            return False, "Невірний пароль. Будь ласка, спробуйте ще раз.", None, None

        token = secrets.token_hex(24)
        self.active_sessions[token] = email
        logger.info(f"🟢 USER AUTHENTICATED SUCCESSFULLY: {email}")
        return True, "Авторизація успішна", user, token

    def verify_session(self, token: str) -> bool:
        """Verifies if session token is active."""
        if not token:
            return False
        return token in self.active_sessions

user_manager = UserManager()

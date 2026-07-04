"""Field-level PII encryption (Back Office A3).

EncryptedString is a transparent SQLAlchemy type: plaintext in Python,
Fernet ciphertext at rest. Legacy plaintext rows are returned as-is on
read (Fernet tokens always start with "gAAAA"), so no data migration is
needed — values are encrypted on next write.

Key: PII_ENCRYPTION_KEY in .env (server only, never in git).
Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text, TypeDecorator

from app.core.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None
if settings.pii_encryption_key:
    try:
        _fernet = Fernet(settings.pii_encryption_key.encode())
    except ValueError:
        logger.error("PII_ENCRYPTION_KEY is invalid — PII encryption DISABLED")


def encrypt_pii(value: str) -> str:
    if _fernet is None:
        return value
    return _fernet.encrypt(value.encode()).decode()


def decrypt_pii(value: str) -> str:
    if _fernet is None or not value.startswith("gAAAA"):
        return value
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        logger.error("PII decryption failed — wrong key? Returning ciphertext")
        return value


class EncryptedString(TypeDecorator):
    """Stores strings Fernet-encrypted; reads legacy plaintext transparently."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return encrypt_pii(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return decrypt_pii(value)

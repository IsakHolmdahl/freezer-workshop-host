"""Technician passes: the service desk's signing key and the passes minted with it."""
from pathlib import Path
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ISSUER = "service-desk"
ALGORITHM = "EdDSA"


class PassIssuer:
    def __init__(self, key_path: Path, ttl_seconds: int = 900):
        self.ttl = ttl_seconds
        if key_path.exists():
            self._key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        else:
            self._key = Ed25519PrivateKey.generate()
            key_path.write_bytes(self._key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            key_path.chmod(0o600)
        self.public_key_pem = self._key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def mint(self, store_id: str, now: float, kind: str = "valid", technician: str = "tech-0417",
             other_store: str = "") -> str:
        """kind: valid | unsigned | expired | other_store. Each differs from valid in one way only."""
        # Backdated a little: a laptop whose clock is behind ours must not see a pass "from the future".
        issued = int(now) - 30 - (26 * 3600 if kind == "expired" else 0)
        claims = {"iss": ISSUER, "aud": other_store if kind == "other_store" else store_id, "sub": technician,
                  "iat": issued, "exp": issued + self.ttl, "jti": uuid4().hex}
        if kind == "unsigned":
            return jwt.encode(claims, None, algorithm="none")
        return jwt.encode(claims, self._key, algorithm=ALGORITHM)

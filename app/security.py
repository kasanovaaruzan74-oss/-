import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, delete, select

from app.database.db import get_session
from app.database.models import AppUser, LoginSession, Role, UserCredential, utc_now


ADMIN_ROLE = "Administrator"
STANDARD_ROLE = "Executor"
PASSWORD_ITERATIONS = 600_000
SESSION_LIFETIME = timedelta(hours=8)
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user: AppUser
    role: str


def validate_password(password: str) -> None:
    if (
        not 12 <= len(password) <= 128
        or not any(character.isalpha() for character in password)
        or not any(character.isdigit() for character in password)
    ):
        raise ValueError("Құпиясөз 12–128 таңбадан тұруы және кемінде бір әріп пен бір цифрды қамтуы керек.")


def _password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{_password_digest(password, salt).hex()}"


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _password_digest(password, bytes(16))
        return False
    salt_hex, digest_hex = password_hash.split(":")
    return hmac.compare_digest(_password_digest(password, bytes.fromhex(salt_hex)), bytes.fromhex(digest_hex))


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(session: Session, user: AppUser) -> str:
    token = secrets.token_urlsafe(32)
    now = utc_now()
    session.exec(delete(LoginSession).where(LoginSession.expires_at <= now))
    session.add(LoginSession(token_hash=token_digest(token), user_id=user.user_id, expires_at=now + SESSION_LIFETIME))
    session.commit()
    return token


def configure_demo_admin(session: Session, email: str, password: str) -> None:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Әкімшінің электрондық пошта мекенжайы жарамсыз.")
    password_hash = hash_password(password)
    role = session.exec(select(Role).where(Role.role_name == ADMIN_ROLE)).first()
    if role is None:
        role = Role(role_name=ADMIN_ROLE)
        session.add(role)
        session.flush()
    user = session.exec(select(AppUser).where(AppUser.email == email)).first()
    if user is None:
        user = AppUser(role_id=role.role_id, email=email, full_name="Әкімші")
        session.add(user)
        session.flush()
    if user.role_id != role.role_id:
        raise ValueError("Көрсетілген тіркелгіде әкімші рөлі жоқ.")
    credential = session.get(UserCredential, user.user_id)
    if credential is None:
        session.add(UserCredential(user_id=user.user_id, password_hash=password_hash))
    elif not verify_password(password, credential.password_hash):
        credential.password_hash = password_hash
        session.exec(delete(LoginSession).where(LoginSession.user_id == user.user_id))
    session.commit()


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Жүйеге кіріңіз немесе кіру деректерін тексеріңіз.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> Principal:
    if credentials is None or len(credentials.credentials) != 43:
        raise unauthorized()
    row = session.exec(
        select(AppUser, Role.role_name)
        .join(Role, Role.role_id == AppUser.role_id)
        .join(LoginSession, LoginSession.user_id == AppUser.user_id)
        .where(
            LoginSession.token_hash == token_digest(credentials.credentials),
            LoginSession.expires_at > utc_now(),
            AppUser.is_active.is_(True),
        )
    ).first()
    if row is None:
        raise unauthorized()
    user, role = row
    return Principal(user, role)


def document_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> Principal | None:
    if request.app.state.auth_required:
        return get_principal(credentials, session)
    return None


def require_document_admin(
    principal: Annotated[Principal | None, Depends(document_principal)],
) -> None:
    if principal is not None and principal.role != ADMIN_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Бұл әрекетке әкімші рөлі қажет.")

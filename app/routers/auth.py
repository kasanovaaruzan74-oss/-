from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database.db import get_session
from app.database.models import AppUser, LoginSession, Role, UserCredential
from app.security import (
    ADMIN_ROLE,
    STANDARD_ROLE,
    Principal,
    bearer_scheme,
    get_principal,
    hash_password,
    issue_token,
    token_digest,
    unauthorized,
    validate_password,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["Авторизация"])


class LoginData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=1, max_length=128)


class RegisterData(LoginData):
    full_name: str = Field(min_length=3, max_length=160)

    @field_validator("password")
    @classmethod
    def check_password(cls, password: str) -> str:
        validate_password(password)
        return password

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_name(cls, full_name):
        return full_name.strip() if isinstance(full_name, str) else full_name


class PublicUser(BaseModel):
    user_id: int
    email: str
    full_name: str
    role: str


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


def user_response(user: AppUser, role: str) -> PublicUser:
    return PublicUser(user_id=user.user_id, email=user.email, full_name=user.full_name, role=role)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterData, session: Annotated[Session, Depends(get_session)]) -> PublicUser:
    email = payload.email.lower()
    password_hash = hash_password(payload.password)
    try:
        role = session.exec(select(Role).where(Role.role_name == STANDARD_ROLE)).first()
        if role is None:
            role = Role(role_name=STANDARD_ROLE)
            session.add(role)
            session.flush()
        user = AppUser(email=email, full_name=payload.full_name, role_id=role.role_id)
        session.add(user)
        session.flush()
        session.add(UserCredential(user_id=user.user_id, password_hash=password_hash))
        profile = user_response(user, STANDARD_ROLE)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Не удалось зарегистрировать аккаунт.") from None
    return profile


@router.post("/login")
def login(
    payload: LoginData,
    session: Annotated[Session, Depends(get_session)],
    response: Response,
) -> AccessToken:
    row = session.exec(
        select(AppUser, UserCredential.password_hash)
        .join(UserCredential, UserCredential.user_id == AppUser.user_id)
        .where(AppUser.email == payload.email.lower(), AppUser.is_active.is_(True))
    ).first()
    if not verify_password(payload.password, row[1] if row else None):
        raise unauthorized()
    response.headers["Cache-Control"] = "no-store"
    return AccessToken(access_token=issue_token(session, row[0]))


@router.get("/me")
def me(principal: Annotated[Principal, Depends(get_principal)]) -> PublicUser:
    return user_response(principal.user, principal.role)


@router.get("/users/{user_id}")
def read_user(
    user_id: int,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> PublicUser:
    if principal.user.user_id != user_id and principal.role != ADMIN_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав.")
    user = session.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден.")
    return user_response(user, user.role.role_name)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    _principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    login_session = session.get(LoginSession, token_digest(credentials.credentials))
    if login_session is not None:
        session.delete(login_session)
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

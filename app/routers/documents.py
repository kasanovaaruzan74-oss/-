from datetime import date
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, desc, select

from app.database.db import get_session
from app.database.models import (
    AppUser,
    Department,
    DeanDocument,
    DocumentApproval,
    DocumentAttachment,
    DocumentComment,
    DocumentCreate,
    DocumentHistoryEntry,
    DocumentRead,
    DocumentReplace,
    DocumentStatus,
    DocumentType,
    DocumentUpdate,
    ExecutorAssignment,
    LookupItem,
)

from app.security import Principal, document_principal, require_document_admin


router = APIRouter(prefix="/documents", tags=["Құжаттар"], dependencies=[Depends(document_principal)])


@router.get("/lookups")
def get_lookups(session: Session = Depends(get_session)) -> dict[str, list[LookupItem]]:
    types = session.exec(select(DocumentType).where(DocumentType.is_active)).all()
    statuses = session.exec(select(DocumentStatus)).all()
    departments = session.exec(select(Department).where(Department.is_active)).all()
    return {
        "types": [LookupItem(id=item.document_type_id, name=item.type_name, code=item.code) for item in types],
        "statuses": [LookupItem(id=item.status_id, name=item.status_name, code=item.status_code) for item in statuses],
        "departments": [LookupItem(id=item.department_id, name=item.department_name) for item in departments],
    }


@router.get("", response_model=list[DocumentRead])
def list_documents(
    search: str | None = Query(default=None, max_length=120),
    status_code: str | None = Query(default=None, max_length=30),
    direction: str | None = Query(default=None, max_length=20),
    include_archived: bool = False,
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[DeanDocument]:
    statement = select(DeanDocument)
    if not include_archived:
        statement = statement.where(DeanDocument.is_archived.is_(False))
    if search and search.strip():
        term = search.strip()
        normalize = func.unicode_casefold if session.get_bind().dialect.name == "sqlite" else func.lower
        term = term.casefold() if session.get_bind().dialect.name == "sqlite" else term.lower()
        statement = statement.where(
            or_(
                normalize(DeanDocument.title).contains(term, autoescape=True),
                normalize(DeanDocument.registration_number).contains(term, autoescape=True),
            )
        )
    if status_code:
        statement = statement.join(DocumentStatus).where(DocumentStatus.status_code == status_code)
    if direction:
        statement = statement.where(DeanDocument.direction == direction)
    return list(session.exec(statement.order_by(desc(DeanDocument.registered_at)).limit(limit)).all())


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(document_id: int, session: Session = Depends(get_session)) -> DeanDocument:
    document = session.get(DeanDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Құжат табылмады")
    return document


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: DocumentCreate,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(document_principal),
) -> DeanDocument:
    values = payload.model_dump()
    author_id = principal.user.user_id if principal is not None else payload.created_by_user_id
    values["created_by_user_id"] = author_id
    _validate_references(session, payload.document_type_id, payload.status_id, payload.department_id, author_id)
    document = DeanDocument(
        registration_number=f"DF-{date.today().year}-{uuid4().hex[:8].upper()}",
        **values,
    )
    session.add(document)
    _commit(session)
    session.refresh(document)
    return document


@router.put("/{document_id}", response_model=DocumentRead)
def replace_document(
    document_id: int,
    payload: DocumentReplace,
    session: Session = Depends(get_session),
) -> DeanDocument:
    document = get_document(document_id, session)
    _validate_references(session, payload.document_type_id, payload.status_id, payload.department_id)
    document.sqlmodel_update(payload.model_dump())
    _commit(session)
    session.refresh(document)
    return document


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: int,
    payload: DocumentUpdate,
    session: Session = Depends(get_session),
) -> DeanDocument:
    document = get_document(document_id, session)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Өзгертілетін өрісті көрсетіңіз")
    for required_field in ("title", "document_type_id", "status_id", "direction", "priority"):
        if required_field in changes and changes[required_field] is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Міндетті өрісті бос қалдыруға болмайды")
    _validate_references(
        session,
        changes.get("document_type_id", document.document_type_id),
        changes.get("status_id", document.status_id),
        changes.get("department_id", document.department_id),
    )

    document.sqlmodel_update(changes)
    _commit(session)
    session.refresh(document)
    return document


@router.delete(
    "/{document_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_document_admin)]
)
def delete_document(document_id: int, session: Session = Depends(get_session)) -> Response:
    document = get_document(document_id, session)

    # Оған байланған жазбалар FK арқылы бірге өшеді; қолданушы мен анықтамалық кестелер сақталады.
    for child_model in (ExecutorAssignment, DocumentApproval, DocumentHistoryEntry, DocumentComment, DocumentAttachment):
        children = session.exec(select(child_model).where(child_model.document_id == document_id)).all()
        for child in children:
            session.delete(child)
    session.delete(document)
    _commit(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Деректер қайшылығы анықталды.") from None


def _validate_references(
    session: Session,
    document_type_id: int,
    status_id: int,
    department_id: int | None,
    created_by_user_id: int | None = None,
) -> None:
    if session.get(DocumentType, document_type_id) is None:
        raise HTTPException(status_code=422, detail="Құжат түрі табылмады")
    if session.get(DocumentStatus, status_id) is None:
        raise HTTPException(status_code=422, detail="Құжат мәртебесі табылмады")
    if department_id is not None and session.get(Department, department_id) is None:
        raise HTTPException(status_code=422, detail="Бөлім табылмады")
    if created_by_user_id is not None and session.get(AppUser, created_by_user_id) is None:
        raise HTTPException(status_code=422, detail="Пайдаланушы табылмады")

from collections.abc import Iterator

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database.db import create_database_engine, init_db
from app.database.models import AppUser, DeanDocument, DocumentStatus, DocumentType, Role


@pytest.fixture
def database(tmp_path) -> Iterator[tuple]:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'transactions.db'}")
    init_db(engine)
    try:
        with Session(engine) as session:
            role = Role(role_name="Executor")
            document_type = DocumentType(type_name="Заявление", code="REQUEST")
            status = DocumentStatus(status_name="Зарегистрирован", status_code="REGISTERED")
            session.add_all([role, document_type, status])
            session.flush()
            user = AppUser(
                full_name="Тестовый исполнитель", email="db-test@example.invalid", role_id=role.role_id
            )
            session.add(user)
            session.flush()
            reference_ids = {
                "document_type_id": document_type.document_type_id,
                "status_id": status.status_id,
                "created_by_user_id": user.user_id,
            }
            session.commit()
        yield engine, reference_ids
    finally:
        engine.dispose()


def test_database_persists_document_crud_between_sessions(database):
    engine, ids = database

    with Session(engine) as session:
        document = DeanDocument(registration_number="DF-DB-CRUD", title="Начальное заявление", **ids)
        session.add(document)
        session.commit()
        session.refresh(document)
        document_id = document.document_id

    with Session(engine) as session:
        document = session.get(DeanDocument, document_id)
        assert document.title == "Начальное заявление"
        assert document.created_by.email == "db-test@example.invalid"
        document.title = "Обновлённое заявление"
        session.commit()

    with Session(engine) as session:
        document = session.get(DeanDocument, document_id)
        assert document.title == "Обновлённое заявление"
        session.delete(document)
        session.commit()

    with Session(engine) as session:
        assert session.get(DeanDocument, document_id) is None


def test_database_rolls_back_previously_flushed_insert_after_constraint_error(database):
    engine, ids = database
    registration = "DF-DB-ROLLBACK"

    with Session(engine) as session:
        session.add(DeanDocument(registration_number=registration, title="Первая запись", **ids))
        session.flush()
        assert session.exec(select(DeanDocument)).one().registration_number == registration
        session.add(DeanDocument(registration_number=registration, title="Повтор номера", **ids))

        with pytest.raises(IntegrityError) as error:
            session.flush()

        assert "UNIQUE constraint failed" in str(error.value.orig)
        session.rollback()
        assert session.exec(select(DeanDocument)).all() == []

    with Session(engine) as session:
        assert session.exec(select(DeanDocument)).all() == []
        assert session.get(AppUser, ids["created_by_user_id"]) is not None
        session.add(DeanDocument(registration_number=registration, title="Новая транзакция", **ids))
        session.commit()

    with Session(engine) as session:
        assert session.exec(select(DeanDocument)).one().title == "Новая транзакция"

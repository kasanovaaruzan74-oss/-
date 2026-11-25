import os

import pandas as pd
import requests
import streamlit as st


API_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = 5


def api_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    token: str | None = None,
):
    response = requests.request(
        method,
        f"{API_URL}{path}",
        params=params,
        json=payload,
        headers={"Authorization": f"Bearer {token}"} if token else None,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    if response.status_code != 204:
        return response.json()


def show_api_error(error: requests.RequestException, *, signing_in: bool = False) -> None:
    response = error.response
    if response is None:
        st.error("Серверге қосылу мүмкін болмады. FastAPI қызметінің жұмыс істеп тұрғанын тексеріңіз.")
        return

    status_code = response.status_code
    if status_code == 401 and not signing_in and st.session_state.get("access_token"):
        st.session_state.pop("access_token", None)
        st.session_state.pop("success_notice", None)
        st.session_state["api_notice"] = "Сеанс аяқталды. Қайта кіріңіз."
        st.rerun()

    messages = {
        400: "Енгізілген деректерді тексеріңіз.",
        401: "Электрондық пошта немесе құпиясөз қате.",
        403: "Бұл әрекетті орындауға рұқсатыңыз жоқ.",
        404: "Жазба табылмады. Тізімді жаңартыңыз.",
        409: "Деректер өзгерді. Тізімді жаңартып, әрекетті қайталаңыз.",
        422: "Енгізілген деректерді және анықтамалық мәндерді тексеріңіз.",
        429: "Сұраныс саны тым көп. Біраздан кейін қайталап көріңіз.",
    }
    st.error(messages.get(status_code, "Сервер сұранысты орындай алмады."))


st.set_page_config(page_title="DeanFlow құжаттары", page_icon="📄", layout="wide")
st.title("DeanFlow — деканаттың құжат айналымы")

notice = st.session_state.pop("api_notice", None)
if notice:
    st.warning(notice)

try:
    auth_required = api_request("GET", "/").get("auth_required", False)
except requests.RequestException as error:
    show_api_error(error)
    st.stop()

profile = None
token = st.session_state.get("access_token") if auth_required else None
if auth_required:
    if not token:
        with st.form("sign_in", clear_on_submit=True):
            st.subheader("Жүйеге кіру")
            email = st.text_input("Электрондық пошта", max_chars=254)
            password = st.text_input("Құпиясөз", type="password", max_chars=256)
            submitted = st.form_submit_button("Кіру", type="primary")

        if submitted:
            try:
                login = api_request("POST", "/auth/login", payload={"email": email.strip(), "password": password})
                st.session_state["access_token"] = login["access_token"]
                st.rerun()
            except requests.RequestException as error:
                show_api_error(error, signing_in=True)
        st.stop()

    try:
        profile = api_request("GET", "/auth/me", token=token)
    except requests.RequestException as error:
        show_api_error(error)
        st.stop()

    with st.sidebar:
        st.text(profile["full_name"])
        if st.button("Шығу"):
            try:
                api_request("POST", "/auth/logout", token=token)
            except requests.RequestException:
                st.session_state["api_notice"] = "Сервердегі сеансты аяқтау мүмкін болмады. Осы беттегі сеанс аяқталды."
            st.session_state.pop("access_token", None)
            st.session_state.pop("success_notice", None)
            st.rerun()

success_notice = st.session_state.pop("success_notice", None)
if success_notice:
    st.success(success_notice)

try:
    lookups = api_request("GET", "/documents/lookups", token=token)
except requests.RequestException as error:
    show_api_error(error)
    st.stop()

status_by_name = {"Барлық мәртебелер": None}
status_by_name.update({item["name"]: item["code"] for item in lookups["statuses"]})
type_by_name = {item["name"]: item["id"] for item in lookups["types"]}
department_by_name = {"Бөлім көрсетілмеген": None}
department_by_name.update({item["name"]: item["id"] for item in lookups["departments"]})

with st.sidebar:
    st.subheader("Іздеу және сүзу")
    search = st.text_input("Тіркеу нөмірі немесе құжат тақырыбы", placeholder="Мысалы: анықтама", max_chars=120)
    selected_status = st.selectbox("Мәртебесі", list(status_by_name))
    selected_direction = st.selectbox("Бағыты", ["Барлығы", "Кіріс", "Шығыс", "Ішкі"])
    st.button("Тізімді жаңарту")

params = {"search": search.strip() or None, "status_code": status_by_name[selected_status]}
if selected_direction != "Барлығы":
    params["direction"] = selected_direction
try:
    rows = api_request("GET", "/documents", params=params, token=token)
except requests.RequestException as error:
    show_api_error(error)
    st.stop()

st.subheader("Құжаттар тізімі")
if rows:
    status_names = {item["id"]: item["name"] for item in lookups["statuses"]}
    type_names = {item["id"]: item["name"] for item in lookups["types"]}
    table = [
        {
            "Тіркеу нөмірі": row["registration_number"],
            "Тақырыбы": row["title"],
            "Түрі": type_names.get(row["document_type_id"], "Белгісіз"),
            "Мәртебесі": status_names.get(row["status_id"], "Белгісіз"),
            "Бағыты": row["direction"],
            "Басымдығы": row["priority"],
            "Тапсыру мерзімі": row["due_date"] or "Көрсетілмеген",
        }
        for row in rows
    ]
    st.dataframe(pd.DataFrame(table), hide_index=True)
else:
    st.info("Сүзгіге сәйкес құжат табылмады.")

st.divider()
st.subheader("Жаңа құжат тіркеу")
if not type_by_name:
    st.warning("Белсенді құжат түрлері жоқ.")
else:
    with st.form("create_document", clear_on_submit=True):
        title = st.text_input("Құжат тақырыбы", max_chars=250)
        description = st.text_area("Қысқаша сипаттама", max_chars=3000)
        col1, col2 = st.columns(2)
        with col1:
            type_name = st.selectbox("Құжат түрі", list(type_by_name))
            direction = st.selectbox("Құжат бағыты", ["Кіріс", "Шығыс", "Ішкі"])
        with col2:
            department_name = st.selectbox("Бөлім", list(department_by_name))
            priority = st.selectbox("Басымдығы", ["Төмен", "Қалыпты", "Жоғары", "Шұғыл"])
        submitted = st.form_submit_button("Құжатты тіркеу", type="primary")

    if submitted:
        if len(title.strip()) < 3:
            st.warning("Тақырып кемінде 3 таңбадан тұруы керек.")
        else:
            payload = {
                "title": title.strip(),
                "description": description.strip() or None,
                "document_type_id": type_by_name[type_name],
                "department_id": department_by_name[department_name],
                "direction": direction,
                "priority": priority,
            }
            try:
                created = api_request("POST", "/documents", payload=payload, token=token)
                st.session_state["success_notice"] = f"{created['registration_number']} нөмірлі құжат тіркелді."
                st.rerun()
            except requests.RequestException as error:
                show_api_error(error)

can_delete = not auth_required or (profile is not None and profile["role"] == "Administrator")
if can_delete and rows:
    st.divider()
    st.subheader("Құжатты жою")
    by_id = {row["document_id"]: row for row in rows}

    def document_label(document_id: int) -> str:
        row = by_id[document_id]
        return f"{row['registration_number']} · {row['title']}"

    with st.form("delete_document", clear_on_submit=True):
        document_id = st.selectbox(
            "Құжат", list(by_id), index=None, format_func=document_label, placeholder="Құжатты таңдаңыз"
        )
        confirmed = st.checkbox("Құжатты жоюды растаймын")
        delete_submitted = st.form_submit_button("Жою")

    if delete_submitted:
        if document_id is None or not confirmed:
            st.warning("Құжатты таңдап, жоюды растаңыз.")
        else:
            try:
                api_request("DELETE", f"/documents/{document_id}", token=token)
                number = by_id[document_id]["registration_number"]
                st.session_state["success_notice"] = f"{number} нөмірлі құжат жойылды."
                st.rerun()
            except requests.RequestException as error:
                show_api_error(error)

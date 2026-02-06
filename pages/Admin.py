import streamlit as st
import sqlite3
from datetime import datetime
import pandas as pd
import os

DB_NAME = "taxi.db"

rate_nal = 0.78
rate_card = 0.75

# ===== ПРОСТАЯ АВТОРИЗАЦИЯ ДЛЯ АДМИНКИ =====
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "changeme")


def check_admin_auth() -> bool:
    """Простая проверка пароля, состояние держим в session_state."""
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False

    if st.session_state.admin_authenticated:
        return True

    st.subheader("🔐 Вход в режим администрирования")
    with st.form("admin_login"):
        pwd = st.text_input("Пароль администратора", type="password")
        ok = st.form_submit_button("Войти")

    if ok:
        if pwd == ADMIN_PASSWORD:
            st.session_state.admin_authenticated = True
            st.success("Доступ к администрированию открыт.")
            return True
        else:
            st.error("Неверный пароль.")
            return False

    return False


# ===== БАЗА / ХЕЛПЕРЫ =====
def get_connection():
    return sqlite3.connect(DB_NAME)


def safe_str_cell(v, default: str = "") -> str:
    """Строка из ячейки: пустые/NaN -> default."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    s = str(v).strip()
    return s if s != "" else default


def safe_num_cell(v, default: float = 0.0) -> float | None:
    """Число из ячейки: пустые/NaN/мусор -> default (или None)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    s = str(v).strip().replace(",", ".")
    if s == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def get_accumulated_beznal():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT total_amount FROM accumulated_beznal WHERE driver_id = 1")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0.0


def recalc_full_db():
    """Пересчитать комиссию, total и безнал по всем заказам и обновить accumulated_beznal."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, type, amount, tips FROM orders")
    rows = cur.fetchall()

    for order_id, typ, amount, tips in rows:
        amount_f = float(amount or 0)
        tips_f = float(tips or 0)

        if typ == "нал":
            final_wo_tips = amount_f
            commission = amount_f * (1 - rate_nal)
            total = amount_f + tips_f
            beznal_added = -commission
        else:
            final_wo_tips = amount_f * rate_card
            commission = amount_f - final_wo_tips
            total = final_wo_tips + tips_f
            beznal_added = final_wo_tips

        cur.execute(
            """
            UPDATE orders
            SET commission = ?, total = ?, beznal_added = ?
            WHERE id = ?
            """,
            (commission, total, beznal_added, order_id),
        )

    # пересчёт накопленного безнала
    cur.execute("SELECT COALESCE(SUM(beznal_added), 0) FROM orders")
    total_beznal = cur.fetchone()[0] or 0.0

    cur.execute("SELECT id FROM accumulated_beznal WHERE driver_id = 1")
    row = cur.fetchone()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if row:
        cur.execute(
            """
            UPDATE accumulated_beznal
            SET total_amount = ?, last_updated = ?
            WHERE driver_id = 1
            """,
            (total_beznal, now),
        )
    else:
        cur.execute(
            """
            INSERT INTO accumulated_beznal (driver_id, total_amount, last_updated)
            VALUES (1, ?, ?)
            """,
            (total_beznal, now),
        )

    conn.commit()
    conn.close()


def import_from_excel(uploaded_file) -> int:
    """
    Импорт из Excel/CSV.
    Строка без суммы или без даты не создаёт смену.
    Даты нормализуются в YYYY-MM-DD.
    """
    try:
        if uploaded_file.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)

        df.columns = [str(c).strip() for c in df.columns]
        st.write("📋 Найдены колонки:", df.columns.tolist())

        if "Сумма" not in df.columns:
            st.error("❌ В файле нет колонки 'Сумма'.")
            return 0

        df["Сумма"] = df["Сумма"].replace(r"^\s*$", pd.NA, regex=True)
        df_clean = df[df["Сумма"].notna()].copy()

        st.write(f"📊 Найдено строк с данными (Сумма не пустая): {len(df_clean)}")
        st.write("Первые 5 строк:", df_clean.head())

        if len(df_clean) == 0:
            st.error("❌ В файле нет строк с суммой!")
            return 0

        imported = 0
        errors = 0

        conn = get_connection()
        cur = conn.cursor()

        for idx, row in df_clean.iterrows():
            try:
                raw_amount = row.get("Сумма")
                amount_f = safe_num_cell(raw_amount, default=None)
                if amount_f is None:
                    st.warning(
                        f"❌ Строка {idx}: пустая или некорректная сумма ({raw_amount!r}), пропускаю."
                    )
                    errors += 1
                    continue

                raw_date = row.get("Дата")
                date_raw = safe_str_cell(raw_date)
                if not date_raw:
                    st.warning(
                        f"❌ Строка {idx}: пустая дата при сумме {amount_f}, пропускаю."
                    )
                    errors += 1
                    continue

                # нормализация даты
                dt = pd.to_datetime(date_raw, dayfirst=True, errors="coerce")
                if pd.isna(dt):
                    st.warning(
                        f"❌ Строка {idx}: не удалось разобрать дату {date_raw!r}, пропускаю."
                    )
                    errors += 1
                    continue

                date_str = dt.strftime("%Y-%m-%d")

                cur.execute("SELECT id FROM shifts WHERE date = ?", (date_str,))
                s = cur.fetchone()
                if s:
                    shift_id = s[0]
                else:
                    cur.execute(
                        "INSERT INTO shifts (date, is_open, opened_at, closed_at) "
                        "VALUES (?, 0, ?, ?)",
                        (date_str, date_str, date_str),
                    )
                    shift_id = cur.lastrowid

                raw_type = row.get("Тип", "нал")
                raw_type_str = safe_str_cell(raw_type, default="нал").lower()
                if raw_type_str in ("безнал", "card", "карта"):
                    typ = "карта"
                else:
                    typ = "нал"

                raw_tips = row.get("Чаевые")
                tips_f = safe_num_cell(raw_tips, default=0.0)

                if typ == "нал":
                    final_wo_tips = amount_f
                    commission = amount_f * (1 - rate_nal)
                    total = amount_f + tips_f
                    beznal_added = -commission
                else:
                    final_wo_tips = amount_f * rate_card
                    commission = amount_f - final_wo_tips
                    total = final_wo_tips + tips_f
                    beznal_added = final_wo_tips

                cur.execute(
                    """
                    INSERT INTO orders (shift_id, type, amount, tips, commission, total, beznal_added, order_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        shift_id,
                        typ,
                        amount_f,
                        tips_f,
                        commission,
                        total,
                        beznal_added,
                        None,
                    ),
                )

                if beznal_added != 0:
                    cur.execute(
                        """
                        UPDATE accumulated_beznal
                        SET total_amount = total_amount + ?
                        WHERE driver_id = 1
                        """,
                        (beznal_added,),
                    )

                imported += 1
            except Exception as e:
                st.warning(f"⚠️ Строка {idx}: {e}")
                errors += 1
                continue

        conn.commit()
        conn.close()

        if imported > 0:
            st.success(f"✅ Импортировано: {imported} заказов")
        if errors > 0:
            st.warning(f"⚠️ Ошибок при импорте: {errors}")
        return imported

    except Exception as e:
        st.error(f"❌ Ошибка чтения файла: {e}")
        return 0


def reset_db():
    """Полный сброс базы и создание пустых таблиц."""
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            km INTEGER DEFAULT 0,
            fuel_liters REAL DEFAULT 0,
            fuel_price REAL DEFAULT 0,
            is_open INTEGER DEFAULT 1,
            opened_at TEXT,
            closed_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id INTEGER,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            tips REAL DEFAULT 0,
            commission REAL NOT NULL,
            total REAL NOT NULL,
            beznal_added REAL DEFAULT 0,
            order_time TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS accumulated_beznal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER DEFAULT 1,
            total_amount REAL DEFAULT 0,
            last_updated TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def import_from_gsheet(sheet_url: str) -> int:
    """
    Импортирует заказы из Google Sheets.
    Пустые даты или строки без суммы не создают смену.
    Даты нормализуются в YYYY-MM-DD.
    """
    try:
        base_url = sheet_url.split("#")[0]
        csv_url = base_url.replace("/edit?gid=", "/export?format=csv&gid=")
        df = pd.read_csv(csv_url)
    except Exception as e:
        st.error(f"❌ Не удалось прочитать данные из Google Sheets: {e}")
        return 0

    df.columns = [str(c).strip() for c in df.columns]
    st.write("📋 Найдены колонки в Google Sheets:", df.columns.tolist())

    if "Сумма" not in df.columns:
        st.error("❌ В таблице нет колонки 'Сумма'.")
        return 0

    df["Сумма"] = df["Сумма"].replace(r"^\s*$", pd.NA, regex=True)
    df_clean = df[df["Сумма"].notna()].copy()

    st.write(f"📊 Найдено строк с данными (Сумма не пустая): {len(df_clean)}")
    st.write("Первые 5 строк:", df_clean.head())

    if len(df_clean) == 0:
        st.error("❌ В таблице нет строк с суммой!")
        return 0

    imported = 0
    errors = 0

    conn = get_connection()
    cur = conn.cursor()

    for idx, row in df_clean.iterrows():
        try:
            raw_amount = row.get("Сумма")
            amount_f = safe_num_cell(raw_amount, default=None)
            if amount_f is None:
                st.warning(
                    f"❌ Строка {idx}: пустая или некорректная сумма ({raw_amount!r}), пропускаю."
                )
                errors += 1
                continue

            raw_date = row.get("Дата")
            date_raw = safe_str_cell(raw_date)
            if not date_raw:
                st.warning(
                    f"❌ Строка {idx}: пустая дата при сумме {amount_f}, пропускаю."
                )
                errors += 1
                continue

            # нормализация даты из Google Sheets
            dt = pd.to_datetime(date_raw, dayfirst=True, errors="coerce")
            if pd.isna(dt):
                st.warning(
                    f"❌ Строка {idx}: не удалось разобрать дату {date_raw!r}, пропускаю."
                )
                errors += 1
                continue

            date_str = dt.strftime("%Y-%m-%d")

            cur.execute("SELECT id FROM shifts WHERE date = ?", (date_str,))
            s = cur.fetchone()
            if s:
                shift_id = s[0]
            else:
                cur.execute(
                    "INSERT INTO shifts (date, is_open, opened_at, closed_at) "
                    "VALUES (?, 0, ?, ?)",
                    (date_str, date_str, date_str),
                )
                shift_id = cur.lastrowid

            raw_type = row.get("Тип", "нал")
            raw_type_str = safe_str_cell(raw_type, default="нал").lower()
            if raw_type_str in ("безнал", "card", "карта"):
                typ = "карта"
            else:
                typ = "нал"

            raw_tips = row.get("Чаевые")
            tips_f = safe_num_cell(raw_tips, default=0.0)

            if typ == "нал":
                final_wo_tips = amount_f
                commission = amount_f * (1 - rate_nal)
                total = amount_f + tips_f
                beznal_added = -commission
            else:
                final_wo_tips = amount_f * rate_card
                commission = amount_f - final_wo_tips
                total = final_wo_tips + tips_f
                beznal_added = final_wo_tips

            cur.execute(
                """
                INSERT INTO orders (shift_id, type, amount, tips, commission, total, beznal_added, order_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shift_id,
                    typ,
                    amount_f,
                    tips_f,
                    commission,
                    total,
                    beznal_added,
                    None,
                ),
            )

            if beznal_added != 0:
                cur.execute(
                    """
                    UPDATE accumulated_beznal
                    SET total_amount = total_amount + ?
                    WHERE driver_id = 1
                    """,
                    (beznal_added,),
                )

            imported += 1
        except Exception as e:
            st.warning(f"⚠️ Строка {idx}: {e}")
            errors += 1
            continue

    conn.commit()
    conn.close()

    if imported > 0:
        st.success(f"✅ Импортировано из Google Sheets: {imported} заказов")
    if errors > 0:
        st.warning(f"⚠️ Ошибок при импорте: {errors}")
    return imported


# ===== UI / ЗАПУСК СТРАНИЦЫ =====
st.set_page_config(page_title="Администрирование", page_icon="🛠", layout="centered")
st.title("🛠 Администрирование")

if not check_admin_auth():
    st.stop()

# 0. Импорт из Google Sheets
with st.expander("📄 Заливка базы из Google Sheets", expanded=False):
    st.caption(
        "Таблица должна быть доступна по ссылке (Anyone with link, Viewer). "
        "Формат колонок: Дата, Тип, Сумма, Чаевые."
    )

    default_url = (
        "https://docs.google.com/spreadsheets/d/"
        "1USdDnw5OnzcIgC0mBVWGKURDJox4ncc5SAUQn-euS3Q/edit?gid=0#gid=0"
    )

    sheet_url = st.text_input("Ссылка на Google Sheets", value=default_url)

    if st.button("Импортировать из Google Sheets"):
        imported = import_from_gsheet(sheet_url)
        if imported > 0:
            st.info("После импорта можно открыть страницу Reports и посмотреть отчёты.")

# 1. Импорт из файла (Excel / CSV)
with st.expander("📂 Импорт из файла (Excel / CSV)", expanded=False):
    uploaded_file = st.file_uploader(
        "Выберите файл Excel или CSV", type=["xlsx", "xls", "csv"]
    )
    if uploaded_file is not None:
        if st.button("Импортировать из файла"):
            imported = import_from_excel(uploaded_file)
            if imported > 0:
                st.info("Импорт завершён. Проверьте данные в отчётах (страница Reports).")

# 2. Пересчёт базы
with st.expander("🔄 Пересчитать комиссии и безнал по всем заказам", expanded=False):
    if st.button("Пересчитать всё"):
        recalc_full_db()
        st.success("Пересчёт завершён.")
    st.write(f"Текущий накопленный безнал: {get_accumulated_beznal():.0f} ₽")

# 3. Сброс базы
with st.expander("⚠️ Полный сброс базы", expanded=False):
    st.warning(
        "Эта операция удалит все смены и заказы и создаст пустую базу заново. "
        "Используйте только если точно понимаете, что делаете."
    )
    if st.button("Удалить базу и создать заново"):
        reset_db()
        st.success("База сброшена и создана заново.")

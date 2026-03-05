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


# ===== БАЗА / ХЕЛПЕРЫ =====

def get_connection():
    return sqlite3.connect(DB_NAME)


def safe_str_cell(v, default=""):
    """Строка из ячейки: пустые/NaN -> default."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    s = str(v).strip()
    return s if s != "" else default


def safe_num_cell(v, default=0.0):
    """Число из ячейки: пустые/NaN/мусор -> default."""
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
                # 1) СУММА
                raw_amount = row.get("Сумма")
                amount_f = safe_num_cell(raw_amount, default=None)
                if amount_f is None:
                    st.warning(
                        f"❌ Строка {idx}: пустая или некорректная сумма ({raw_amount!r}), пропускаю."
                    )
                    errors += 1
                    continue

                # 2) ДАТА
                raw_date = row.get("Дата")
                date_str = safe_str_cell(raw_date)
                if not date_str:
                    st.warning(
                        f"❌ Строка {idx}: пустая дата при сумме {amount_f}, пропускаю."
                    )
                    errors += 1
                    continue

                # 3) СМЕНА
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

                # 4) ТИП ОПЛАТЫ
                raw_type = row.get("Тип", "нал")
                raw_type_str = safe_str_cell(raw_type, default="нал").lower()
                if raw_type_str in ("безнал", "card", "карта"):
                    typ = "карта"
                else:
                    typ = "нал"

                # 5) ЧАЕВЫЕ
                raw_tips = row.get("Чаевые")
                tips_f = safe_num_cell(raw_tips, default=0.0)

                # 6) Расчёты
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
            date_str = safe_str_cell(raw_date)
            if not date_str:
                st.warning(
                    f"❌ Строка {idx}: пустая дата при сумме {amount_f}, пропускаю."
                )
                errors += 1
                continue

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

    default_url = "https://docs.google.com/spreadsheets/d/1USdDnw5OnzcIgC0mBVWGKURDJox4ncc5SAUQn-euS3Q/edit?gid=0#gid=0"
    sheet_url = st.text_input("Ссылка на Google Sheets", value=default_url)

    if st.button("Импортировать из Google Sheets", width="stretch"):
        with st.spinner("Читаем данные из Google Sheets..."):
            count = import_from_gsheet(sheet_url)
        if count <= 0:
            st.warning("Не удалось импортировать из Google Sheets. Проверь ссылку и формат колонок.")

# 1. Импорт из файла
with st.expander("📥 Импорт из файла 'Работа такси' (Excel/CSV)", expanded=False):
    st.caption(
        "Поддерживаются .xlsx, .xls, .csv с колонками: Дата, Тип, Сумма/Приход, Чаевые (необязательно)."
    )

    uploaded = st.file_uploader("Выберите файл", type=["xlsx", "xls", "csv"])
    if uploaded is not None:
        if st.button("Импортировать", width="stretch"):
            with st.spinner("Импортируем данные..."):
                count = import_from_excel(uploaded)
            if count > 0:
                st.success(f"✓ Импортировано заказов: {count}")
                acc = get_accumulated_beznal()
                st.info(f"Текущий накопленный безнал: {acc:.2f} ₽")
            else:
                st.warning("Не удалось импортировать данные. Проверьте формат файла.")

# 2. Ручная корректировка безнала
with st.expander("🔧 Ручная корректировка накопленного безнала", expanded=False):
    current_acc = get_accumulated_beznal()
    st.write(f"Текущее значение: {current_acc:.2f} ₽")

    new_value = st.number_input(
        "Новое значение, ₽",
        min_value=0.0,
        value=float(current_acc),
        step=100.0,
        format="%.2f",
        key="manual_beznal",
    )

    if st.button("💾 Установить", width="stretch", key="btn_set_beznal"):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE accumulated_beznal
            SET total_amount = ?, last_updated = ?
            WHERE driver_id = 1
            """,
            (new_value, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()
        st.success(f"Накопленный безнал обновлён до {new_value:.2f} ₽")
        st.rerun()

# 3. Пересчёт базы
with st.expander("🔁 Пересчитать базу", expanded=False):
    st.caption(
        "Пересчитывает commission, total и beznal_added по всем заказам "
        "и заново собирает накопленный безнал по текущей логике."
    )

    if "confirm_recalc_db" not in st.session_state:
        st.session_state.confirm_recalc_db = False

    if not st.session_state.confirm_recalc_db:
        if st.button("Пересчитать базу", width="stretch", key="btn_recalc"):
            st.session_state.confirm_recalc_db = True
    else:
        st.error("БУДУТ ПЕРЕСЧИТАНЫ ВСЕ ЗАКАЗЫ И НАКОПЛЕННЫЙ БЕЗНАЛ ПО НОВОЙ ЛОГИКЕ.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Да", width="stretch", key="recalc_yes"):
                recalc_full_db()
                new_acc = get_accumulated_beznal()
                st.session_state.confirm_recalc_db = False
                st.success(
                    f"Готово! База пересчитана. Новый накопленный безнал: {new_acc:.2f} ₽"
                )
                st.stop()
        with c2:
            if st.button("Отмена", width="stretch", key="recalc_no"):
                st.session_state.confirm_recalc_db = False

# 4. Обнуление базы
with st.expander("⚠ Обнуление базы данных", expanded=False):
    st.caption(
        "Удаляет все смены, заказы и накопленный безнал. "
        "Используйте только если хотите начать учёт с нуля."
    )

    if "confirm_reset" not in st.session_state:
        st.session_state.confirm_reset = False

    if not st.session_state.confirm_reset:
        if st.button("Обнулить базу", width="stretch", key="btn_reset"):
            st.session_state.confirm_reset = True
    else:
        st.error("Все смены, заказы и отчёты будут удалены безвозвратно!")
        r1, r2 = st.columns(2)
        with r1:
            if st.button("Да, удалить", width="stretch", key="reset_yes"):
                reset_db()
                st.session_state.confirm_reset = False
                st.success("База очищена. Можно начинать заново.")
                st.stop()
        with r2:
            if st.button("Отмена", width="stretch", key="reset_no"):
                st.session_state.confirm_reset = False
                st.info("Обнуление базы отменено.")

import streamlit as st
import sqlite3
from datetime import datetime
import pandas as pd
import os
import shutil

DB_NAME = "taxi.db"

rate_nal = 0.78
rate_card = 0.75

BACKUP_DIR = "backups"

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


def ensure_accum_row(cur):
    """Гарантируем, что есть строка driver_id=1 в accumulated_beznal."""
    cur.execute("SELECT id FROM accumulated_beznal WHERE driver_id = 1")
    row = cur.fetchone()
    if not row:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO accumulated_beznal (driver_id, total_amount, last_updated)
            VALUES (1, 0, ?)
            """,
            (now,),
        )


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


def parse_date_to_iso(v) -> str | None:
    """
    Универсальный парсер даты:
    возвращает строку YYYY-MM-DD или None, если распарсить не удалось.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None

    from datetime import date as _date, datetime as _dt

    if isinstance(v, (_dt, _date, pd.Timestamp)):
        dt = pd.to_datetime(v).date()
        return dt.strftime("%Y-%m-%d")

    s = str(v).strip()
    if not s:
        return None

    fmts = [
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%Y.%m.%d",
    ]
    for fmt in fmts:
        try:
            dt = _dt.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.date().strftime("%Y-%m-%d")


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
        ensure_accum_row(cur)

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

                iso_date = parse_date_to_iso(row.get("Дата"))
                if not iso_date:
                    st.warning(
                        f"❌ Строка {idx}: не удалось разобрать дату при сумме {amount_f}, пропускаю."
                    )
                    errors += 1
                    continue

                cur.execute("SELECT id FROM shifts WHERE date = ?", (iso_date,))
                s = cur.fetchone()
                if s:
                    shift_id = s[0]
                else:
                    cur.execute(
                        "INSERT INTO shifts (date, is_open, opened_at, closed_at) "
                        "VALUES (?, 0, ?, ?)",
                        (iso_date, iso_date, iso_date),
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

    ensure_accum_row(cur)

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
    ensure_accum_row(cur)

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

            iso_date = parse_date_to_iso(row.get("Дата"))
            if not iso_date:
                st.warning(
                    f"❌ Строка {idx}: не удалось разобрать дату при сумме {amount_f}, пропускаю."
                )
                errors += 1
                continue

            cur.execute("SELECT id FROM shifts WHERE date = ?", (iso_date,))
            s = cur.fetchone()
            if s:
                shift_id = s[0]
            else:
                cur.execute(
                    "INSERT INTO shifts (date, is_open, opened_at, closed_at) "
                    "VALUES (?, 0, ?, ?)",
                    (iso_date, iso_date, iso_date),
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


def normalize_shift_dates():
    """
    Привести все даты в shifts к формату YYYY-MM-DD.
    Понимает старые форматы:
      - '02.02.2026', '02-02-2026', '02/02/2026'
      - '2026/02/07', '2026.02.07'
      - '2026-02-07'
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, date FROM shifts")
    rows = cur.fetchall()

    fixed = 0
    skipped = 0

    for shift_id, date_str in rows:
        new_val = parse_date_to_iso(date_str)
        s = str(date_str).strip() if date_str is not None else ""
        if new_val and new_val != s:
            cur.execute("UPDATE shifts SET date = ? WHERE id = ?", (new_val, shift_id))
            fixed += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()
    return fixed, skipped


# ===== БЭКАПЫ =====
def ensure_backup_dir():
    """Создаёт папку для бэкапов, если её ещё нет."""
    if not os.path.isdir(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)


def create_backup() -> str:
    """
    Делает копию DB_NAME в папку backups.
    Имя вида: taxi_backup_YYYYMMDD_HHMMSS.db
    Возвращает полный путь к созданному файлу.
    """
    ensure_backup_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"taxi_backup_{ts}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    shutil.copy2(DB_NAME, backup_path)
    return backup_path


def list_backups() -> list[tuple[str, str]]:
    """
    Возвращает список (метка_для_показа, полный_путь) для всех файлов бэкапа.
    Метка содержит дату/время сохранения.
    """
    if not os.path.isdir(BACKUP_DIR):
        return []

    files = [
        os.path.join(BACKUP_DIR, f)
        for f in os.listdir(BACKUP_DIR)
        if f.endswith(".db")
    ]
    if not files:
        return []

    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    result = []
    for path in files:
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        label = f"{mtime.strftime('%d.%m.%Y %H:%M:%S')} — {os.path.basename(path)}"
        result.append((label, path))
    return result


def restore_backup(path: str):
    """
    Перезаписывает основную базу выбранным файлом бэкапа.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Файл бэкапа не найден: {path}")
    shutil.copy2(path, DB_NAME)


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

# 2.1 Установить накопленный безнал вручную
with st.expander("✏️ Установить накопленный безнал вручную", expanded=False):
    current = get_accumulated_beznal()
    st.write(f"Сейчас в базе: {current:.0f} ₽")

    new_value = st.number_input(
        "Новое значение накопленного безнала, ₽",
        min_value=0.0,
        step=100.0,
        format="%.0f",
    )

    if st.button("Сохранить это значение в базу"):
        conn = get_connection()
        cur = conn.cursor()
        ensure_accum_row(cur)
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
        st.success(f"В базе теперь записано: {new_value:.0f} ₽")

# 2.2 Нормализовать даты смен
with st.expander("🗓 Нормализовать даты смен (исправить формат дат)", expanded=False):
    st.caption(
        "Используйте, если в отчётах месяцы определяются неправильно "
        "(например, февраль попадает в июль). "
        "Все даты в shifts будут приведены к виду ГГГГ-ММ-ДД."
    )
    if st.button("Исправить формат дат в shifts"):
        fixed, skipped = normalize_shift_dates()
        st.success(
            f"Исправлено дат: {fixed}, без изменений (уже нормальные): {skipped}."
        )

# 3. Сброс базы
with st.expander("⚠️ Полный сброс базы", expanded=False):
    st.warning(
        "Эта операция удалит все смены и заказы и создаст пустую базу заново. "
        "Используйте только если точно понимаете, что делаете."
    )
    if st.button("Удалить базу и создать заново"):
        reset_db()
        st.success("База сброшена и создана заново.")

# 4. Бэкап и восстановление базы
with st.expander("💾 Бэкап и восстановление базы", expanded=False):
    st.caption(
        "Бэкап создаёт копию файла базы данных taxi.db. "
        "Восстановление перезапишет текущую базу выбранным бэкапом."
    )

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Сделать бэкап сейчас", key="backup_now"):
            try:
                backup_path = create_backup()
                st.success(
                    f"Бэкап создан: {os.path.basename(backup_path)} "
                    f"({datetime.fromtimestamp(os.path.getmtime(backup_path)).strftime('%d.%m.%Y %H:%M:%S')})"
                )
            except Exception as e:
                st.error(f"Не удалось создать бэкап: {e}")

    with col2:
        backups = list_backups()
        if not backups:
            st.info("Пока нет ни одного файла бэкапа.")
        else:
            labels = [lbl for (lbl, _) in backups]
            paths = {lbl: p for (lbl, p) in backups}
            selected_label = st.selectbox(
                "Выберите бэкап для восстановления",
                options=labels,
                key="backup_select",
            )

            st.warning(
                "ВНИМАНИЕ: при восстановлении текущая база будет полностью "
                "перезаписана содержимым выбранного бэкапа."
            )

            if st.button("Восстановить выбранный бэкап", key="backup_restore"):
                try:
                    restore_backup(paths[selected_label])
                    st.success(
                        f"База восстановлена из бэкапа: {selected_label}"
                    )
                    st.info(
                        "Рекомендуется перезапустить приложение, чтобы все страницы перечитали данные."
                    )
                except Exception as e:
                    st.error(f"Ошибка при восстановлении из бэкапа: {e}")

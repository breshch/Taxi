import streamlit as st
import sqlite3
from datetime import datetime, date, timezone, timedelta

# ===== НАСТРОЙКИ =====
DB_NAME = "taxi.db"

rate_nal = 0.78   # процент для нала (для расчёта комиссии)
rate_card = 0.75  # процент для карты

# Московский часовой пояс
MOSCOW_TZ = timezone(timedelta(hours=3))

# ===== КАСТОМНЫЙ ДИЗАЙН / CSS =====
def apply_custom_css():
    st.markdown(
        """
        <style>
        .stApp {
            background: #f3f4f6;
            color: #111827;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
        }
        .block-container {
            padding-top: 0.8rem;
            padding-bottom: 0.8rem;
            max-width: 720px;
        }
        h1 {
            font-size: 1.6rem !important;
            text-align: center;
            margin-bottom: 0.5rem;
            color: #0f172a;
        }
        h2, h3 {
            color: #1f2933;
            font-size: 1.1rem !important;
            margin-top: 0.8rem;
            margin-bottom: 0.4rem;
        }
        .streamlit-expanderHeader {
            font-weight: 600;
            font-size: 0.95rem;
        }
        .stExpander {
            background: #ffffff !important;
            border-radius: 0.75rem !important;
            border: 1px solid #e5e7eb !important;
            padding: 0.2rem 0.4rem !important;
        }
        .stMetric {
            background-color: #ffffff;
            padding: 0.4rem 0.6rem;
            border-radius: 0.75rem;
            border: 1px solid #e5e7eb;
        }
        button[kind="primary"], button[kind="secondary"] {
            border-radius: 999px !important;
            padding-top: 0.5rem !important;
            padding-bottom: 0.5rem !important;
            font-weight: 600 !important;
            background-color: #bfdbfe !important;
            color: #111827 !important;
            border: 1px solid #93c5fd !important;
        }
        button[kind="primary"]:hover, button[kind="secondary"]:hover {
            background-color: #93c5fd !important;
            color: #111827 !important;
        }
        hr {
            margin: 0.3rem 0 !important;
            border-color: #e5e7eb;
        }
        .stForm, .stMarkdown, .stNumberInput, .stSelectbox, .stFileUploader {
            margin-bottom: 0.4rem !important;
        }
        .stContainer {
            background-color: transparent;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

# ===== ФУНКЦИИ БД =====
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
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

    cursor.execute(
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

    # на случай старой таблицы без order_time
    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN order_time TEXT")
    except sqlite3.OperationalError:
        pass

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS accumulated_beznal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER DEFAULT 1,
            total_amount REAL DEFAULT 0,
            last_updated TEXT
        )
        """
    )

    cursor.execute("SELECT id FROM accumulated_beznal WHERE driver_id = 1")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO accumulated_beznal "
            "(driver_id, total_amount, last_updated) "
            "VALUES (1, 0, ?)",
            (datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),),
        )

    conn.commit()
    conn.close()


def get_open_shift():
    """Возвращает (id, date) открытой смены или None."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, date FROM shifts WHERE is_open = 1 LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row


def open_shift(date_str: str) -> int:
    """date_str должен быть в формате YYYY-MM-DD."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO shifts (date, is_open, opened_at) VALUES (?, 1, ?)",
        (date_str, now),
    )
    shift_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return shift_id


def close_shift_db(shift_id: int, km: int, liters: float, fuel_price: float):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        UPDATE shifts
        SET is_open = 0, km = ?, fuel_liters = ?, fuel_price = ?, closed_at = ?
        WHERE id = ?
        """,
        (km, liters, fuel_price, now, shift_id),
    )
    conn.commit()
    conn.close()


def add_order_db(
    shift_id,
    order_type,
    amount,
    tips,
    commission,
    total,
    beznal_added,
    order_time,
):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO orders (shift_id, type, amount, tips, commission, total, beznal_added, order_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shift_id,
            order_type,
            amount,
            tips,
            commission,
            total,
            beznal_added,
            order_time,
        ),
    )
    conn.commit()
    conn.close()


def get_shift_orders(shift_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT type, amount, tips, commission, total, beznal_added, order_time
        FROM orders
        WHERE shift_id = ?
        ORDER BY id
        """,
        (shift_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_shift_totals(shift_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT type, SUM(total - tips) FROM orders "
        "WHERE shift_id = ? GROUP BY type",
        (shift_id,),
    )
    by_type = dict(cursor.fetchall())

    cursor.execute(
        "SELECT SUM(tips), SUM(beznal_added) FROM orders WHERE shift_id = ?",
        (shift_id,),
    )
    tips_sum, beznal_sum = cursor.fetchone()
    tips_sum = tips_sum or 0
    beznal_sum = beznal_sum or 0

    conn.close()

    by_type["чаевые"] = tips_sum
    by_type["безнал_смена"] = beznal_sum
    return by_type


def get_accumulated_beznal():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT total_amount FROM accumulated_beznal WHERE driver_id = 1")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0.0


def add_to_accumulated_beznal(amount: float):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        UPDATE accumulated_beznal
        SET total_amount = total_amount + ?, last_updated = ?
        WHERE driver_id = 1
        """,
        (amount, now),
    )
    conn.commit()
    conn.close()


def get_last_fuel_params():
    """
    Возвращает (расход_л_на_100км, цена_бензина_за_литр) из последней закрытой смены,
    либо (8.0, 55.0) по умолчанию, если данных ещё нет.
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT fuel_liters, km, fuel_price
        FROM shifts
        WHERE is_open = 0
          AND km > 0
          AND fuel_liters > 0
          AND fuel_price > 0
        ORDER BY closed_at DESC, id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return 8.0, 55.0

    fuel_liters, km, fuel_price = row
    try:
        consumption = (fuel_liters / km) * 100 if km > 0 else 8.0
    except Exception:
        consumption = 8.0

    return float(consumption or 8.0), float(fuel_price or 55.0)

# ===== UI =====
st.set_page_config(page_title="Такси учёт", page_icon="🚕", layout="centered")
apply_custom_css()
init_db()

st.title("🚕 Учёт работы такси")

open_shift_data = get_open_shift()

if not open_shift_data:
    st.info("Сейчас нет открытой смены.")

    # Простое открытие смены без шаблона пробега/часов
    with st.expander("📝 Открыть смену", expanded=True):
        with st.form("open_shift_form"):
            date_input = st.date_input(
                "Дата смены",
                value=date.today(),
            )
            st.caption(f"Выбрано: {date_input.strftime('%d/%m/%Y')}")
            submitted_tpl = st.form_submit_button("📂 Открыть смену")

        if submitted_tpl:
            # в БД сохраняем в формате YYYY-MM-DD
            date_str_db = date_input.strftime("%Y-%m-%d")
            open_shift(date_str_db)
            # пользователю показываем ДД/ММ/ГГГГ
            date_str_show = date_input.strftime("%d/%m/%Y")
            st.success(f"Смена открыта: {date_str_show}")
            st.rerun()

    st.caption("История и отчёты — на страницах Reports / Admin в левом меню.")
else:
    shift_id, date_str = open_shift_data
    # date_str хранится как YYYY-MM-DD, показываем как ДД/ММ/ГГГГ
    try:
        date_show = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        date_show = date_str
    st.success(f"📅 Открыта смена: {date_show}")

    acc = get_accumulated_beznal()
    if acc != 0:
        st.metric("Накопленный безнал", f"{acc:.0f} ₽")

    # ===== Форма добавления заказа (без 0 по умолчанию) =====
    with st.expander("➕ Добавить заказ", expanded=True):
        with st.form("order_form"):
            c1, c2 = st.columns(2)
            with c1:
                amount_str = st.text_input(
                    "Сумма заказа, ₽",
                    value="",
                    placeholder="например, 650",
                )
            with c2:
                payment = st.selectbox("Тип оплаты", ["нал", "карта"])

            tips_str = st.text_input(
                "Чаевые, ₽ (без комиссии)",
                value="",
                placeholder="0 (если без чаевых)",
            )

            now_moscow = datetime.now(MOSCOW_TZ)
            st.caption(f"Текущее время (МСК): {now_moscow.strftime('%H:%M')}")

            submitted = st.form_submit_button("💾 Сохранить заказ")

        if submitted:
            # парсим сумму
            try:
                amount = float(amount_str.replace(",", "."))
            except ValueError:
                st.error("Введите сумму заказа числом.")
                st.stop()

            if amount <= 0:
                st.error("Сумма заказа должна быть больше нуля.")
                st.stop()

            # парсим чаевые (пусто -> 0)
            tips = 0.0
            if tips_str.strip():
                try:
                    tips = float(tips_str.replace(",", "."))
                except ValueError:
                    st.error("Чаевые нужно вводить числом (или оставить пустым).")
                    st.stop()

            order_time = datetime.now(MOSCOW_TZ).strftime("%H:%M")

            if payment == "нал":
                typ = "нал"
                final_wo_tips = amount
                commission = amount * (1 - rate_nal)
                total = amount + tips
                beznal_added = -commission
            else:
                typ = "карта"
                final_wo_tips = amount * rate_card
                commission = amount - final_wo_tips
                total = final_wo_tips + tips
                beznal_added = final_wo_tips

            # попытка записи заказа
            try:
                add_order_db(
                    shift_id, typ, amount, tips, commission, total, beznal_added, order_time
                )

                if beznal_added != 0:
                    add_to_accumulated_beznal(beznal_added)

                human_type = "Нал" if typ == "нал" else "Карта"
                st.success(
                    f"Запись удачна: {human_type}, сумма {amount:.2f} ₽, "
                    f"чаевые {tips:.2f} ₽, вам {total:.2f} ₽"
                )
                st.rerun()
            except Exception as e:
                st.error(f"Ошибка при сохранении заказа: {e}")

    # ===== Список заказов и итоги =====
    orders = get_shift_orders(shift_id)
    totals = get_shift_totals(shift_id) if orders else {}
    nal = totals.get("нал", 0.0)
    card = totals.get("карта", 0.0)
    tips_sum = totals.get("чаевые", 0.0)
    beznal_this = totals.get("безнал_смена", 0.0)

    if orders:
        st.subheader("📋 Заказы за смену")
        for i, (typ, amount, tips, comm, total, beznal_add, order_time) in enumerate(
            orders, 1
        ):
            with st.container():
                left, right = st.columns([2, 1])
                with left:
                    time_str = f"{order_time} · " if order_time else ""
                    st.markdown(
                        f"**#{i}** · {time_str}"
                        f"{'💵 Нал' if typ == 'нал' else '💳 Карта'} · "
                        f"{amount:.0f} ₽"
                    )
                    details = []
                    if tips > 0:
                        details.append(f"чаевые {tips:.0f} ₽")
                    if beznal_add > 0:
                        details.append(f"+{beznal_add:.0f} ₽ в безнал")
                    elif beznal_add < 0:
                        details.append(f"{beznal_add:.0f} ₽ списано с безнала")
                    if details:
                        st.caption(", ".join(details))
                with right:
                    st.markdown(f"**Вам:** {total:.0f} ₽")
            st.divider()

        st.subheader("💼 Итоги по смене")
        top = st.container()
        bottom = st.container()
        with top:
            c1, c2 = st.columns(2)
            c1.metric("Нал", f"{nal:.0f} ₽")
            c2.metric("Карта", f"{card:.0f} ₽")
        with bottom:
            c3, c4 = st.columns(2)
            c3.metric("Чаевые", f"{tips_sum:.0f} ₽")
            c4.metric("Изм. безнала", f"{beznal_this:.0f} ₽")

        total_day = nal + card + tips_sum
        st.caption(f"Всего за смену (до бензина): {total_day:.0f} ₽")

    # ===== Закрытие смены =====
    st.write("---")
    with st.expander("🔒 Закрыть смену (километраж)"):
        # последние параметры топлива
        last_consumption, last_price = get_last_fuel_params()

        with st.form("close_form"):
            km = st.number_input(
                "Километраж за смену (км)", min_value=0, step=10
            )

            col1, col2 = st.columns(2)
            with col1:
                consumption = st.number_input(
                    "Расход, л на 100 км",
                    min_value=0.0,
                    step=0.5,
                    value=float(f"{last_consumption:.1f}"),
                    format="%.1f",
                )
            with col2:
                fuel_price = st.number_input(
                    "Цена бензина, ₽/л",
                    min_value=0.0,
                    step=1.0,
                    value=float(f"{last_price:.1f}"),
                    format="%.1f",
                )

            if km > 0 and consumption > 0 and fuel_price > 0:
                liters = (km / 100) * consumption
                fuel_cost = liters * fuel_price
                st.write(
                    f"Расход: {liters:.1f} л, бензин: {fuel_cost:.2f} ₽"
                )
            else:
                liters = 0.0
                fuel_cost = 0.0

            submitted_close = st.form_submit_button("🔒 Закрыть смену")

        if submitted_close:
            if km > 0 and consumption > 0 and fuel_price > 0:
                liters = (km / 100) * consumption
                fuel_cost = liters * fuel_price
            else:
                liters = 0.0
                fuel_cost = 0.0

            close_shift_db(shift_id, km, liters, fuel_price)

            income = nal + card + tips_sum
            profit = income - fuel_cost

            st.success("Смена закрыта.")
            r1, r2, r3 = st.columns(3)
            r1.metric("Доход", f"{income:.0f} ₽")
            r2.metric("Бензин", f"{fuel_cost:.0f} ₽")
            r3.metric("Чистая прибыль", f"{profit:.0f} ₽")
            st.info("Проверьте отчёт в разделе Reports / Admin для детализации.")

import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime

DB_NAME = "taxi.db"

# ===== Работа с БД =====
def get_connection():
    return sqlite3.connect(DB_NAME)


def get_available_year_months():
    """
    Месяцы только по закрытым сменам, у которых есть хотя бы один заказ.
    Формат: 'YYYY-MM'.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT strftime('%Y-%m', date)
        FROM shifts
        WHERE date IS NOT NULL
          AND TRIM(date) <> ''
          AND is_open = 0
          AND EXISTS (SELECT 1 FROM orders o WHERE o.shift_id = shifts.id)
        ORDER BY 1 DESC
        """
    )
    rows = cur.fetchall()
    conn.close()

    res = []
    for (val,) in rows:
        if val is None:
            continue
        s = str(val)
        if len(s) >= 7 and s[0:4].isdigit() and s[5:7].isdigit():
            res.append(s)
    return res


def get_current_accumulated_beznal() -> float:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT total_amount FROM accumulated_beznal WHERE driver_id = 1"
    )
    row = cur.fetchone()
    conn.close()
    return float(row[0]) if row and row[0] is not None else 0.0


def get_month_totals(year_month: str):
    """
    Итоги за месяц по ЗАКРЫТЫМ сменам, где есть хотя бы один заказ.
    year_month в формате 'YYYY-MM'.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id
        FROM shifts
        WHERE date LIKE ?
          AND is_open = 0
          AND EXISTS (SELECT 1 FROM orders o WHERE o.shift_id = shifts.id)
        """,
        (f"{year_month}%",),
    )
    shifts = cur.fetchall()

    total_nal = 0.0
    total_card = 0.0
    total_tips = 0.0
    total_beznal_add = 0.0

    for (shift_id,) in shifts:
        cur.execute(
            "SELECT type, SUM(total - tips) "
            "FROM orders WHERE shift_id = ? GROUP BY type",
            (shift_id,),
        )
        for typ, summ in cur.fetchall():
            summ = summ or 0.0
            if typ == "нал":
                total_nal += summ
            elif typ == "карта":
                total_card += summ

        cur.execute(
            "SELECT SUM(tips), SUM(beznal_added) "
            "FROM orders WHERE shift_id = ?",
            (shift_id,),
        )
        tips_sum, beznal_sum = cur.fetchone()
        total_tips += tips_sum or 0.0
        total_beznal_add += beznal_sum or 0.0

    conn.close()
    current_acc = get_current_accumulated_beznal()

    return {
        "нал": total_nal,
        "карта": total_card,
        "чаевые": total_tips,
        "безнал_добавлено": total_beznal_add,
        "всего": total_nal + total_card + total_tips,
        "смен": len(shifts),
        "накопленный_безнал": current_acc,
    }


def get_month_shifts_details(year_month: str) -> pd.DataFrame:
    """
    Одна строка на каждую ЗАКРЫТУЮ смену, у которой есть хотя бы один заказ.
    Км/литры/цена берутся только из закрытия смены.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, date, km, fuel_liters, fuel_price
        FROM shifts
        WHERE date LIKE ?
          AND is_open = 0
          AND EXISTS (SELECT 1 FROM orders o WHERE o.shift_id = shifts.id)
        ORDER BY date
        """,
        (f"{year_month}%",),
    )
    shifts = cur.fetchall()

    rows = []
    for shift_id, date_str, km, fuel_liters, fuel_price in shifts:
        cur.execute(
            "SELECT type, SUM(total - tips) "
            "FROM orders WHERE shift_id = ? GROUP BY type",
            (shift_id,),
        )
        by_type = {t: s for t, s in cur.fetchall()}

        cur.execute(
            "SELECT SUM(tips), SUM(beznal_added) "
            "FROM orders WHERE shift_id = ?",
            (shift_id,),
        )
        tips_sum, beznal_sum = cur.fetchone()
        tips_sum = tips_sum or 0.0
        beznal_sum = beznal_sum or 0.0

        nal = by_type.get("нал", 0.0) or 0.0
        card = by_type.get("карта", 0.0) or 0.0
        total = nal + card + tips_sum

        rows.append(
            {
                "Дата": date_str,
                "Нал": nal,
                "Карта": card,
                "Чаевые": tips_sum,
                "Δ безнал": beznal_sum,
                "Км": km or 0,
                "Литры": fuel_liters or 0.0,
                "Цена": fuel_price or 0.0,
                "Всего": total,
            }
        )

    conn.close()
    df = pd.DataFrame(rows)
    if not df.empty:
        df.index = list(range(1, len(df) + 1))
    return df


def get_closed_shift_id_by_date(date_str: str):
    """id ЗАКРЫТОЙ смены по дате (date_str формата YYYY-MM-DD)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM shifts WHERE date = ? AND is_open = 0 ORDER BY id LIMIT 1",
        (date_str,),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def get_shift_orders_df(shift_id: int | None) -> pd.DataFrame:
    """Заказы в смене: одна строка = один заказ."""
    if shift_id is None:
        return pd.DataFrame()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT type, amount, tips, beznal_added, total, order_time
        FROM orders
        WHERE shift_id = ?
        ORDER BY id
        """,
        (shift_id,),
    )
    rows = cur.fetchall()
    conn.close()

    data = []
    for typ, amount, tips, beznal_added, total, order_time in rows:
        data.append(
            {
                "Время": order_time or "",
                "Тип": "Нал" if typ == "нал" else "Карта",
                "Сумма": amount or 0.0,
                "Чаевые": tips or 0.0,
                "Δ безнал": beznal_added or 0.0,
                "Вам": total or 0.0,
            }
        )

    df = pd.DataFrame(data)
    if not df.empty:
        df.index = list(range(1, len(df) + 1))
    return df


def get_orders_by_hour(date_str: str) -> pd.DataFrame:
    """
    Кол-во заказов по часам за дату (date_str формата YYYY-MM-DD).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT o.order_time
        FROM orders o
        JOIN shifts s ON o.shift_id = s.id
        WHERE s.date = ?
          AND s.is_open = 0
          AND o.order_time IS NOT NULL
        """,
        (date_str,),
    )
    rows = cur.fetchall()
    conn.close()

    times = [r[0] for r in rows]
    if not times:
        return pd.DataFrame({"Час": list(range(24)), "Заказов": [0] * 24})

    hours = []
    for t in times:
        try:
            h = int(str(t)[0:2])
            if 0 <= h <= 23:
                hours.append(h)
        except Exception:
            continue

    if not hours:
        return pd.DataFrame({"Час": list(range(24)), "Заказов": [0] * 24})

    s = pd.Series(hours)
    counts = s.value_counts().sort_index()
    df = pd.DataFrame({"Час": counts.index, "Заказов": counts.values})
    full = pd.DataFrame({"Час": list(range(24))})
    df = full.merge(df, on="Час", how="left").fillna(0)
    df["Заказов"] = df["Заказов"].astype(int)

    return df


# ===== Справочники =====
month_name = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}


def format_month_option(s) -> str:
    """Показывает 'YYYY-MM (месяц)'."""
    if s is None:
        return "—"
    s_str = str(s)
    if len(s_str) >= 7:
        mm = s_str[5:7]
        if mm.isdigit():
            m = int(mm)
            return f"{s_str} ({month_name.get(m, '')})"
    return s_str or "—"


def format_date_ddmmyyyy(s: str) -> str:
    """Форматирует YYYY-MM-DD -> DD.MM.YYYY для отображения."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return s or "—"


# ===== UI =====
st.set_page_config(page_title="Отчёты", page_icon="📊", layout="centered")
st.title("📊 Отчёты")

year_months = get_available_year_months()

if not year_months:
    st.info("Пока нет закрытых смен с заказами для формирования отчёта.")
    st.stop()

ym = st.selectbox(
    "Выберите месяц",
    year_months,
    format_func=format_month_option,
)

df_shifts = get_month_shifts_details(ym)
totals = get_month_totals(ym)

st.write("---")

# 1. ОТЧЁТ ПО ОДНОЙ СМЕНЕ
st.subheader("📄 Отчёт по смене")

if df_shifts.empty:
    st.write("Нет закрытых смен с заказами за выбранный месяц.")
else:
    available_dates = df_shifts["Дата"].unique().tolist()
    selected_date = st.selectbox(
        "Дата смены",
        options=available_dates,
        format_func=format_date_ddmmyyyy,
    )

    df_shift_summary = df_shifts[df_shifts["Дата"] == selected_date].copy()
    if not df_shift_summary.empty:
        df_shift_summary.index = list(range(1, len(df_shift_summary) + 1))
        st.dataframe(
            df_shift_summary.style.format(
                {
                    "Нал": "{:.0f}",
                    "Карта": "{:.0f}",
                    "Чаевые": "{:.0f}",
                    "Δ безнал": "{:.0f}",
                    "Км": "{:.0f}",
                    "Литры": "{:.1f}",
                    "Цена": "{:.1f}",
                    "Всего": "{:.0f}",
                }
            ),
            width="stretch",
        )

    shift_id = get_closed_shift_id_by_date(selected_date)
    st.markdown("**Заказы в смене**")
    df_orders = get_shift_orders_df(shift_id)
    if df_orders.empty:
        st.write("Нет заказов для выбранной смены.")
    else:
        st.dataframe(
            df_orders.style.format(
                {
                    "Сумма": "{:.0f}",
                    "Чаевые": "{:.0f}",
                    "Δ безнал": "{:.0f}",
                    "Вам": "{:.0f}",
                }
            ),
            width="stretch",
        )

    st.markdown("**График заказов по часам**")
    df_hours = get_orders_by_hour(selected_date)
    df_hours["Час"] = df_hours["Час"].apply(lambda h: f"{h:02d}:00")
    st.bar_chart(
        data=df_hours,
        x="Час",
        y="Заказов",
    )

# 2. ОТЧЁТ ПО СМЕНАМ ЗА МЕСЯЦ
st.write("---")
st.subheader("📅 Отчёт по сменам (таблица)")

if df_shifts.empty:
    st.write("Нет детальных данных по сменам за выбранный месяц.")
else:
    st.dataframe(
        df_shifts.style.format(
            {
                "Нал": "{:.0f}",
                "Карта": "{:.0f}",
                "Чаевые": "{:.0f}",
                "Δ безнал": "{:.0f}",
                "Км": "{:.0f}",
                "Литры": "{:.1f}",
                "Цена": "{:.1f}",
                "Всего": "{:.0f}",
            }
        ),
        width="stretch",
    )

# 1. ОТЧЁТ ПО ОДНОЙ СМЕНЕ
st.write("---")
st.subheader("📄 Отчёт по смене")

if df_shifts.empty:
    st.write("Нет закрытых смен с заказами за выбранный месяц.")
else:
    available_dates = df_shifts["Дата"].unique().tolist()
    selected_date = st.selectbox(
        "Дата смены",
        options=available_dates,
    )

    df_shift_summary = df_shifts[df_shifts["Дата"] == selected_date].copy()
    if not df_shift_summary.empty:
        df_shift_summary.index = list(range(1, len(df_shift_summary) + 1))
        st.dataframe(
            df_shift_summary.style.format(
                {
                    "Нал": "{:.0f}",
                    "Карта": "{:.0f}",
                    "Чаевые": "{:.0f}",
                    "Δ безнал": "{:.0f}",
                    "Км": "{:.0f}",
                    "Литры": "{:.1f}",
                    "Цена": "{:.1f}",
                    "Всего": "{:.0f}",
                }
            ),
            width="stretch",
        )

        # --- НОВЫЙ БЛОК: мини-отчёт по смене ---
        row = df_shift_summary.iloc[0]
        nal_shift = float(row["Нал"] or 0.0)
        card_shift = float(row["Карта"] or 0.0)
        tips_shift = float(row["Чаевые"] or 0.0)
        delta_beznal_shift = float(row["Δ безнал"] or 0.0)
        liters_shift = float(row["Литры"] or 0.0)
        price_shift = float(row["Цена"] or 0.0)
        total_shift = float(row["Всего"] or 0.0)

        fuel_cost_shift = liters_shift * price_shift
        profit_shift = total_shift - fuel_cost_shift

        st.markdown("**Краткий отчёт по выбранной смене**")
        c1, c2, c3 = st.columns(3)
        c1.metric("Нал", f"{nal_shift:.0f} ₽")
        c2.metric("Карта", f"{card_shift:.0f} ₽")
        c3.metric("Чаевые", f"{tips_shift:.0f} ₽")

        c4, c5, c6 = st.columns(3)
        c4.metric("Δ безнала", f"{delta_beznal_shift:.0f} ₽")
        c5.metric("Бензин", f"{fuel_cost_shift:.0f} ₽")
        c6.metric("Прибыль (≈)", f"{profit_shift:.0f} ₽")
        # --- КОНЕЦ НОВОГО БЛОКА ---

    shift_id = get_closed_shift_id_by_date(selected_date)

    st.markdown("**Заказы в смене**")
    df_orders = get_shift_orders_df(shift_id)
    if df_orders.empty:
        st.write("Нет заказов для выбранной смены.")
    else:
        st.dataframe(
            df_orders.style.format(
                {
                    "Сумма": "{:.0f}",
                    "Чаевые": "{:.0f}",
                    "Δ безнал": "{:.0f}",
                    "Вам": "{:.0f}",
                }
            ),
            width="stretch",
        )

    st.markdown("**График заказов по часам**")
    df_hours = get_orders_by_hour(selected_date)
    df_hours["Час"] = df_hours["Час"].apply(lambda h: f"{h:02d}:00")
    st.bar_chart(
        data=df_hours,
        x="Час",
        y="Заказов",
    )


# 3. ОТЧЁТ ЗА МЕСЯЦ (ИТОГИ)
st.write("---")
st.subheader("📊 Отчёт за месяц")

col1, col2, col3 = st.columns(3)
col1.metric("Нал", f"{totals['нал']:.0f} ₽")
col2.metric("Карта", f"{totals['карта']:.0f} ₽")
col3.metric("Чаевые", f"{totals['чаевые']:.0f} ₽")

col4, col5, col6 = st.columns(3)
col4.metric("Изм. безнала (за месяц)", f"{totals['безнал_добавлено']:.0f} ₽")
col5.metric("Накопленный безнал (текущий)", f"{totals['накопленный_безнал']:.0f} ₽")
col6.metric("Смен", f"{totals['смен']}")

total_income = totals["всего"]

# Расходы на бензин и примерная прибыль за месяц
if df_shifts.empty:
    fuel_cost = 0.0
else:
    fuel_cost = float(
        (df_shifts["Литры"].fillna(0) * df_shifts["Цена"].fillna(0)).sum()
    )

profit = total_income - fuel_cost

st.write("---")
st.subheader("💰 Финансовый результат за месяц")

c1, c2, c3 = st.columns(3)
c1.metric("Доход (всего)", f"{total_income:.0f} ₽")
c2.metric("Бензин (расход)", f"{fuel_cost:.0f} ₽")
c3.metric("Прибыль (≈)", f"{profit:.0f} ₽")

st.write(
    "_Примечание: прибыль указана приблизительно, без учёта других возможных расходов._"
)
st.write("---")     
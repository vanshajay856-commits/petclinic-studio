import os
import re
import ssl
import html
import smtplib
import sqlite3

from datetime import date, datetime
from pathlib import Path
from email.message import EmailMessage

import pandas as pd
import streamlit as st


# =========================================================
# CONFIGURATION
# =========================================================

st.set_page_config(
    page_title="PetClinic Studio",
    page_icon="🐾",
    layout="wide",
)

DB_PATH = Path(__file__).resolve().parent / "petclinic.db"

DOCTORS = ["Dr. Sharma", "Dr. Mehta", "Dr. Singh"]
SLOTS = [
    f"{hour:02d}:{minute:02d}"
    for hour in range(9, 18)
    for minute in (0, 30)
]


def setting(name, default=""):
    value = os.environ.get(name)

    if value:
        return value

    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


CLINIC_NAME = setting("CLINIC_NAME", "PetClinic Studio")


# =========================================================
# DATABASE AND MIGRATION
# =========================================================

def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS pets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                species TEXT NOT NULL,
                owner TEXT NOT NULL,
                phone TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pet_id INTEGER NOT NULL,
                visit_date TEXT NOT NULL,
                doctor TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                FOREIGN KEY (pet_id) REFERENCES pets(id)
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        pet_columns = {
            row[1] for row in db.execute("PRAGMA table_info(pets)")
        }

        if "email" not in pet_columns:
            db.execute(
                "ALTER TABLE pets ADD COLUMN email TEXT NOT NULL DEFAULT ''"
            )

        visit_columns = {
            row[1] for row in db.execute("PRAGMA table_info(visits)")
        }

        additions = {
            "appointment_time": "TEXT NOT NULL DEFAULT ''",
            "cancelled": "INTEGER NOT NULL DEFAULT 0",
            "email_status": "TEXT NOT NULL DEFAULT 'Not sent'",
        }

        for column, definition in additions.items():
            if column not in visit_columns:
                db.execute(
                    f"ALTER TABLE visits ADD COLUMN {column} {definition}"
                )


def query(sql, params=()):
    with connect() as db:
        return pd.read_sql_query(sql, db, params=params)


def execute(sql, params=()):
    with connect() as db:
        db.execute(sql, params)


def notification(message):
    execute(
        "INSERT INTO notifications (message) VALUES (?)",
        (message,),
    )


def refresh(message):
    st.session_state["flash"] = message
    st.rerun()


def valid_email(value):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))


initialize()


# =========================================================
# EMAIL
# =========================================================

def email_configured():
    return all(
        setting(key)
        for key in ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"]
    )


def send_appointment_email(visit_id, event):
    data = query("""
        SELECT v.id, v.visit_date, v.appointment_time,
               v.doctor, v.reason,
               p.name AS pet, p.owner, p.email
        FROM visits v
        JOIN pets p ON p.id = v.pet_id
        WHERE v.id = ?
    """, (int(visit_id),))

    if data.empty:
        return "Appointment not found"

    record = data.iloc[0]

    if not valid_email(record["email"]):
        result = "Not sent: owner email missing or invalid"
    elif not email_configured():
        result = "Not sent: email setup required"
    else:
        try:
            escape = lambda value: html.escape(str(value))

            message = EmailMessage()
            message["Subject"] = (
                f"{CLINIC_NAME} | Appointment {event} | #{visit_id}"
            )
            message["From"] = setting(
                "SMTP_FROM", setting("SMTP_USER")
            )
            message["To"] = record["email"]

            message.set_content(
                f"Hello {record['owner']},\n\n"
                f"Your appointment is {event}.\n\n"
                f"Reference: #{visit_id}\n"
                f"Pet: {record['pet']}\n"
                f"Doctor: {record['doctor']}\n"
                f"Date: {record['visit_date']}\n"
                f"Time: {record['appointment_time']} (clinic local time)\n\n"
                f"Thank you,\n{CLINIC_NAME}"
            )

            message.add_alternative(f"""
            <html>
            <body style="margin:0;background:#f5f4fc;
                         font-family:Arial,sans-serif;padding:25px;">
              <div style="max-width:560px;margin:auto;background:white;
                          border-radius:18px;overflow:hidden;">
                <div style="background:#5543c8;padding:28px;color:white;">
                  <h1 style="margin:0;font-size:25px;">
                    {escape(CLINIC_NAME)}
                  </h1>
                  <p style="margin-bottom:0;">
                    Appointment {escape(event)}
                  </p>
                </div>
                <div style="padding:28px;color:#292745;">
                  <p>Hello {escape(record['owner'])},</p>
                  <p>Your appointment has been {escape(event)}.</p>
                  <table style="width:100%;line-height:2;">
                    <tr><td>Reference</td><td>#{visit_id}</td></tr>
                    <tr><td>Pet</td><td>{escape(record['pet'])}</td></tr>
                    <tr><td>Doctor</td><td>{escape(record['doctor'])}</td></tr>
                    <tr><td>Date</td><td>{escape(record['visit_date'])}</td></tr>
                    <tr><td>Time</td>
                      <td>{escape(record['appointment_time'])}</td></tr>
                  </table>
                  <p style="color:#77738e;font-size:12px;">
                    All appointment times use the clinic's local time.
                  </p>
                  <p>Thank you for choosing {escape(CLINIC_NAME)}.</p>
                </div>
              </div>
            </body>
            </html>
            """, subtype="html")

            host = setting("SMTP_HOST")
            port = int(setting("SMTP_PORT", "587"))
            context = ssl.create_default_context()

            if port == 465:
                with smtplib.SMTP_SSL(
                    host, port, context=context, timeout=15
                ) as server:
                    server.login(
                        setting("SMTP_USER"),
                        setting("SMTP_PASSWORD"),
                    )
                    server.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=15) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(
                        setting("SMTP_USER"),
                        setting("SMTP_PASSWORD"),
                    )
                    server.send_message(message)

            result = f"{event.title()} email accepted by mail server"

        except Exception:
            result = "Email failed: check SMTP settings or connection"

    execute(
        "UPDATE visits SET email_status = ? WHERE id = ?",
        (result, int(visit_id)),
    )

    return result


# =========================================================
# VISUAL THEME
# =========================================================

st.markdown("""
<style>
.stApp {
    background: #f5f4fc;
    color: #282642;
}

.block-container {
    max-width: 1400px;
    padding: 2rem 2.3rem 3rem;
}

[data-testid="stSidebar"] {
    background: #171a3d;
}

[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] span {
    color: #eeeefe !important;
}

[data-testid="stSidebar"] [role="radiogroup"] {
    gap: 10px;
}

[data-testid="stSidebar"] [role="radiogroup"] label {
    border-radius: 12px;
    background: #25294e;
    border: 1px solid #363961;
    padding: 12px 14px;
}

.hero {
    background: linear-gradient(120deg, #24234f, #6450d8, #8a71ec);
    border-radius: 24px;
    padding: 34px;
    margin-bottom: 25px;
    position: relative;
    overflow: hidden;
    box-shadow: 0 12px 30px #5543c81a;
}

.hero::after {
    content: "";
    position: absolute;
    right: -50px;
    top: -80px;
    width: 260px;
    height: 260px;
    border: 40px solid #ffffff0c;
    border-radius: 50%;
    pointer-events: none;
}

.eyebrow {
    color: #c6fff0;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    margin-bottom: 15px;
}

.hero h1 {
    color: white !important;
    font-size: clamp(27px, 4vw, 39px);
    letter-spacing: -1px;
    margin: 0;
    padding: 0;
}

.hero p {
    color: #ece8ff;
    font-size: 15px;
    line-height: 1.7;
    margin: 12px 0 20px;
}

.pill {
    display: inline-block;
    border-radius: 30px;
    padding: 8px 13px;
    background: #ffffff17;
    border: 1px solid #ffffff30;
    color: white;
    font-size: 12px;
}

.metric {
    background: white;
    border-radius: 18px;
    border: 1px solid #e6e3f3;
    padding: 23px;
    margin-bottom: 18px;
    box-shadow: 0 5px 20px #29274506;
}

.metric-label {
    color: #76738c;
    font-size: 13px;
    font-weight: 600;
}

.metric-value {
    color: #292745;
    font-size: 39px;
    font-weight: 800;
    margin: 10px 0 3px;
}

.metric-note {
    color: #858198;
    font-size: 12px;
}

.violet { border-top: 4px solid #705cf0; }
.mint { border-top: 4px solid #37cba5; }
.amber { border-top: 4px solid #f5b544; }
.coral { border-top: 4px solid #ef7180; }

h2, h3 {
    color: #292745 !important;
    letter-spacing: -0.4px;
}

[data-testid="stForm"] {
    background: white;
    border: 1px solid #e6e3f3;
    border-radius: 18px;
    padding: 25px;
}

.stButton > button,
.stFormSubmitButton > button {
    min-height: 43px;
    border-radius: 10px;
    font-weight: 600;
}

button[kind="primary"] {
    background: #705cf0 !important;
    border-color: #705cf0 !important;
    color: white !important;
}

[data-testid="stDataFrame"] {
    border-radius: 13px;
    border: 1px solid #e6e3f3;
    overflow: hidden;
}

.footer {
    text-align: center;
    color: #87839a;
    font-size: 12px;
    margin-top: 35px;
    padding-top: 20px;
    border-top: 1px solid #e1dfee;
}

@media (max-width: 750px) {
    .block-container { padding: 1rem; }
    .hero { padding: 23px; }
}
</style>
""", unsafe_allow_html=True)


# =========================================================
# LOAD RECORDS
# =========================================================

pets = query("SELECT * FROM pets ORDER BY id DESC")

visits = query("""
    SELECT v.*, p.name AS pet, p.owner, p.email
    FROM visits v
    JOIN pets p ON p.id = v.pet_id
    ORDER BY v.visit_date, v.appointment_time
""")

visits["display_status"] = visits.apply(
    lambda row: "Cancelled" if row["cancelled"] else row["status"],
    axis=1,
) if not visits.empty else pd.Series(dtype="str")


# =========================================================
# NAVIGATION
# =========================================================

with st.sidebar:
    st.title("🐾 PetClinic")
    st.caption("STUDIO / CARE MANAGEMENT")

    page = st.radio(
        "Workspace",
        [
            "Dashboard",
            "Patients",
            "Book Appointment",
            "Manage Appointments",
            "Notifications",
        ],
    )

    st.divider()

    if email_configured():
        st.caption("Email configuration loaded")
    else:
        st.caption("Email setup required")

    st.caption("Appointment times use clinic local time.")


titles = {
    "Dashboard": (
        "A brighter day for better care.",
        "Your patients, appointments and clinic activity in one place.",
    ),
    "Patients": (
        "Every pet deserves a profile.",
        "Register patients and keep owner contact details organised.",
    ),
    "Book Appointment": (
        "Their next visit starts here.",
        "Choose a patient, doctor and appointment time.",
    ),
    "Manage Appointments": (
        "Stay on top of every visit.",
        "Complete, cancel or review appointments and email notifications.",
    ),
    "Notifications": (
        "Your clinic activity feed.",
        "Keep track of recent bookings and appointment updates.",
    ),
}

title, description = titles[page]

st.markdown(f"""
<div class="hero">
    <div class="eyebrow">PETCLINIC STUDIO / {page.upper()}</div>
    <h1>{title}</h1>
    <p>{description}</p>
    <span class="pill">{date.today().strftime("%A, %d %B %Y")}</span>
</div>
""", unsafe_allow_html=True)

if "flash" in st.session_state:
    st.info(st.session_state.pop("flash"))


def appointment_table(frame):
    columns = {
        "id": "Reference",
        "pet": "Pet",
        "owner": "Owner",
        "visit_date": "Date",
        "appointment_time": "Time",
        "doctor": "Doctor",
        "display_status": "Status",
        "email_status": "Email Status",
    }

    st.dataframe(
        frame[list(columns)].rename(columns=columns),
        use_container_width=True,
        hide_index=True,
    )


# =========================================================
# DASHBOARD
# =========================================================

if page == "Dashboard":
    active = visits[visits["cancelled"] == 0]
    pending = active[active["status"] == "Pending"]
    completed = active[active["status"] == "Completed"]
    today_visits = active[
        active["visit_date"] == date.today().isoformat()
    ]

    cards = [
        ("Registered Pets", len(pets), "Patient profiles", "violet"),
        ("Today's Visits", len(today_visits), "All active visits today", "mint"),
        ("Pending Visits", len(pending), "Awaiting completion", "amber"),
        ("Completed", len(completed), "All-time completed visits", "coral"),
    ]

    for column, (label, number, note, color) in zip(
        st.columns(4), cards
    ):
        with column:
            st.markdown(f"""
            <div class="metric {color}">
                <div class="metric-label">{label}</div>
                <div class="metric-value">{number}</div>
                <div class="metric-note">{note}</div>
            </div>
            """, unsafe_allow_html=True)

    left, right = st.columns(2)

    with left:
        st.subheader("Patient Mix")
        st.caption("Registered patients by animal type.")

        if pets.empty:
            st.info("Register your first patient to see this chart.")
        else:
            st.bar_chart(
                pets.groupby("species").size().rename("Patients"),
                color="#705cf0",
            )

    with right:
        st.subheader("Appointment Activity")
        st.caption("Active appointments by scheduled date.")

        if active.empty:
            st.info("Book an appointment to see activity.")
        else:
            chart = (
                active.assign(
                    day=pd.to_datetime(active["visit_date"])
                )
                .groupby("day")
                .size()
                .rename("Appointments")
                .sort_index()
            )
            st.bar_chart(chart, color="#37cba5")

    st.subheader("Today's Schedule")

    if today_visits.empty:
        st.info("No appointments scheduled for today.")
    else:
        appointment_table(today_visits)

    st.subheader("Upcoming Appointments")

    upcoming = pending[
        pending["visit_date"] > date.today().isoformat()
    ]

    if upcoming.empty:
        st.caption("No upcoming appointments.")
    else:
        appointment_table(upcoming.head(15))


# =========================================================
# PATIENTS
# =========================================================

elif page == "Patients":
    register_tab, directory_tab = st.tabs(
        ["Register Patient", "Patient Directory"]
    )

    with register_tab:
        with st.form("register_patient"):
            left, right = st.columns(2)

            with left:
                pet_name = st.text_input("Pet Name", max_chars=80)
                species = st.selectbox(
                    "Animal Type",
                    ["Dog", "Cat", "Bird", "Rabbit", "Other"],
                )
                owner = st.text_input("Owner Name", max_chars=80)

            with right:
                phone = st.text_input("Contact Number", max_chars=25)
                email = st.text_input("Owner Email", max_chars=150)
                st.caption(
                    "Appointment notifications go to this email address."
                )

            submitted = st.form_submit_button(
                "Register Patient",
                type="primary",
            )

            if submitted:
                pet_name = pet_name.strip()
                owner = owner.strip()
                phone = phone.strip()
                email = email.strip()

                if not all([pet_name, owner, phone, email]):
                    st.error("Complete all fields.")
                elif not valid_email(email):
                    st.error("Enter a valid owner email address.")
                elif not 7 <= sum(c.isdigit() for c in phone) <= 15:
                    st.error("Phone number must contain 7–15 digits.")
                else:
                    execute("""
                        INSERT INTO pets (
                            name, species, owner, phone, email
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (pet_name, species, owner, phone, email))

                    notification(f"Patient registered: {pet_name}")
                    refresh("Patient registered successfully.")

    with directory_tab:
        search = st.text_input(
            "Search patients",
            placeholder="Pet name or owner...",
        )

        filtered = pets

        if search.strip():
            filtered = pets[
                pets["name"].str.contains(
                    search.strip(), case=False, regex=False
                )
                | pets["owner"].str.contains(
                    search.strip(), case=False, regex=False
                )
            ]

        st.dataframe(
            filtered[
                ["id", "name", "species", "owner", "phone", "email"]
            ],
            use_container_width=True,
            hide_index=True,
        )

        if not pets.empty:
            st.subheader("Update Owner Email")

            labels = {
                int(row.id): f"{row.name} — {row.owner} (#{row.id})"
                for row in pets.itertuples()
            }

            selected = st.selectbox(
                "Patient",
                list(labels),
                format_func=lambda value: labels[value],
            )

            current_email = pets.loc[
                pets["id"] == selected, "email"
            ].iloc[0]

            new_email = st.text_input(
                "Email Address",
                value=current_email,
                key=f"email_{selected}",
            )

            if st.button("Save Email"):
                if valid_email(new_email.strip()):
                    execute(
                        "UPDATE pets SET email = ? WHERE id = ?",
                        (new_email.strip(), int(selected)),
                    )
                    refresh("Owner email updated.")
                else:
                    st.error("Enter a valid email address.")


# =========================================================
# BOOK APPOINTMENT
# =========================================================

elif page == "Book Appointment":
    if pets.empty:
        st.info("Register a patient first.")
    else:
        labels = {
            int(row.id): f"{row.name} — {row.owner} (#{row.id})"
            for row in pets.itertuples()
        }

        with st.form("appointment_booking"):
            pet_id = st.selectbox(
                "Patient",
                list(labels),
                format_func=lambda value: labels[value],
            )

            left, middle, right = st.columns(3)

            with left:
                visit_date = st.date_input(
                    "Appointment Date",
                    value=date.today(),
                    min_value=date.today(),
                )

            with middle:
                appointment_time = st.selectbox("Time", SLOTS)

            with right:
                doctor = st.selectbox("Veterinarian", DOCTORS)

            reason = st.text_area(
                "Reason for Visit",
                max_chars=500,
                placeholder="Routine checkup, vaccination...",
            )

            send_email = st.checkbox(
                "Send confirmation email to owner",
                value=True,
            )

            submitted = st.form_submit_button(
                "Confirm Appointment",
                type="primary",
            )

            if submitted:
                appointment_datetime = datetime.combine(
                    visit_date,
                    datetime.strptime(
                        appointment_time, "%H:%M"
                    ).time(),
                )

                if not reason.strip():
                    st.error("Enter a reason for the visit.")
                elif appointment_datetime <= datetime.now():
                    st.error("Choose a future appointment time.")
                else:
                    booked_id = None

                    # A write transaction prevents simultaneous
                    # bookings from taking the same slot.
                    with connect() as db:
                        db.execute("BEGIN IMMEDIATE")

                        conflict = db.execute("""
                            SELECT id FROM visits
                            WHERE visit_date = ?
                              AND appointment_time = ?
                              AND cancelled = 0
                              AND (doctor = ? OR pet_id = ?)
                        """, (
                            visit_date.isoformat(),
                            appointment_time,
                            doctor,
                            int(pet_id),
                        )).fetchone()

                        if not conflict:
                            cursor = db.execute("""
                                INSERT INTO visits (
                                    pet_id, visit_date, doctor,
                                    reason, appointment_time
                                ) VALUES (?, ?, ?, ?, ?)
                            """, (
                                int(pet_id),
                                visit_date.isoformat(),
                                doctor,
                                reason.strip(),
                                appointment_time,
                            ))
                            booked_id = cursor.lastrowid

                    if conflict:
                        st.error(
                            "This doctor or patient already has an "
                            "appointment at that time. Choose another slot."
                        )
                    else:
                        notification(
                            f"Appointment #{booked_id} booked for "
                            f"{visit_date} at {appointment_time}."
                        )

                        result = "Email not requested."

                        if send_email:
                            with st.spinner("Sending confirmation..."):
                                result = send_appointment_email(
                                    booked_id, "confirmed"
                                )

                        refresh(
                            f"Appointment #{booked_id} saved. {result}"
                        )


# =========================================================
# MANAGE APPOINTMENTS
# =========================================================

elif page == "Manage Appointments":
    status_filter = st.selectbox(
        "Status",
        ["All", "Pending", "Completed", "Cancelled"],
    )

    filtered = visits if status_filter == "All" else visits[
        visits["display_status"] == status_filter
    ]

    appointment_table(filtered)

    if not filtered.empty:
        labels = {
            int(row.id): (
                f"#{row.id} · {row.pet} · "
                f"{row.visit_date} {row.appointment_time}"
            )
            for row in filtered.itertuples()
        }

        selected = st.selectbox(
            "Select Appointment",
            list(labels),
            format_func=lambda value: labels[value],
        )

        record = filtered[filtered["id"] == selected].iloc[0]
        is_pending = record["display_status"] == "Pending"

        st.caption(f"Owner email: {record['email'] or 'Not provided'}")

        if st.button(
            "Mark as Completed",
            type="primary",
            disabled=not is_pending,
        ):
            execute("""
                UPDATE visits SET status = 'Completed'
                WHERE id = ? AND cancelled = 0 AND status = 'Pending'
            """, (int(selected),))

            notification(f"Appointment #{selected} completed.")
            refresh("Appointment marked as completed.")

        with st.expander("Cancel Appointment"):
            confirm_cancel = st.checkbox(
                "Confirm cancellation",
                key=f"cancel_{selected}",
            )

            email_cancel = st.checkbox(
                "Email cancellation to owner",
                value=True,
                key=f"cancel_email_{selected}",
            )

            if st.button(
                "Cancel Appointment",
                disabled=not (is_pending and confirm_cancel),
            ):
                execute("""
                    UPDATE visits SET cancelled = 1
                    WHERE id = ? AND status = 'Pending'
                """, (int(selected),))

                notification(f"Appointment #{selected} cancelled.")

                result = "Email not requested."

                if email_cancel:
                    with st.spinner("Sending cancellation..."):
                        result = send_appointment_email(
                            selected, "cancelled"
                        )

                refresh(f"Appointment cancelled. {result}")

        if record["display_status"] != "Completed":
            if st.button("Send / Retry Notification Email"):
                event = (
                    "cancelled"
                    if record["cancelled"]
                    else "confirmed"
                )

                with st.spinner("Sending email..."):
                    result = send_appointment_email(selected, event)

                refresh(result)


# =========================================================
# NOTIFICATIONS
# =========================================================

elif page == "Notifications":
    records = query("""
        SELECT message AS Activity, created_at AS "Time (UTC)"
        FROM notifications
        ORDER BY id DESC
        LIMIT 100
    """)

    if records.empty:
        st.info("New clinic activity will appear here.")
    else:
        st.dataframe(
            records,
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Email Delivery Status")
    st.caption(
        "Accepted by the mail server does not guarantee inbox delivery."
    )

    if not visits.empty:
        st.dataframe(
            visits[["id", "pet", "email", "email_status"]],
            use_container_width=True,
            hide_index=True,
        )


st.markdown("""
<div class="footer">
    PetClinic Studio · Patient care, beautifully organised.
</div>
""", unsafe_allow_html=True)
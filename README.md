# petclinic-studio
A Python and Streamlit veterinary clinic dashboard with patient management, appointment booking, and email notifications.
# PetClinic Studio 🐾

A veterinary clinic dashboard built with Python, Streamlit and SQLite.

## Features
- Patient registration and search
- Appointment booking with time slots
- Doctor and patient booking conflict checks
- Appointment completion and cancellation
- Optional confirmation and cancellation emails
- Dashboard charts and activity notifications

## Run locally
python -m pip install -r requirements.txt
python -m streamlit run app.py

## Email setup
Create .streamlit/secrets.toml locally with your SMTP configuration.
Never commit email credentials.

## Database
petclinic.db is created automatically when the app starts.
Patient records are stored locally and excluded from Git.

## Scope
Local demonstration app. Multi-user authentication and scheduled
reminders are not included.

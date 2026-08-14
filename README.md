# Pocket Money Management System

A Django web app to track budget estimates vs actual daily spending, with deficit/surplus reports by category and monthly email summaries.

## Features

- **Total Money** — Set the total amount available for a month
- **Budget Estimates** — Create spending categories with estimated amounts (default 10-day periods + custom categories like Food, Transport, Shopping)
- **Daily Expenses** — Log actual spending each day by category
- **Category Reports** — Compare budget vs actual for each category
- **Monthly Summary** — Compare total spending against your total money (deficit/surplus)
- **Email Reports** — Send the full report to the user's email

## Quick Start

```bash
# 1. Create virtual environment (optional)
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
copy .env.example .env       # Windows
# cp .env.example .env       # macOS/Linux
# Edit .env with your SMTP credentials for email reports

# 4. Run migrations
python manage.py migrate

# 5. Start the server
python manage.py runserver
```

Open **http://127.0.0.1:8000/** in your browser.

## Usage Flow

1. **Dashboard → Setup** — Register a user (name + email) and create a monthly budget with total money
2. **Dashboard → Budget** — Set estimates for the default 10-day categories (Days 1-10, 11-20, 21-end) and add custom categories (Food, Transport, etc.) with estimates
3. **Dashboard → Daily Expenses** — Record spending each day under the correct category
4. **Reports** — Select a budget month to view deficit/surplus per category and the full month
5. **Email Report** — Click the email button on the reports page to send the report to the user

## Project Structure

```
├── config/           # Django project settings
├── budget/           # Main app (models, views, services)
│   ├── models.py     # PocketUser, BudgetMonth, BudgetCategory, DailyExpense
│   ├── services/     # Report generation & email
│   └── templates/    # (in templates/budget/)
├── templates/        # HTML templates with Tailwind CSS
├── manage.py
└── requirements.txt
```

## Email Configuration

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833):

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-16-char-app-password
SMTP_FROM=your-email@gmail.com
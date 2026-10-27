"""Tests for the two independent monthly report schedules.

Schedule A — previous-month report on the 1st.
Schedule B — no-estimate overview (days 1-15) on the 16th.

Email delivery is mocked with Django's locmem backend; no real emails are sent.
These tests are fully isolated and do not touch the existing category-specific
or monthly-full automated email behaviour.
"""

from datetime import date
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings

from budget.models import (
    BudgetCategory,
    BudgetMonth,
    DailyExpense,
    PocketUser,
    ReportEmailLog,
    SubBalance,
)
from budget.services.category_service import create_default_period_categories
from budget.services.monthly_schedule_service import (
    MONTHLY_PREVIOUS,
    NO_ESTIMATE_FIRST_HALF,
    process_monthly_schedules,
    send_no_estimate_overview,
    send_previous_month_report,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST_USER="test@example.com",
    EMAIL_HOST_PASSWORD="test-password",
)
class MonthlyScheduleTests(TestCase):
    def setUp(self):
        self.user = PocketUser.objects.create(
            name="Test User", email="test@example.com"
        )
        # Previous month (October) for Schedule A.
        self.october = BudgetMonth.objects.create(
            user=self.user, year=2026, month=10, total_money=Decimal("500000")
        )
        create_default_period_categories(self.october)
        # Current month (November) for Schedule B.
        self.november = BudgetMonth.objects.create(
            user=self.user, year=2026, month=11, total_money=Decimal("500000")
        )
        create_default_period_categories(self.november)

    def _add_spending(self, category, day, amount):
        return DailyExpense.objects.create(
            budget_month=self.november,
            category=category,
            expense_date=date(2026, 11, day),
            amount=Decimal(amount),
            note="test",
        )

    # ------------------------------------------------------------------
    # Schedule A — previous-month report on the 1st
    # ------------------------------------------------------------------
    def test_schedule_a_sends_previous_month_on_first(self):
        results = send_previous_month_report(today=date(2026, 11, 1))

        self.assertIn("October 2026 — monthly previous", results["sent"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("October 2026", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["test@example.com"])
        self.assertTrue(
            ReportEmailLog.objects.filter(
                budget_month=self.october, report_type=MONTHLY_PREVIOUS
            ).exists()
        )

    def test_schedule_a_not_sent_on_other_days(self):
        # The dispatcher only runs Schedule A on the 1st.
        results = process_monthly_schedules(today=date(2026, 11, 5))
        self.assertEqual(results["sent"], [])
        self.assertEqual(mail.outbox, [])

    def test_schedule_a_no_duplicate_on_second_run(self):
        send_previous_month_report(today=date(2026, 11, 1))
        first_count = len(mail.outbox)

        results = send_previous_month_report(today=date(2026, 11, 1))
        self.assertEqual(results["sent"], [])
        self.assertEqual(len(mail.outbox), first_count)

    def test_schedule_a_force_resends(self):
        send_previous_month_report(today=date(2026, 11, 1))
        first_count = len(mail.outbox)

        results = send_previous_month_report(today=date(2026, 11, 1), force=True)
        self.assertIn("October 2026 — monthly previous", results["sent"])
        self.assertEqual(len(mail.outbox), first_count + 1)


    # ------------------------------------------------------------------
    # Schedule B — no-estimate overview on the 16th (days 1-15)
    # ------------------------------------------------------------------
    def test_schedule_b_includes_only_no_estimate_categories_in_window(self):
        no_est = self.november.categories.get(period_index=1)   # estimated_amount == 0
        with_est = self.november.categories.get(period_index=2)

        # A category WITH an estimated budget must be excluded from this report.
        with_est.estimated_amount = Decimal("100000")
        with_est.save(update_fields=["estimated_amount"])

        self._add_spending(no_est, 5, "30000")     # in window, no estimate -> included
        self._add_spending(with_est, 5, "40000")   # in window, has estimate -> excluded

        results = send_no_estimate_overview(today=date(2026, 11, 16))

        self.assertEqual(len(mail.outbox), 1)
        # HTML is attached as an alternative; check the message payload.
        alt_html = mail.outbox[0].alternatives[0][0]
        self.assertIn(no_est.name, alt_html)
        self.assertNotIn(with_est.name, alt_html)
        self.assertIn("November 2026", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["test@example.com"])
        self.assertIn("November 2026 — no-estimate overview", results["sent"])
        self.assertTrue(
            ReportEmailLog.objects.filter(
                budget_month=self.november, report_type=NO_ESTIMATE_FIRST_HALF
            ).exists()
        )

    def test_schedule_b_separates_direct_and_sub_balance_totals(self):
        direct_category = self.november.categories.get(period_index=1)
        sub_balance = SubBalance.objects.create(
            budget_month=self.november,
            name="November Travel",
            balance_amount=Decimal("50000"),
        )
        sub_category = BudgetCategory.objects.create(
            budget_month=self.november,
            sub_balance=sub_balance,
            name="Sub Transport",
            estimated_amount=Decimal("0"),
        )
        self._add_spending(direct_category, 5, "30000")
        DailyExpense.objects.create(
            budget_month=self.november,
            sub_balance=sub_balance,
            category=sub_category,
            expense_date=date(2026, 11, 6),
            amount=Decimal("15000"),
        )

        send_no_estimate_overview(today=date(2026, 11, 16))

        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("Main Balance — Direct Actual Spending", html)
        self.assertIn("Sub-Balance Actual Spending", html)
        self.assertIn("Combined Actual Spending", html)
        self.assertIn("November Travel", html)
        self.assertIn("30,000 MMK", html)
        self.assertIn("15,000 MMK", html)
        self.assertIn("45,000 MMK", html)

    def test_schedule_b_excludes_spending_after_day_15(self):
        no_est = self.november.categories.get(period_index=1)
        # Spending on day 20 is outside the days 1-15 window -> not included.
        self._add_spending(no_est, 20, "30000")

        results = send_no_estimate_overview(today=date(2026, 11, 16))
        self.assertEqual(results["sent"], [])
        self.assertEqual(mail.outbox, [])

    def test_schedule_b_no_email_when_no_no_estimate_spending(self):
        # Only a category WITH an estimate has spending -> nothing to report.
        with_est = self.november.categories.get(period_index=1)
        with_est.estimated_amount = Decimal("50000")
        with_est.save(update_fields=["estimated_amount"])
        self._add_spending(with_est, 5, "30000")

        results = send_no_estimate_overview(today=date(2026, 11, 16))
        self.assertEqual(results["sent"], [])
        self.assertEqual(mail.outbox, [])

    def test_schedule_b_no_duplicate_on_second_run(self):
        no_est = self.november.categories.get(period_index=1)
        self._add_spending(no_est, 5, "30000")

        send_no_estimate_overview(today=date(2026, 11, 16))
        first_count = len(mail.outbox)

        results = send_no_estimate_overview(today=date(2026, 11, 16))
        self.assertEqual(results["sent"], [])
        self.assertEqual(len(mail.outbox), first_count)

    # ------------------------------------------------------------------
    # Dispatcher independence
    # ------------------------------------------------------------------
    def test_dispatcher_runs_only_schedule_a_on_first(self):
        # Add November no-estimate spending so B *could* fire, but day is the 1st.
        no_est = self.november.categories.get(period_index=1)
        self._add_spending(no_est, 5, "30000")

        results = process_monthly_schedules(today=date(2026, 11, 1))

        self.assertIn("October 2026 — monthly previous", results["sent"])
        # Schedule B must NOT have run on the 1st.
        self.assertFalse(
            ReportEmailLog.objects.filter(report_type=NO_ESTIMATE_FIRST_HALF).exists()
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_dispatcher_runs_only_schedule_b_on_sixteenth(self):
        no_est = self.november.categories.get(period_index=1)
        self._add_spending(no_est, 5, "30000")

        results = process_monthly_schedules(today=date(2026, 11, 16))

        self.assertIn("November 2026 — no-estimate overview", results["sent"])
        # Schedule A must NOT have run on the 16th (no previous-month report).
        self.assertFalse(
            ReportEmailLog.objects.filter(report_type=MONTHLY_PREVIOUS).exists()
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_dispatcher_runs_both_with_force(self):
        no_est = self.november.categories.get(period_index=1)
        self._add_spending(no_est, 5, "30000")

        results = process_monthly_schedules(today=date(2026, 11, 10), force=True)

        self.assertIn("October 2026 — monthly previous", results["sent"])
        self.assertIn("November 2026 — no-estimate overview", results["sent"])
        self.assertEqual(len(mail.outbox), 2)

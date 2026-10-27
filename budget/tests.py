from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from budget.currency import format_mmk
from budget.models import (
    Balance,
    BudgetCategory,
    BudgetMonth,
    DailyExpense,
    PocketUser,
    ReportEmailLog,
    SubBalance,
)
from budget.services.automated_email_service import process_automated_emails
from budget.services.category_service import create_default_period_categories
from budget.services.report_service import generate_monthly_report
from budget.services.sub_balance_schedule_service import process_sub_balance_schedules


class DashboardBudgetAndExtraMoneyTests(TestCase):
    def setUp(self):
        self.auth_user = User.objects.create_user(
            username="testuser", email="test@example.com", password="Passw0rd!x"
        )
        self.user = PocketUser.objects.create(
            name="Test User",
            email="test@example.com",
            auth_user=self.auth_user,
        )
        self.balance = Balance.objects.create(user=self.user, name="Main Wallet")
        self.budget_month = BudgetMonth.objects.create(
            user=self.user,
            balance=self.balance,
            year=2026,
            month=8,
            total_money=Decimal("500000"),
        )
        create_default_period_categories(self.budget_month)
        self.client.force_login(self.auth_user)

    def test_update_category_budget_from_dashboard(self):
        category = self.budget_month.categories.get(period_index=1)

        response = self.client.post(
            reverse("budget:dashboard"),
            {
                "action": "update_category_budget",
                "category_id": category.id,
                "estimated_amount": "250000",
            },
        )

        self.assertEqual(response.status_code, 302)
        category.refresh_from_db()
        self.assertEqual(category.estimated_amount, Decimal("250000"))
        self.budget_month.refresh_from_db()
        self.assertGreater(self.budget_month.total_money, 0)

    def test_custom_categories_do_not_become_sub_balances(self):
        for index in range(7):
            response = self.client.post(
                reverse("budget:dashboard"),
                {
                    "action": "save_custom_category_budget",
                    "budget_month": self.budget_month.id,
                    "new_category_name": f"Subcategory {index}",
                    "estimated_amount": str(index * 100),
                },
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.budget_month.categories.filter(name__startswith="Subcategory ").count(),
            7,
        )
        self.assertFalse(
            SubBalance.objects.filter(budget_month=self.budget_month).exists()
        )

    def test_creating_duplicate_user_month_opens_existing_record(self):
        response = self.client.post(
            reverse("budget:dashboard"),
            {
                "action": "create_budget",
                "balance": self.balance.id,
                "name": "August 2026 Main Balance",
                "year": 2026,
                "month": 8,
                "total_money": 650000,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("budget:main_balance_detail", args=[self.budget_month.id]))
        self.assertEqual(
            BudgetMonth.objects.filter(user=self.user, year=2026, month=8).count(), 1
        )
        self.budget_month.refresh_from_db()
        self.assertEqual(self.budget_month.total_money, Decimal("500000"))

    def test_create_main_balance_for_same_month_in_another_year(self):
        response = self.client.post(
            reverse("budget:dashboard"),
            {
                "action": "create_budget",
                "name": "August 2027",
                "balance": self.balance.id,
                "year": 2027,
                "month": 8,
                "total_money": 700000,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BudgetMonth.objects.filter(user=self.user, month=8).count(), 2)
        self.assertTrue(BudgetMonth.objects.filter(user=self.user, year=2027, month=8).exists())

    def test_main_balance_detail_is_owner_scoped(self):
        other_auth = User.objects.create_user(
            username="other", email="other@example.com", password="Passw0rd!x"
        )
        PocketUser.objects.create(name="Other", email="other@example.com", auth_user=other_auth)
        self.client.force_login(other_auth)
        response = self.client.get(
            reverse("budget:main_balance_detail", args=[self.budget_month.id])
        )
        self.assertEqual(response.status_code, 302)

    def test_main_balance_delete_cascades_to_children(self):
        SubBalance.objects.create(
            budget_month=self.budget_month, name="Savings", balance_amount=1000
        )
        response = self.client.post(
            reverse("budget:delete_main_balance", args=[self.budget_month.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(BudgetMonth.objects.filter(pk=self.budget_month.id).exists())
        self.assertFalse(SubBalance.objects.filter(budget_month=self.budget_month).exists())

    def test_main_balance_edit_keeps_reporting_period_fixed(self):
        edit_page = self.client.get(
            reverse("budget:edit_main_balance", args=[self.budget_month.id])
        )
        self.assertEqual(edit_page.status_code, 200)
        response = self.client.post(
            reverse("budget:edit_main_balance", args=[self.budget_month.id]),
            {"name": "Updated August", "total_money": "600000", "year": 2027, "month": 2},
        )
        self.assertEqual(response.status_code, 302)
        self.budget_month.refresh_from_db()
        self.assertEqual((self.budget_month.year, self.budget_month.month), (2026, 8))
        self.assertEqual(self.budget_month.name, "Updated August")

    def test_sub_balance_inherits_parent_and_edit_preserves_creation_date(self):
        create_page = self.client.get(
            reverse("budget:create_sub_balance", args=[self.budget_month.id])
        )
        self.assertEqual(create_page.status_code, 200)
        response = self.client.post(
            reverse("budget:create_sub_balance", args=[self.budget_month.id]),
            {
                "name": "Emergency",
                "balance_amount": "50000",
                "balance_date": "2026-08-14",
                "estimated_amount": "20000",
            },
        )
        sub = SubBalance.objects.get(budget_month=self.budget_month, name="Emergency")
        detail = self.client.get(reverse("budget:sub_balance_detail", args=[sub.id]))
        self.assertEqual(detail.status_code, 200)
        edit_page = self.client.get(reverse("budget:edit_sub_balance", args=[sub.id]))
        self.assertEqual(edit_page.status_code, 200)
        original_created = sub.created_at
        response = self.client.post(
            reverse("budget:edit_sub_balance", args=[sub.id]),
            {
                "name": "Emergency Fund",
                "balance_amount": "45000",
                "balance_date": "2026-08-16",
                "estimated_amount": "25000",
                "budget_month": "99999",
            },
        )
        self.assertEqual(response.status_code, 302)
        sub.refresh_from_db()
        self.assertEqual(sub.budget_month_id, self.budget_month.id)
        self.assertEqual(sub.created_at, original_created)
        self.assertEqual(sub.name, "Emergency Fund")
        self.assertEqual(sub.balance_date, date(2026, 8, 16))

    def test_setup_sub_balance_form_persists_date_and_inherits_month_year(self):
        response = self.client.get(reverse("budget:dashboard"), {"tab": "setup"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="date"')
        self.assertContains(response, "Month (inherited)")
        self.assertContains(response, "Year (inherited)")

        response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=setup",
            {
                "action": "create_sub_balance",
                "budget_month": self.budget_month.id,
                "name": "Date Checked",
                "balance_amount": "25000",
                "balance_date": "2026-08-31",
            },
        )
        self.assertEqual(response.status_code, 302)
        sub_balance = SubBalance.objects.get(name="Date Checked")
        self.assertEqual(sub_balance.balance_date, date(2026, 8, 31))
        self.assertEqual(
            (sub_balance.budget_month.year, sub_balance.budget_month.month),
            (sub_balance.balance_date.year, sub_balance.balance_date.month),
        )
        refreshed = self.client.get(reverse("budget:dashboard"), {"tab": "setup"})
        self.assertContains(refreshed, "Date Checked")

    def test_sub_balance_creation_rejects_invalid_and_parent_period_dates(self):
        for invalid_date in ("2026-02-30", "2026-09-01"):
            with self.subTest(balance_date=invalid_date):
                response = self.client.post(
                    f"{reverse('budget:dashboard')}?tab=setup",
                    {
                        "action": "create_sub_balance",
                        "budget_month": self.budget_month.id,
                        "name": f"Invalid {invalid_date}",
                        "balance_amount": "1000",
                        "balance_date": invalid_date,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    response.context["sub_balance_form"].has_error("balance_date")
                )
                self.assertFalse(
                    SubBalance.objects.filter(name=f"Invalid {invalid_date}").exists()
                )

    def test_sub_balance_date_accepts_leap_day_in_parent_february(self):
        february = BudgetMonth.objects.create(
            user=self.user,
            balance=self.balance,
            year=2024,
            month=2,
            total_money=Decimal("100000"),
        )

        response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=setup",
            {
                "action": "create_sub_balance",
                "budget_month": february.id,
                "name": "Leap Day Balance",
                "balance_amount": "1000",
                "balance_date": "2024-02-29",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            SubBalance.objects.get(name="Leap Day Balance").balance_date,
            date(2024, 2, 29),
        )


    def test_sub_balance_creation_is_separate_from_expense_categories(self):
        category_count = BudgetCategory.objects.filter(
            budget_month=self.budget_month
        ).count()
        for name in ("Home", "Savings", "Emergency"):
            response = self.client.post(
                reverse("budget:create_sub_balance", args=[self.budget_month.id]),
                {
                    "name": name,
                    "balance_amount": "50000",
                    "balance_date": "2026-08-01",
                    "estimated_amount": "",
                },
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(
            BudgetCategory.objects.filter(budget_month=self.budget_month).count(),
            category_count,
        )
        self.assertEqual(
            SubBalance.objects.filter(budget_month=self.budget_month).count(), 3
        )
        self.assertIsNone(
            SubBalance.objects.get(budget_month=self.budget_month, name="Home").estimated_amount
        )

    def test_detail_lists_only_sub_balances_for_selected_parent(self):
        other_month = BudgetMonth.objects.create(
            user=self.user, balance=self.balance, year=2026, month=9,
            total_money=Decimal("500000"),
        )
        own = SubBalance.objects.create(
            budget_month=self.budget_month, name="August only", balance_amount=10
        )
        SubBalance.objects.create(
            budget_month=other_month, name="September only", balance_amount=10
        )
        response = self.client.get(
            reverse("budget:main_balance_detail", args=[self.budget_month.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["sub_balance"].id for row in response.context["sub_balance_rows"]], [own.id])
        self.assertNotContains(response, "September only")

    def test_monthly_report_includes_named_sub_balances_without_categories(self):
        sub = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Emergency fund",
            balance_amount=Decimal("120000"),
            estimated_amount=None,
        )
        report = generate_monthly_report(self.budget_month.id)
        self.assertEqual(len(report.sub_balance_rows), 1)
        self.assertEqual(report.sub_balance_rows[0]["name"], sub.name)
        self.assertIsNone(report.sub_balance_rows[0]["estimated_amount"])
        self.assertEqual(report.total_budget_estimated, Decimal("0"))
        self.assertEqual(report.total_actual_spent, Decimal("0"))

    def test_sub_balance_spending_and_main_monthly_totals_are_independent(self):
        sub = SubBalance.objects.create(
            budget_month=self.budget_month, name="Week 1", estimated_amount=Decimal("10000")
        )
        category = BudgetCategory.objects.create(
            budget_month=self.budget_month, sub_balance=sub, name="Meals",
            estimated_amount=Decimal("8000"), period_index=None,
        )
        main_category = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month, category=main_category,
            expense_date="2026-08-05", amount=Decimal("3000"),
        )
        DailyExpense.objects.create(
            budget_month=self.budget_month, sub_balance=sub, category=category,
            expense_date="2026-08-06", amount=Decimal("2500"),
        )
        report = generate_monthly_report(self.budget_month.id)
        self.assertEqual(sub.total_actual_spent, Decimal("2500"))
        self.assertEqual(sub.remaining_balance, Decimal("7500"))
        self.assertEqual(report.remaining_balance, Decimal("497000"))
        self.assertEqual(report.total_actual_spent, Decimal("5500"))
        self.assertEqual(report.direct_actual_spent, Decimal("3000"))
        self.assertEqual(report.sub_balance_total_actual_spent, Decimal("2500"))
        self.assertEqual(report.sub_balance_rows[0]["categories"][0]["actual_amount"], Decimal("2500"))

    def test_summary_and_reports_separate_direct_and_sub_balance_spending(self):
        direct_category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            name="Main Food",
            estimated_amount=Decimal("10000"),
        )
        week_one = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Week 1",
            balance_amount=Decimal("10000"),
        )
        week_two = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Week 2",
            balance_amount=Decimal("10000"),
        )
        week_one_category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=week_one,
            name="Food",
            estimated_amount=Decimal("5000"),
        )
        week_two_category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=week_two,
            name="Transport",
            estimated_amount=Decimal("5000"),
        )
        direct_expense = DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=direct_category,
            expense_date=date(2026, 8, 5),
            amount=Decimal("5000"),
            note="Direct groceries",
        )
        week_one_expense = DailyExpense.objects.create(
            budget_month=self.budget_month,
            sub_balance=week_one,
            category=week_one_category,
            expense_date=date(2026, 8, 6),
            amount=Decimal("2000"),
            note="Week one groceries",
        )
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            sub_balance=week_two,
            category=week_two_category,
            expense_date=date(2026, 8, 15),
            amount=Decimal("1000"),
            note="Week two bus",
        )

        report = generate_monthly_report(self.budget_month.id)
        self.assertEqual(report.direct_actual_spent, Decimal("5000"))
        self.assertEqual(report.sub_balance_total_actual_spent, Decimal("3000"))
        self.assertEqual(report.total_actual_spent, Decimal("8000"))
        self.assertEqual(report.main_transaction_count, 1)
        self.assertEqual(
            [row["total_actual_spent"] for row in report.sub_balance_rows],
            [Decimal("2000"), Decimal("1000")],
        )
        self.assertEqual(report.remaining_balance, Decimal("495000"))

        summary = self.client.get(
            reverse("budget:dashboard"),
            {"tab": "summary", "summary_month": self.budget_month.id},
        )
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(
            summary.context["summary_report"].direct_actual_spent,
            Decimal("5000"),
        )
        self.assertContains(summary, "Main Balance — Direct Actual Spending")
        self.assertContains(summary, "Combined Actual Spending")
        self.assertContains(summary, "5,000 MMK")
        self.assertContains(summary, "2,000 MMK")
        self.assertContains(summary, "1,000 MMK")
        self.assertContains(summary, "8,000 MMK")

        main_detail = self.client.get(
            reverse("budget:main_balance_detail", args=[self.budget_month.id])
        )
        self.assertContains(main_detail, "Direct groceries")
        self.assertContains(main_detail, "Main Balance — Direct Actual Spending")
        self.assertContains(main_detail, "Combined Actual Spending")
        self.assertEqual(
            main_detail.context["report"].main_transactions[0]["amount"],
            direct_expense.amount,
        )

        sub_detail = self.client.get(
            reverse("budget:sub_balance_detail", args=[week_one.id])
        )
        self.assertContains(sub_detail, "Week one groceries")
        self.assertContains(sub_detail, "2,000 MMK")
        self.assertNotContains(sub_detail, "Week two bus")
        self.assertNotContains(sub_detail, "1,000 MMK")
        self.assertEqual(
            list(sub_detail.context["transactions"]),
            [week_one_expense],
        )

        reports_page = self.client.get(
            reverse("budget:reports"),
            {"budget_month": self.budget_month.id},
        )
        self.assertContains(reports_page, "Main Balance — Direct Actual Spending")
        self.assertContains(reports_page, "Combined Actual Spending")
        self.assertContains(reports_page, "8,000 MMK")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="configured@example.com",
        EMAIL_HOST_PASSWORD="configured",
    )
    def test_monthly_email_uses_separated_latest_spending_totals(self):
        from budget.services.email_service import send_monthly_report_email

        direct_category = self.budget_month.categories.get(period_index=1)
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Email Week",
            balance_amount=Decimal("5000"),
        )
        sub_category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            name="Sub Food",
            estimated_amount=Decimal("3000"),
        )
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=direct_category,
            expense_date=date(2026, 8, 5),
            amount=Decimal("5000"),
            note="Direct email transaction",
        )
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            category=sub_category,
            expense_date=date(2026, 8, 6),
            amount=Decimal("2000"),
            note="Sub email transaction",
        )

        report = generate_monthly_report(self.budget_month.id)
        self.assertTrue(
            send_monthly_report_email(self.user.email, self.user.name, report)
        )
        html_content = mail.outbox[0].alternatives[0][0]
        self.assertIn("Main Balance — Direct Actual Spending", html_content)
        self.assertIn("Sub-Balance Actual Spending", html_content)
        self.assertIn("Combined Actual Spending", html_content)
        self.assertIn("5,000 MMK", html_content)
        self.assertIn("2,000 MMK", html_content)
        self.assertIn("7,000 MMK", html_content)
        self.assertIn("Direct email transaction", html_content)
        self.assertIn("Sub email transaction", html_content)
        self.assertIn("Combined Actual Spending", mail.outbox[0].body)

    def test_sub_balance_transaction_requires_owned_category_and_parent_month(self):
        from budget.forms import SubBalanceCategoryForm, SubBalanceTransactionForm

        sub = SubBalance.objects.create(budget_month=self.budget_month, name="Week 1")
        category = SubBalanceCategoryForm(
            {"name": "Food", "estimated_amount": "0"}, sub_balance=sub
        )
        self.assertTrue(category.is_valid(), category.errors)
        category = category.save()
        form = SubBalanceTransactionForm(
            {"category": category.id, "expense_date": "2026-09-01", "amount": "100", "note": "wrong month"},
            sub_balance=sub,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("expense_date", form.errors)

    def test_sub_balance_delete_without_history_is_safe(self):
        sub = SubBalance.objects.create(
            budget_month=self.budget_month, name="Unused", balance_amount=10
        )
        response = self.client.post(
            reverse("budget:delete_sub_balance", args=[sub.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SubBalance.objects.filter(pk=sub.id).exists())

    def test_main_balance_delete_without_dependencies_is_safe(self):
        empty_month = BudgetMonth.objects.create(
            user=self.user, balance=self.balance, year=2026, month=9,
            total_money=Decimal("500000"),
        )
        response = self.client.post(
            reverse("budget:delete_main_balance", args=[empty_month.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(BudgetMonth.objects.filter(pk=empty_month.id).exists())

    def test_sub_balance_with_financial_history_cannot_be_deleted(self):
        category = self.budget_month.categories.create(
            name="Protected expenses", estimated_amount=Decimal("1000"), period_index=None
        )
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month, name="Protected", balance_amount=1000,
            estimated_amount=Decimal("1000"),
        )
        ReportEmailLog.objects.create(
            budget_month=self.budget_month, report_type=f"sb15_{sub_balance.id}"
        )
        self.client.post(reverse("budget:delete_sub_balance", args=[sub_balance.id]))
        self.assertTrue(SubBalance.objects.filter(pk=sub_balance.id).exists())
        self.assertTrue(BudgetCategory.objects.filter(pk=category.id).exists())

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="configured@example.com",
        EMAIL_HOST_PASSWORD="configured",
    )
    def test_sub_balance_finish_can_be_sent_repeatedly(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month, name="Savings", balance_amount=5000,
            estimated_amount=Decimal("5000"),
        )
        url = reverse("budget:finish_sub_balance", args=[sub_balance.id])
        self.client.post(url)
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("Savings", mail.outbox[0].subject)
        self.assertTrue(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month, report_type=f"sbfn_{sub_balance.id}"
            ).exists()
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="configured@example.com",
        EMAIL_HOST_PASSWORD="configured",
    )
    def test_sub_balance_manual_report_uses_latest_data_and_omits_legacy_estimate(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Savings",
            balance_amount=5000,
            estimated_amount=98765,
        )
        category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            name="Food",
            estimated_amount=1000,
            period_index=None,
        )
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            category=category,
            expense_date=date(2026, 8, 5),
            amount=Decimal("100"),
        )
        url = reverse("budget:finish_sub_balance", args=[sub_balance.id])
        self.client.post(url)

        DailyExpense.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            category=category,
            expense_date=date(2026, 8, 6),
            amount=Decimal("250"),
        )
        self.client.post(url)

        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("100 MMK", mail.outbox[0].alternatives[0][0])
        self.assertIn("350 MMK", mail.outbox[1].alternatives[0][0])
        self.assertNotIn("98,765 MMK", mail.outbox[0].alternatives[0][0])
        self.assertNotIn("estimated_amount", mail.outbox[0].alternatives[0][0])
        detail = self.client.get(
            reverse("budget:sub_balance_detail", args=[sub_balance.id])
        )
        self.assertContains(detail, "you can send it again")
        self.assertContains(detail, "Finish and Email This Sub Balance")
        self.assertNotContains(detail, 'disabled="disabled"')

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="configured@example.com",
        EMAIL_HOST_PASSWORD="configured",
    )
    def test_sub_balance_manual_email_denies_another_users_balance(self):
        other_auth = User.objects.create_user(
            username="private-owner", email="private@example.com", password="secret"
        )
        other_profile = PocketUser.objects.create(
            name="Private Owner", email="private@example.com", auth_user=other_auth
        )
        other_month = BudgetMonth.objects.create(
            user=other_profile,
            year=2026,
            month=9,
            total_money=Decimal("100000"),
        )
        other_sub_balance = SubBalance.objects.create(
            budget_month=other_month, name="Private balance", balance_amount=1000
        )

        response = self.client.post(
            reverse("budget:finish_sub_balance", args=[other_sub_balance.id]),
            follow=True,
        )

        self.assertEqual(mail.outbox, [])
        self.assertNotContains(response, "Sub Balance report sent")

    @override_settings(EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="")
    def test_sub_balance_manual_report_failure_can_be_retried(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month, name="Retry", balance_amount=100
        )
        url = reverse("budget:finish_sub_balance", args=[sub_balance.id])
        failed = self.client.post(url, follow=True)
        self.assertContains(failed, "could not be sent")
        self.assertFalse(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month,
                report_type=f"sbfn_{sub_balance.id}",
            ).exists()
        )

        with self.settings(
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            EMAIL_HOST_USER="configured@example.com",
            EMAIL_HOST_PASSWORD="configured",
        ):
            retried = self.client.post(url, follow=True)
        self.assertContains(retried, "Sub Balance report sent")
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="configured@example.com",
        EMAIL_HOST_PASSWORD="configured",
    )
    def test_each_sub_balance_15_day_schedule_is_independent_and_idempotent(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month, name="Medical", balance_amount=30000,
            estimated_amount=Decimal("30000"),
        )
        due_date = timezone.localdate() - timedelta(days=15)
        SubBalance.objects.filter(pk=sub_balance.id).update(
            created_at=timezone.make_aware(
                datetime.combine(due_date, time.min)
            )
        )
        result = process_sub_balance_schedules(today=timezone.localdate())
        self.assertEqual(result["sent"], [sub_balance.id])
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month, report_type=f"sb15_{sub_balance.id}"
            ).exists()
        )
        process_sub_balance_schedules(today=timezone.localdate())
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="")
    def test_15_day_report_failure_remains_retryable(self):
        sub = SubBalance.objects.create(
            budget_month=self.budget_month, name="Retry me", balance_amount=100
        )
        due_date = timezone.localdate() - timedelta(days=15)
        SubBalance.objects.filter(pk=sub.id).update(
            created_at=timezone.make_aware(datetime.combine(due_date, time.min))
        )
        first = process_sub_balance_schedules(today=timezone.localdate())
        self.assertEqual(first["skipped"], [sub.id])
        self.assertFalse(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month, report_type=f"sb15_{sub.id}"
            ).exists()
        )
        with self.settings(
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            EMAIL_HOST_USER="configured@example.com",
            EMAIL_HOST_PASSWORD="configured",
        ):
            second = process_sub_balance_schedules(today=timezone.localdate())
        self.assertEqual(second["sent"], [sub.id])

    @override_settings(EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="")
    def test_finish_failure_does_not_report_success_or_leave_log(self):
        sub = SubBalance.objects.create(
            budget_month=self.budget_month, name="No mail", balance_amount=100
        )
        self.client.post(reverse("budget:finish_sub_balance", args=[sub.id]))
        self.assertFalse(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month, report_type=f"sbfn_{sub.id}"
            ).exists()
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="configured@example.com",
        EMAIL_HOST_PASSWORD="configured",
    )
    def test_finish_month_report_can_be_sent_repeatedly(self):
        data = {
            "action": "finish_month",
            "budget_month_id": self.budget_month.id,
        }
        response = self.client.post(reverse("budget:dashboard"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertTrue(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month,
                report_type=ReportEmailLog.FINISH,
            ).exists()
        )

        self.client.post(reverse("budget:dashboard"), data)
        self.client.post(reverse("budget:dashboard"), data)
        self.assertEqual(len(mail.outbox), 3)

    @override_settings(EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="")
    def test_finish_email_failure_does_not_leave_success_log(self):
        response = self.client.post(
            reverse("budget:dashboard"),
            {
                "action": "finish_month",
                "budget_month_id": self.budget_month.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month,
                report_type=ReportEmailLog.FINISH,
            ).exists()
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="configured@example.com",
        EMAIL_HOST_PASSWORD="configured",
    )
    def test_failed_monthly_email_can_be_retried(self):
        data = {
            "action": "finish_month",
            "budget_month_id": self.budget_month.id,
        }
        with self.settings(EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD=""):
            failed = self.client.post(reverse("budget:dashboard"), data, follow=True)
        self.assertContains(failed, "could not be sent")
        self.assertFalse(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month,
                report_type=ReportEmailLog.FINISH,
            ).exists()
        )

        sent = self.client.post(reverse("budget:dashboard"), data, follow=True)
        self.assertContains(sent, "Monthly report sent")
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="configured@example.com",
        EMAIL_HOST_PASSWORD="configured",
    )
    def test_reports_page_allows_repeated_latest_monthly_reports(self):
        data = {"action": "email", "budget_month": self.budget_month.id}
        self.client.post(reverse("budget:reports"), data)

        category = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category,
            expense_date=date(2026, 8, 5),
            amount=Decimal("42000"),
            note="Updated spending",
        )
        self.client.post(reverse("budget:reports"), data)
        retry = self.client.post(reverse("budget:reports"), data, follow=True)

        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("0 MMK", mail.outbox[0].body)
        self.assertIn("42,000 MMK", mail.outbox[1].body)
        self.assertIn("42,000 MMK", mail.outbox[2].body)
        self.assertNotIn("attached as HTML", mail.outbox[2].body)
        self.assertEqual(mail.outbox[2].alternatives[0][1], "text/html")
        self.assertContains(retry, "Send Total Report", html=False)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="configured@example.com",
        EMAIL_HOST_PASSWORD="configured",
    )
    def test_reports_page_rejects_another_users_month(self):
        other_auth = User.objects.create_user(
            username="report-owner", email="report-owner@example.com", password="secret"
        )
        other_profile = PocketUser.objects.create(
            name="Report Owner",
            email="report-owner@example.com",
            auth_user=other_auth,
        )
        other_month = BudgetMonth.objects.create(
            user=other_profile,
            year=2026,
            month=8,
            total_money=Decimal("100000"),
        )
        self.client.force_login(other_auth)

        response = self.client.post(
            reverse("budget:reports"),
            {"action": "email", "budget_month": self.budget_month.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])
        self.assertNotContains(response, "Monthly report sent")
        self.assertNotEqual(other_month.user_id, self.budget_month.user_id)

    def test_add_extra_money_from_dashboard(self):
        category = self.budget_month.categories.get(period_index=1)

        response = self.client.post(
            reverse("budget:dashboard"),
            {
                "action": "add_extra_money",
                "budget_month": self.budget_month.id,
                "category": category.id,
                "expense_date": "2026-08-05",
                "amount": "150000",
                "note": "Unexpected bonus",
            },
        )

        self.assertEqual(response.status_code, 302)
        expense = DailyExpense.objects.get(budget_month=self.budget_month)
        self.assertEqual(expense.amount, Decimal("-150000"))
        self.assertEqual(expense.category_id, category.id)

    def test_update_expense_amount_from_dashboard(self):
        category = self.budget_month.categories.get(period_index=1)
        expense = DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category,
            expense_date="2026-08-05",
            amount=Decimal("40000"),
            note="Groceries",
        )

        response = self.client.post(
            reverse("budget:dashboard"),
            {
                "action": "update_expense",
                "expense_id": expense.id,
                "amount": "50000",
                "note": "Groceries updated",
            },
        )

        self.assertEqual(response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal("50000"))
        self.assertEqual(expense.note, "Groceries updated")

    def test_edit_expense_form_is_rendered_on_dashboard(self):
        category = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category,
            expense_date="2026-08-05",
            amount=Decimal("50000"),
            note="Groceries",
        )

        response = self.client.get(reverse("budget:dashboard"), {"tab": "expenses"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="action" value="update_expense"')
        self.assertContains(response, 'id="edit-expense-amount"')
        self.assertContains(response, 'id="edit-expense-id"')

    def test_delete_expense_from_dashboard(self):
        category = self.budget_month.categories.get(period_index=1)
        expense = DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category,
            expense_date="2026-08-05",
            amount=Decimal("40000"),
            note="Delete me",
        )

        response = self.client.post(
            reverse("budget:dashboard"),
            {"action": "delete_expense", "expense_id": expense.id},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(DailyExpense.objects.filter(pk=expense.id).exists())

    def test_edit_budget_month_from_dashboard(self):
        response = self.client.post(
            reverse("budget:dashboard"),
            {
                "action": "edit_budget_month",
                "budget_month_id": self.budget_month.id,
                "year": "2027",
                "month": "9",
                "name": "Revised Main Balance",
                "total_money": "600000",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.budget_month.refresh_from_db()
        self.assertEqual(self.budget_month.year, 2026)
        self.assertEqual(self.budget_month.month, 8)
        self.assertEqual(self.budget_month.name, "Revised Main Balance")
        self.assertEqual(self.budget_month.total_money, Decimal("600000"))

    def test_delete_budget_month_from_dashboard(self):
        second_month = BudgetMonth.objects.create(
            user=self.user,
            balance=self.balance,
            year=2026,
            month=7,
            total_money=Decimal("450000"),
        )

        response = self.client.post(
            reverse("budget:dashboard"),
            {"action": "delete_budget_month", "budget_month_id": second_month.id},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(BudgetMonth.objects.filter(pk=second_month.id).exists())

    def test_create_sub_balance_from_dashboard(self):
        response = self.client.post(
            reverse("budget:dashboard"),
            {"action": "create_balance", "name": "Savings"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Balance.objects.filter(user=self.user, name="Savings").exists()
        )

    def test_setup_shows_sub_balance_creation_form_without_estimated_money(self):
        response = self.client.get(
            reverse("budget:dashboard"),
            {"tab": "setup"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create Sub Balance")
        self.assertContains(response, 'id="sub-balance-budget-month"')
        self.assertNotContains(response, "Estimated Money (MMK)")

    def test_dashboard_creates_sub_balance_under_selected_owned_main_balance(self):
        response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=setup",
            {
                "action": "create_sub_balance",
                "budget_month": self.budget_month.id,
                "name": "Travel Fund",
                "balance_amount": "80000",
                "balance_date": "2026-08-11",
                "estimated_amount": "99999",
            },
        )

        self.assertEqual(response.status_code, 302)
        sub_balance = SubBalance.objects.get(name="Travel Fund")
        self.assertEqual(sub_balance.budget_month_id, self.budget_month.id)
        self.assertEqual(sub_balance.balance_amount, Decimal("80000"))
        self.assertEqual(sub_balance.balance_date, date(2026, 8, 11))
        self.assertIsNone(sub_balance.estimated_amount)

    def test_dashboard_sub_balance_form_rejects_another_users_main_balance(self):
        other_auth = User.objects.create_user(
            username="other-balance-owner",
            email="other-balance-owner@example.com",
            password="test-password-123",
        )
        other_user = PocketUser.objects.create(
            name="Other Balance Owner",
            email="other-balance-owner@example.com",
            auth_user=other_auth,
        )
        other_balance = Balance.objects.create(user=other_user, name="Other Wallet")
        other_month = BudgetMonth.objects.create(
            user=other_user,
            balance=other_balance,
            year=2026,
            month=9,
            total_money=Decimal("100000"),
        )

        response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=setup",
            {
                "action": "create_sub_balance",
                "budget_month": other_month.id,
                "name": "Unauthorized",
                "balance_amount": "1000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SubBalance.objects.filter(name="Unauthorized").exists())
        self.assertTrue(response.context["sub_balance_form"].has_error("budget_month"))

    def test_dashboard_records_actual_money_for_selected_sub_balance(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Food Wallet",
            balance_amount=Decimal("80000"),
        )
        category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            name="Groceries",
            estimated_amount=Decimal("0"),
        )

        response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=expenses&expense_type=sub",
            {
                "action": "add_expense",
                "expense_type": "sub",
                "budget_month": self.budget_month.id,
                "sub_balance": sub_balance.id,
                "category": category.id,
                "expense_date": "2026-08-12",
                "amount": "2500",
                "note": "Market",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("expense_type=sub", response.url)
        expense = DailyExpense.objects.get(note="Market")
        self.assertEqual(expense.budget_month_id, self.budget_month.id)
        self.assertEqual(expense.sub_balance_id, sub_balance.id)
        self.assertEqual(expense.category_id, category.id)
        self.assertEqual(sub_balance.total_actual_spent, Decimal("2500"))
        self.assertEqual(
            generate_monthly_report(self.budget_month.id).total_actual_spent,
            Decimal("2500"),
        )
        refreshed = self.client.get(
            reverse("budget:dashboard"),
            {
                "tab": "expenses",
                "expense_type": "sub",
                "expense_month": self.budget_month.id,
                "sub_balance": sub_balance.id,
            },
        )
        self.assertEqual(refreshed.status_code, 200)
        self.assertContains(refreshed, "Market")

    def test_dashboard_creates_typed_custom_sub_balance_category_for_spending(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Custom Category Wallet",
            balance_amount=Decimal("20000"),
        )
        category_response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=expenses&expense_type=sub&expense_month={self.budget_month.id}&sub_balance={sub_balance.id}",
            {
                "action": "create_expense_category",
                "category_type": "sub",
                "budget_month": self.budget_month.id,
                "sub_balance": sub_balance.id,
                "name": "  Monthly Internet Access  ",
            },
        )

        self.assertEqual(category_response.status_code, 302)
        category = BudgetCategory.objects.get(name="Monthly Internet Access")
        self.assertEqual(category.budget_month_id, self.budget_month.id)
        self.assertEqual(category.sub_balance_id, sub_balance.id)
        self.assertIsNone(category.period_index)

        expense_response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=expenses&expense_type=sub",
            {
                "action": "add_expense",
                "expense_type": "sub",
                "budget_month": self.budget_month.id,
                "sub_balance": sub_balance.id,
                "category": category.id,
                "expense_date": "2026-08-12",
                "amount": "1700",
                "note": "Internet bill",
            },
        )
        self.assertEqual(expense_response.status_code, 302)
        expense = DailyExpense.objects.get(note="Internet bill")
        self.assertEqual(expense.category_id, category.id)
        self.assertEqual(expense.sub_balance_id, sub_balance.id)
        self.assertEqual(expense.budget_month_id, self.budget_month.id)

    def test_dashboard_custom_category_input_rejects_duplicate_and_invalid_parent(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Category Owner",
            balance_amount=Decimal("5000"),
        )
        BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            name="Food",
            estimated_amount=Decimal("0"),
        )
        duplicate_response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=expenses&expense_type=sub",
            {
                "action": "create_expense_category",
                "category_type": "sub",
                "budget_month": self.budget_month.id,
                "sub_balance": sub_balance.id,
                "name": " food ",
            },
        )
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertTrue(
            duplicate_response.context["expense_category_form"].has_error("name")
        )
        self.assertEqual(
            BudgetCategory.objects.filter(budget_month=self.budget_month).count(),
            4,
        )

        invalid_parent_response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=expenses&expense_type=sub",
            {
                "action": "create_expense_category",
                "category_type": "sub",
                "budget_month": self.budget_month.id,
                "sub_balance": "not-a-number",
                "name": "Unauthorized Category",
            },
        )
        self.assertEqual(invalid_parent_response.status_code, 200)
        self.assertTrue(
            invalid_parent_response.context["expense_category_form"].has_error(
                "sub_balance"
            )
        )
        self.assertFalse(
            BudgetCategory.objects.filter(name="Unauthorized Category").exists()
        )

    def test_sub_balance_category_input_is_text_and_duplicate_validation_is_preserved(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Text Category Wallet",
            balance_amount=Decimal("20000"),
        )
        response = self.client.get(
            reverse("budget:sub_balance_detail", args=[sub_balance.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<input type="text" name="name"')
        self.assertContains(response, '<select name="category"')

        for name in ("Books", "books"):
            response = self.client.post(
                reverse("budget:sub_balance_detail", args=[sub_balance.id]),
                {"action": "add_category", "name": name},
            )
            if name == "Books":
                self.assertEqual(response.status_code, 302)
            else:
                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    response.context["category_form"].has_error("name")
                )
        self.assertEqual(
            BudgetCategory.objects.filter(budget_month=self.budget_month).count(),
            4,
        )

    def test_dashboard_actual_money_rejects_wrong_sub_balance_category(self):
        first_sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="First Wallet",
            balance_amount=Decimal("10000"),
        )
        second_sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Second Wallet",
            balance_amount=Decimal("10000"),
        )
        wrong_category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=second_sub_balance,
            name="Other Food",
            estimated_amount=Decimal("0"),
        )

        response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=expenses&expense_type=sub",
            {
                "action": "add_expense",
                "expense_type": "sub",
                "budget_month": self.budget_month.id,
                "sub_balance": first_sub_balance.id,
                "category": wrong_category.id,
                "expense_date": "2026-08-12",
                "amount": "1000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["expense_form"].has_error("category"))
        self.assertFalse(DailyExpense.objects.exists())

    def test_dashboard_actual_money_rejects_non_positive_amount(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Daily Wallet",
            balance_amount=Decimal("5000"),
        )
        category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            name="Meals",
            estimated_amount=Decimal("0"),
        )

        response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=expenses&expense_type=sub",
            {
                "action": "add_expense",
                "expense_type": "sub",
                "budget_month": self.budget_month.id,
                "sub_balance": sub_balance.id,
                "category": category.id,
                "expense_date": "2026-08-12",
                "amount": "0",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["expense_form"].has_error("amount"))
        self.assertFalse(DailyExpense.objects.exists())

    def test_dashboard_actual_money_rejects_another_users_sub_balance_and_category(self):
        other_auth = User.objects.create_user(
            username="other-expense-owner",
            email="other-expense-owner@example.com",
            password="test-password-123",
        )
        other_user = PocketUser.objects.create(
            name="Other Expense Owner",
            email="other-expense-owner@example.com",
            auth_user=other_auth,
        )
        other_balance = Balance.objects.create(user=other_user, name="Other Wallet")
        other_month = BudgetMonth.objects.create(
            user=other_user,
            balance=other_balance,
            year=2026,
            month=8,
            total_money=Decimal("100000"),
        )
        other_sub_balance = SubBalance.objects.create(
            budget_month=other_month,
            name="Private Sub-Balance",
            balance_amount=Decimal("10000"),
        )
        other_category = BudgetCategory.objects.create(
            budget_month=other_month,
            sub_balance=other_sub_balance,
            name="Private Category",
            estimated_amount=Decimal("0"),
        )

        response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=expenses&expense_type=sub",
            {
                "action": "add_expense",
                "expense_type": "sub",
                "budget_month": self.budget_month.id,
                "sub_balance": other_sub_balance.id,
                "category": other_category.id,
                "expense_date": "2026-08-12",
                "amount": "1000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(DailyExpense.objects.exists())

    def test_dashboard_sub_balance_actual_money_updates_and_deletes_with_totals(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Travel Wallet",
            balance_amount=Decimal("50000"),
        )
        category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            name="Transport",
            estimated_amount=Decimal("0"),
        )
        expense = DailyExpense.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            category=category,
            expense_date="2026-08-12",
            amount=Decimal("3000"),
        )

        response = self.client.post(
            f"{reverse('budget:dashboard')}?tab=expenses&expense_type=sub",
            {
                "action": "update_expense",
                "expense_type": "sub",
                "expense_id": expense.id,
                "budget_month": self.budget_month.id,
                "sub_balance": sub_balance.id,
                "category": category.id,
                "expense_date": "2026-08-12",
                "amount": "4500",
                "note": "Updated fare",
            },
        )

        self.assertEqual(response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal("4500"))
        self.assertEqual(sub_balance.total_actual_spent, Decimal("4500"))

        response = self.client.post(
            reverse("budget:dashboard"),
            {"action": "delete_expense", "expense_id": expense.id},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(DailyExpense.objects.filter(pk=expense.id).exists())
        self.assertEqual(sub_balance.total_actual_spent, Decimal("0"))

    def test_sub_balance_actual_money_view_is_available_and_owner_scoped(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Private Wallet",
            balance_amount=Decimal("10000"),
        )
        category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            name="Private Category",
            estimated_amount=Decimal("0"),
        )
        response = self.client.get(
            reverse("budget:dashboard"),
            {"tab": "expenses", "expense_type": "sub"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sub-Balance")
        self.assertContains(response, 'id="expense-sub-balance"')

        api_response = self.client.get(
            reverse("budget:categories_for_month", args=[self.budget_month.id])
        )
        self.assertEqual(api_response.status_code, 200)
        self.assertIn(
            {"id": category.id, "name": category.name, "period_index": None, "sub_balance_id": sub_balance.id},
            api_response.json()["categories"],
        )
        self.assertIn(
            {"id": sub_balance.id, "name": sub_balance.name},
            api_response.json()["sub_balances"],
        )

    def test_sub_balance_actual_money_can_filter_to_one_owned_sub_balance(self):
        sub_balances = [
            SubBalance.objects.create(
                budget_month=self.budget_month,
                name=f"Wallet {index}",
                balance_amount=Decimal("10000"),
            )
            for index in (1, 2)
        ]
        expenses = []
        for index, sub_balance in enumerate(sub_balances, start=1):
            category = BudgetCategory.objects.create(
                budget_month=self.budget_month,
                sub_balance=sub_balance,
                name=f"Category {index}",
                estimated_amount=Decimal("0"),
            )
            expenses.append(
                DailyExpense.objects.create(
                    budget_month=self.budget_month,
                    sub_balance=sub_balance,
                    category=category,
                    expense_date="2026-08-12",
                    amount=Decimal("1000"),
                    note=f"Transaction {index}",
                )
            )

        response = self.client.get(
            reverse("budget:dashboard"),
            {
                "tab": "expenses",
                "expense_type": "sub",
                "expense_month": self.budget_month.id,
                "sub_balance": sub_balances[0].id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [expense.id for expense in response.context["expenses"]],
            [expenses[0].id],
        )
        self.assertContains(response, "Transaction 1")
        self.assertNotContains(response, "Transaction 2")
        self.assertEqual(
            response.context["selected_sub_balance"].id, sub_balances[0].id
        )

    def test_delete_sub_balance_from_dashboard(self):
        second = Balance.objects.create(user=self.user, name="Savings")

        response = self.client.post(
            reverse("budget:dashboard"),
            {"action": "delete_balance", "balance_id": second.id},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Balance.objects.filter(pk=second.id).exists())

    def test_cannot_delete_only_sub_balance(self):
        response = self.client.post(
            reverse("budget:dashboard"),
            {"action": "delete_balance", "balance_id": self.balance.id},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Balance.objects.filter(pk=self.balance.id).exists())

    def test_dashboard_redirects_anonymous_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("budget:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_delete_category_budget_from_dashboard(self):
        category = self.budget_month.categories.create(
            name="Books",
            estimated_amount=Decimal("150000"),
            period_index=None,
        )

        response = self.client.post(
            reverse("budget:dashboard"),
            {"action": "delete_category_budget", "category_id": category.id},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            BudgetMonth.objects.get(pk=self.budget_month.id)
            .categories.filter(pk=category.id)
            .exists()
        )

    def test_dashboard_shows_all_expenses_for_selected_month(self):
        category = self.budget_month.categories.get(period_index=1)
        for i in range(60):
            DailyExpense.objects.create(
                budget_month=self.budget_month,
                category=category,
                expense_date="2026-08-05",
                amount=Decimal("1000") + Decimal(i),
                note=f"Expense {i}",
            )

        response = self.client.get(reverse("budget:dashboard"), {"tab": "expenses"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["expenses"]), 60)

    def test_dashboard_debug_rows_include_period_bucket_for_each_expense(self):
        category = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category,
            expense_date="2026-08-05",
            amount=Decimal("62800"),
            note="Period 1 expense",
        )

        response = self.client.get(reverse("budget:dashboard"), {"tab": "expenses"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("expense_debug_rows", response.context)
        self.assertEqual(len(response.context["expense_debug_rows"]), 1)
        self.assertEqual(response.context["expense_debug_rows"][0]["period_name"], "Days 1-10")

    def test_report_status_for_actual_spend_uses_deficit_label(self):
        category = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category,
            expense_date="2026-08-05",
            amount=Decimal("62800"),
            note="Period 1 expense",
        )

        report = generate_monthly_report(self.budget_month.id)
        period_report = report.period_reports[0]

        self.assertEqual(period_report.status, "deficit")

    def test_report_status_for_negative_actual_amount_is_deficit(self):
        category = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category,
            expense_date="2026-08-05",
            amount=Decimal("-150000"),
            note="Unexpected money",
        )

        report = generate_monthly_report(self.budget_month.id)
        period_report = report.period_reports[0]

        self.assertEqual(period_report.status, "deficit")

    def test_report_period_actual_uses_only_matching_category(self):
        period_category = self.budget_month.categories.get(period_index=1)
        custom_category = self.budget_month.categories.create(
            name="Shopping",
            estimated_amount=Decimal("50000"),
            period_index=None,
        )

        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=period_category,
            expense_date="2026-08-05",
            amount=Decimal("62800"),
            note="Period 1 expense",
        )
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=custom_category,
            expense_date="2026-08-08",
            amount=Decimal("78000"),
            note="Other category expense",
        )

        report = generate_monthly_report(self.budget_month.id)
        period_report = report.period_reports[0]

        self.assertEqual(period_report.actual_amount, Decimal("62800"))

    def test_add_extra_money_with_manual_category_name(self):
        response = self.client.post(
            reverse("budget:dashboard"),
            {
                "action": "add_extra_money",
                "budget_month": self.budget_month.id,
                "new_category_name": "Bonus",
                "expense_date": "2026-08-05",
                "amount": "150000",
                "note": "Unexpected bonus",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            self.budget_month.categories.filter(name="Bonus", period_index__isnull=True).exists()
        )
        expense = DailyExpense.objects.get(note="Unexpected bonus")
        self.assertEqual(expense.category.name, "Bonus")
        self.assertEqual(expense.amount, Decimal("-150000"))

    def test_negative_amounts_are_treated_as_expenses_not_income(self):
        # Mirror the Linn Latt Loon case: only negative ("extra money") entries,
        # with category budgets of zero. They must be treated as expenditures so
        # the remaining balance is reduced, never inflated by adding them back.
        self.budget_month.total_money = Decimal("190000")
        self.budget_month.save(update_fields=["total_money"])

        for name, amount in [
            ("Sim Reconfigure", "10000"),
            ("Visa Card", "40000"),
            ("Lunch", "10000"),
            ("For Uniform & Other related Accessories", "112300"),
            ("For Drinking", "7000"),
        ]:
            category = self.budget_month.categories.create(
                name=name, estimated_amount=Decimal("0"), period_index=None
            )
            DailyExpense.objects.create(
                budget_month=self.budget_month,
                category=category,
                expense_date="2026-08-21",
                amount=Decimal("-" + amount),
                note=name,
            )

        report = generate_monthly_report(self.budget_month.id)

        # 10,000 + 40,000 + 10,000 + 112,300 + 7,000 = 179,300
        self.assertEqual(report.total_actual_spent, Decimal("179300"))
        self.assertEqual(report.remaining_balance, Decimal("10700"))


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="admin", email="admin@example.com", password="Passw0rd!x",
            is_staff=True,
        )
        self.regular_user = User.objects.create_user(
            username="regular", email="regular@example.com", password="Passw0rd!x"
        )
        self.pocket = PocketUser.objects.create(
            name="Alice", email="alice@example.com", auth_user=self.regular_user
        )
        self.balance = Balance.objects.create(user=self.pocket, name="Main Wallet")
        self.budget_month = BudgetMonth.objects.create(
            user=self.pocket,
            balance=self.balance,
            year=2026,
            month=8,
            total_money=Decimal("500000"),
        )
        self.category = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=self.category,
            expense_date="2026-08-05",
            amount=Decimal("30000"),
            note="Lunch",
        )

    def test_admin_dashboard_redirects_anonymous_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("budget:admin_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_admin_dashboard_denies_non_staff(self):
        self.client.force_login(self.regular_user)
        response = self.client.get(reverse("budget:admin_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("budget:dashboard"))

    def test_admin_dashboard_accessible_to_staff(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("budget:admin_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin Dashboard")

    def test_admin_dashboard_summary_counts(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("budget:admin_dashboard"))
        self.assertEqual(response.context["total_users"], 1)
        self.assertEqual(response.context["total_balances"], 1)
        self.assertEqual(response.context["total_months"], 1)
        self.assertEqual(response.context["total_categories"], 3)
        self.assertEqual(response.context["total_expenses"], 1)

    def test_admin_dashboard_financial_summary(self):
        sub_balance = SubBalance.objects.create(
            budget_month=self.budget_month,
            name="Travel",
            balance_amount=Decimal("10000"),
        )
        sub_category = BudgetCategory.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            name="Bus",
            estimated_amount=Decimal("0"),
        )
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            sub_balance=sub_balance,
            category=sub_category,
            expense_date="2026-08-06",
            amount=Decimal("5000"),
        )
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("budget:admin_dashboard"))
        self.assertEqual(response.context["total_allocated"], Decimal("500000"))
        self.assertEqual(response.context["total_direct_spent"], Decimal("30000"))
        self.assertEqual(response.context["total_sub_balance_spent"], Decimal("5000"))
        self.assertEqual(response.context["total_spent"], Decimal("35000"))
        self.assertEqual(response.context["total_remaining"], Decimal("465000"))
        self.assertContains(response, "Combined Actual Spending")

    def test_admin_login_redirects_staff_to_custom_dashboard(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("budget:admin_login"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("budget:admin_dashboard"))


class ZeroSpendAdjustmentTests(TestCase):
    def setUp(self):
        self.user = PocketUser.objects.create(name="Test User", email="test@example.com")
        self.budget_month = BudgetMonth.objects.create(
            user=self.user,
            year=2026,
            month=8,
            total_money=Decimal("500000"),
        )
        create_default_period_categories(self.budget_month)

    def test_zero_spend_period_budget_subtracted_from_available_money(self):
        # Give period 1 a budget of 100,000 with zero spend.
        category_1 = self.budget_month.categories.get(period_index=1)
        category_1.estimated_amount = Decimal("100000")
        category_1.save()

        report = generate_monthly_report(self.budget_month.id)

        self.assertIn("Days 1-10", report.zero_spend_periods)
        # adjusted = total - zero-spend budget = 500,000 - 100,000
        self.assertEqual(report.adjusted_total_money, Decimal("400000"))
        self.assertNotEqual(report.adjusted_total_money, report.total_money)

    def test_multiple_zero_spend_periods_subtract_all_budgets(self):
        category_1 = self.budget_month.categories.get(period_index=1)
        category_1.estimated_amount = Decimal("100000")
        category_1.save(update_fields=["estimated_amount"])

        category_2 = self.budget_month.categories.get(period_index=2)
        category_2.estimated_amount = Decimal("200000")
        category_2.save(update_fields=["estimated_amount"])

        report = generate_monthly_report(self.budget_month.id)

        self.assertIn("Days 1-10", report.zero_spend_periods)
        self.assertIn("Days 11-20", report.zero_spend_periods)
        self.assertEqual(report.adjusted_total_money, Decimal("200000"))

    def test_no_zero_spend_means_adjusted_matches_total(self):
        # Spend in every period so none is considered zero-spend.
        for period_index, amount in [(1, 10000), (2, 10000), (3, 10000)]:
            category = self.budget_month.categories.get(period_index=period_index)
            DailyExpense.objects.create(
                budget_month=self.budget_month,
                category=category,
                expense_date=date(2026, 8, period_index * 10),
                amount=Decimal(str(amount)),
                note=category.name,
            )

        report = generate_monthly_report(self.budget_month.id)

        self.assertEqual(report.zero_spend_periods, [])
        self.assertEqual(report.adjusted_total_money, report.total_money)

    def test_remaining_balance_is_total_minus_actual_spent(self):
        # Spend 100,000 in period 1.
        category_1 = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category_1,
            expense_date=date(2026, 8, 5),
            amount=Decimal("100000"),
            note="Period 1 expense",
        )

        report = generate_monthly_report(self.budget_month.id)

        # remaining = total_money - total_actual_spent = 500,000 - 100,000
        self.assertEqual(report.remaining_balance, Decimal("400000"))
        self.assertEqual(
            report.remaining_balance,
            report.total_money - report.total_actual_spent,
        )

    def test_remaining_balance_negative_when_spending_exceeds_total(self):
        # Spend more than the total money available.
        category_1 = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category_1,
            expense_date=date(2026, 8, 5),
            amount=Decimal("600000"),
            note="Overspend",
        )

        report = generate_monthly_report(self.budget_month.id)

        self.assertEqual(report.remaining_balance, Decimal("-100000"))
        self.assertLess(report.remaining_balance, 0)

    def test_remaining_balance_zero_when_spending_equals_total(self):
        # Spend exactly the total money available.
        category_1 = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category_1,
            expense_date=date(2026, 8, 5),
            amount=Decimal("500000"),
            note="Spend all",
        )

        report = generate_monthly_report(self.budget_month.id)

        self.assertEqual(report.remaining_balance, Decimal("0"))

    def test_remaining_balance_correct_when_all_periods_have_no_spending(self):
        # Give all three periods budgets that sum to the total money.
        budgets = [Decimal("100000"), Decimal("200000"), Decimal("200000")]
        for cat, val in zip(
            self.budget_month.categories.order_by("period_index"), budgets
        ):
            cat.estimated_amount = val
            cat.save(update_fields=["estimated_amount"])

        # No expenses at all across Days 1-10, 11-20 and 21-end.
        report = generate_monthly_report(self.budget_month.id)

        self.assertEqual(
            report.zero_spend_periods, ["Days 1-10", "Days 11-20", "Days 21-end"]
        )
        self.assertEqual(report.total_actual_spent, Decimal("0"))
        # Remaining balance must be total - spent (never total + spent), so with
        # zero spending the full total money remains.
        self.assertEqual(report.remaining_balance, report.total_money)
        self.assertEqual(
            report.remaining_balance,
            report.total_money - report.total_actual_spent,
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST_USER="test@example.com",
    EMAIL_HOST_PASSWORD="test-password",
)
class AutomatedEmailScheduleTests(TestCase):
    """Final full monthly report on the last day; per-category emails on
    actual spending."""

    def setUp(self):
        self.user = PocketUser.objects.create(name="Test User", email="test@example.com")
        self.budget_month = BudgetMonth.objects.create(
            user=self.user,
            year=2026,
            month=8,
            total_money=Decimal("500000"),
        )
        create_default_period_categories(self.budget_month)

    def test_no_emails_on_regular_day(self):
        results = process_automated_emails(today=date(2026, 8, 5))

        self.assertEqual(results["sent"], [])
        self.assertEqual(
            mail.outbox, [], "No emails should be sent on a regular day."
        )

    def test_final_report_sent_when_month_expires(self):
        results = process_automated_emails(today=date(2026, 8, 31))

        self.assertIn("August 2026 — monthly full", results["sent"])
        self.assertTrue(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month,
                report_type=ReportEmailLog.MONTHLY_FULL,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_no_duplicate_emails_on_second_run_same_day(self):
        process_automated_emails(today=date(2026, 8, 31))
        first_count = len(mail.outbox)

        results = process_automated_emails(today=date(2026, 8, 31))

        self.assertEqual(results["sent"], [])
        self.assertEqual(len(mail.outbox), first_count)

    def test_force_sends_monthly_report_any_day(self):
        results = process_automated_emails(today=date(2026, 8, 5), force=True)

        self.assertIn("August 2026 — monthly full", results["sent"])

    def test_no_category_email_when_category_has_no_spending(self):
        from budget.services.automated_email_service import send_category_report_email

        category_1 = self.budget_month.categories.get(period_index=1)

        sent = send_category_report_email(category_1)

        self.assertFalse(sent)
        self.assertEqual(mail.outbox, [])

    def test_category_email_sent_when_spending_recorded(self):
        from budget.services.automated_email_service import (
            category_report_type,
            send_category_report_email,
        )

        category_1 = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category_1,
            expense_date=date(2026, 8, 5),
            amount=Decimal("50000"),
            note="Lunch",
        )

        sent = send_category_report_email(category_1)

        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(category_1.name, mail.outbox[0].subject)
        self.assertTrue(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month,
                report_type=category_report_type(category_1.id),
            ).exists()
        )

    def test_category_email_sent_only_once_per_month(self):
        from budget.services.automated_email_service import send_category_report_email

        category_1 = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category_1,
            expense_date=date(2026, 8, 5),
            amount=Decimal("50000"),
            note="Lunch",
        )
        send_category_report_email(category_1)
        first_count = len(mail.outbox)

        # More spending in the same category/month: no second email.
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category_1,
            expense_date=date(2026, 8, 6),
            amount=Decimal("20000"),
            note="Dinner",
        )
        sent = send_category_report_email(category_1)

        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), first_count)

    def test_other_categories_without_spending_get_no_email(self):
        from budget.services.automated_email_service import send_category_report_email

        category_1 = self.budget_month.categories.get(period_index=1)
        category_2 = self.budget_month.categories.get(period_index=2)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category_1,
            expense_date=date(2026, 8, 5),
            amount=Decimal("50000"),
            note="Lunch",
        )

        self.assertTrue(send_category_report_email(category_1))
        # Category 2 has no spending -> no email.
        self.assertFalse(send_category_report_email(category_2))
        self.assertEqual(len(mail.outbox), 1)


class CurrencyDisplayNeverNegativeTests(TestCase):
    def test_format_mmk_never_shows_a_negative_sign(self):
        # Deficits and unexpected money are stored as negative Decimals.
        # They must be displayed as positive amounts while remaining signed
        # internally so calculations stay correct.
        self.assertEqual(format_mmk(Decimal("-100000")), "100,000 MMK")
        self.assertEqual(format_mmk(Decimal("80000")), "80,000 MMK")
        self.assertEqual(format_mmk(Decimal("-150000")), "150,000 MMK")
        self.assertEqual("-" not in format_mmk(Decimal("-1")), True)

    def test_format_mmk_handles_zero_and_none(self):
        self.assertEqual(format_mmk(Decimal("0")), "0 MMK")
        self.assertEqual(format_mmk(None), "0 MMK")

    def test_negative_remaining_balance_stays_signed_internally(self):
        # The calculation itself must keep the true (negative) value so the
        # reports and colouring remain correct.
        category_1 = self.budget_month.categories.get(period_index=1)
        DailyExpense.objects.create(
            budget_month=self.budget_month,
            category=category_1,
            expense_date=date(2026, 8, 5),
            amount=Decimal("600000"),
            note="Overspend",
        )

        report = generate_monthly_report(self.budget_month.id)
        self.assertEqual(report.remaining_balance, Decimal("-100000"))
        # But when surfaced to the user it is shown without the minus sign.
        self.assertEqual(format_mmk(report.remaining_balance), "100,000 MMK")

    def setUp(self):
        self.user = PocketUser.objects.create(name="Test User", email="test@example.com")
        self.budget_month = BudgetMonth.objects.create(
            user=self.user,
            year=2026,
            month=8,
            total_money=Decimal("500000"),
        )
        create_default_period_categories(self.budget_month)


class AdminUserManagementTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="manager",
            email="manager@example.com",
            password="Passw0rd!x",
            is_staff=True,
        )
        self.target_auth = User.objects.create_user(
            username="bob", email="bob@example.com", password="OldPass123!"
        )
        self.target = PocketUser.objects.create(
            name="Bob", email="bob@example.com", auth_user=self.target_auth
        )
        self.balance = Balance.objects.create(
            user=self.target, name="Main Wallet"
        )
        self.budget_month = BudgetMonth.objects.create(
            user=self.target,
            balance=self.balance,
            year=2026,
            month=8,
            total_money=Decimal("100000"),
        )
        self.client.force_login(self.staff_user)

    def test_admin_users_denies_non_staff(self):
        clerk = User.objects.create_user(
            username="clerk", password="Passw0rd!x"
        )
        self.client.force_login(clerk)
        response = self.client.get(reverse("budget:admin_users"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("budget:dashboard"))

    def test_admin_users_redirects_anonymous_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("budget:admin_users"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_admin_users_lists_users(self):
        response = self.client.get(reverse("budget:admin_users"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bob")
        self.assertContains(response, "bob@example.com")

    def test_admin_reset_password(self):
        response = self.client.post(
            reverse("budget:admin_users"),
            {
                "action": "reset_password",
                "user": self.target.id,
                "password1": "NewPassword123!",
                "password2": "NewPassword123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.target_auth.refresh_from_db()
        self.assertTrue(self.target_auth.check_password("NewPassword123!"))

    def test_admin_reset_password_rejects_mismatch(self):
        response = self.client.post(
            reverse("budget:admin_users"),
            {
                "action": "reset_password",
                "user": self.target.id,
                "password1": "NewPassword123!",
                "password2": "Different123!",
            },
        )
        # Form errors return to the page and the password stays unchanged.
        self.target_auth.refresh_from_db()
        self.assertTrue(self.target_auth.check_password("OldPass123!"))

    def test_admin_cannot_delete_own_account(self):
        own = PocketUser.objects.create(
            name="Manager",
            email="manager@example.com",
            auth_user=self.staff_user,
        )
        response = self.client.post(
            reverse("budget:admin_users"),
            {"action": "delete_user", "user_id": own.id},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.staff_user.pk).exists())
        self.assertTrue(PocketUser.objects.filter(pk=own.pk).exists())

    def test_admin_delete_user_removes_pocket_and_auth(self):
        response = self.client.post(
            reverse("budget:admin_users"),
            {"action": "delete_user", "user_id": self.target.id},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PocketUser.objects.filter(pk=self.target.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.target_auth.pk).exists())
        self.assertFalse(
            BudgetMonth.objects.filter(pk=self.budget_month.pk).exists()
        )

    def test_admin_toggle_active(self):
        response = self.client.post(
            reverse("budget:admin_users"),
            {"action": "toggle_active", "user_id": self.target.id},
        )
        self.assertEqual(response.status_code, 302)
        self.target_auth.refresh_from_db()
        self.assertFalse(self.target_auth.is_active)

    def test_admin_cannot_toggle_own_account(self):
        own = PocketUser.objects.create(
            name="Manager",
            email="manager@example.com",
            auth_user=self.staff_user,
        )
        self.client.post(
            reverse("budget:admin_users"),
            {"action": "toggle_active", "user_id": own.id},
        )
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.is_active)


class ChangePasswordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sally", email="sally@example.com", password="OldPass123!"
        )
        PocketUser.objects.create(
            name="Sally", email="sally@example.com", auth_user=self.user
        )
        self.client.force_login(self.user)

    def test_change_password_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("budget:change_password"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_change_password_updates_credentials(self):
        response = self.client.post(
            reverse("budget:change_password"),
            {
                "old_password": "OldPass123!",
                "new_password1": "BrandNewPass1!",
                "new_password2": "BrandNewPass1!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("BrandNewPass1!"))

    def test_change_password_rejects_wrong_current(self):
        response = self.client.post(
            reverse("budget:change_password"),
            {
                "old_password": "TotallyWrong!",
                "new_password1": "BrandNewPass1!",
                "new_password2": "BrandNewPass1!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OldPass123!"))

    def test_change_password_keeps_user_logged_in(self):
        self.client.post(
            reverse("budget:change_password"),
            {
                "old_password": "OldPass123!",
                "new_password1": "BrandNewPass1!",
                "new_password2": "BrandNewPass1!",
            },
        )
        response = self.client.get(reverse("budget:dashboard"))
        self.assertEqual(response.status_code, 200)

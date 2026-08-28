from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from budget.currency import format_mmk
from budget.models import (
    Balance,
    BudgetMonth,
    DailyExpense,
    PocketUser,
    ReportEmailLog,
)
from budget.services.automated_email_service import process_automated_emails
from budget.services.category_service import create_default_period_categories
from budget.services.report_service import generate_monthly_report


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
                "balance": self.balance.id,
                "year": "2027",
                "month": "9",
                "total_money": "600000",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.budget_month.refresh_from_db()
        self.assertEqual(self.budget_month.year, 2027)
        self.assertEqual(self.budget_month.month, 9)
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
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("budget:admin_dashboard"))
        self.assertEqual(response.context["total_allocated"], Decimal("500000"))
        self.assertEqual(response.context["total_spent"], Decimal("30000"))
        self.assertEqual(response.context["total_remaining"], Decimal("470000"))

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
class AutomatedEmailZeroSpendGatingTests(TestCase):
    def setUp(self):
        self.user = PocketUser.objects.create(name="Test User", email="test@example.com")
        self.budget_month = BudgetMonth.objects.create(
            user=self.user,
            year=2026,
            month=8,
            total_money=Decimal("500000"),
        )
        create_default_period_categories(self.budget_month)

    def test_email_sent_when_zero_spend_adjustment_exists(self):
        # No expenses at all -> all three periods are zero-spend.
        results = process_automated_emails(today=date(2026, 8, 21))

        self.assertIn("August 2026 — monthly full", results["sent"])
        self.assertTrue(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month,
                report_type=ReportEmailLog.MONTHLY_FULL,
            ).exists()
        )

    def test_email_skipped_when_no_zero_spend(self):
        # Spend in every period so no zero-spend adjustment triggers.
        for period_index, amount in [(1, 10000), (2, 10000), (3, 10000)]:
            category = self.budget_month.categories.get(period_index=period_index)
            DailyExpense.objects.create(
                budget_month=self.budget_month,
                category=category,
                expense_date=date(2026, 8, period_index * 10),
                amount=Decimal(str(amount)),
                note=category.name,
            )

        results = process_automated_emails(today=date(2026, 8, 21))

        self.assertEqual(results["sent"], [])
        self.assertFalse(
            ReportEmailLog.objects.filter(
                budget_month=self.budget_month,
                report_type=ReportEmailLog.MONTHLY_FULL,
            ).exists()
        )


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

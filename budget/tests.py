from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from budget.models import BudgetMonth, DailyExpense, PocketUser
from budget.services.category_service import create_default_period_categories
from budget.services.report_service import generate_monthly_report


class DashboardBudgetAndExtraMoneyTests(TestCase):
    def setUp(self):
        self.user = PocketUser.objects.create(name="Test User", email="test@example.com")
        self.budget_month = BudgetMonth.objects.create(
            user=self.user,
            year=2026,
            month=8,
            total_money=Decimal("500000"),
        )
        create_default_period_categories(self.budget_month)

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
                "amount": "55000",
                "note": "Groceries updated",
            },
        )

        self.assertEqual(response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal("55000"))
        self.assertEqual(expense.note, "Groceries updated")

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
                "user": self.user.id,
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

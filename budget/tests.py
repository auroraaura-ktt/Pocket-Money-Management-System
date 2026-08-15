from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from budget.models import BudgetMonth, DailyExpense, PocketUser
from budget.services.category_service import create_default_period_categories


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

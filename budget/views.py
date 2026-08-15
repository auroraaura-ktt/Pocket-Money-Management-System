"""Views for Pocket Money Management System."""

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from budget.forms import (
    BudgetMonthForm,
    CategoryBudgetUpdateForm,
    CustomCategoryBudgetForm,
    DailyExpenseForm,
    PeriodBudgetForm,
    ReportSelectForm,
    UnexpectedMoneyForm,
    UserForm,
)
from budget.models import BudgetCategory, BudgetMonth, DailyExpense, ReportEmailLog
from budget.services.category_service import create_default_period_categories
from budget.services.email_service import (
    send_monthly_report_email,
    send_period_report_email,
)
from budget.services.report_service import generate_monthly_report


def _dashboard_redirect(tab=None):
    url = reverse("budget:dashboard")
    if tab:
        url = f"{url}?tab={tab}"
    return redirect(url)


def home(request):
    return render(request, "budget/home.html")


def dashboard(request):
    tab = request.GET.get("tab", "setup")
    user_form = UserForm()
    budget_form = BudgetMonthForm()
    period_budget_form = PeriodBudgetForm()
    custom_budget_form = CustomCategoryBudgetForm()
    expense_form = DailyExpenseForm()
    category_update_form = CategoryBudgetUpdateForm()
    unexpected_money_form = UnexpectedMoneyForm()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "register_user":
            user_form = UserForm(request.POST)
            if user_form.is_valid():
                user_form.save()
                messages.success(request, "User registered successfully.")
                return redirect("budget:dashboard")

        elif action == "create_budget":
            budget_form = BudgetMonthForm(request.POST)
            if budget_form.is_valid():
                budget_month = budget_form.save()
                messages.success(
                    request,
                    f"Monthly budget created for {budget_month.month_label} "
                    f"with total {budget_month.total_money} MMK.",
                )
                return redirect("budget:dashboard")

        elif action == "edit_budget_month":
            budget_month_id = request.POST.get("budget_month_id")
            budget_month = BudgetMonth.objects.filter(pk=budget_month_id).first()
            if not budget_month:
                messages.error(request, "Budget month not found.")
                return redirect("budget:dashboard")

            budget_form = BudgetMonthForm(request.POST, instance=budget_month)
            if budget_form.is_valid():
                updated_month = budget_form.save()
                messages.success(
                    request,
                    f"Budget month updated to {updated_month.month_label} "
                    f"with total {updated_month.total_money} MMK.",
                )
                return redirect("budget:dashboard")

        elif action == "delete_budget_month":
            budget_month_id = request.POST.get("budget_month_id")
            budget_month = BudgetMonth.objects.filter(pk=budget_month_id).first()
            if not budget_month:
                messages.error(request, "Budget month not found.")
                return redirect("budget:dashboard")

            budget_month_label = budget_month.month_label
            budget_month.delete()
            messages.success(request, f"Budget month '{budget_month_label}' deleted.")
            return redirect("budget:dashboard")

        elif action == "save_period_budget":
            period_budget_form = PeriodBudgetForm(request.POST)
            if period_budget_form.is_valid():
                budget_month = period_budget_form.save()
                messages.success(
                    request,
                    f"Budget estimates saved for {budget_month.month_label}. "
                    f"Total money updated to {budget_month.total_money} MMK.",
                )
                return _dashboard_redirect("budget")

        elif action == "save_custom_category_budget":
            custom_budget_form = CustomCategoryBudgetForm(request.POST)
            if custom_budget_form.is_valid():
                category = custom_budget_form.save()
                messages.success(
                    request,
                    f"Budget for '{category.name}' set to {category.estimated_amount} MMK.",
                )
                return _dashboard_redirect("budget")

        elif action == "update_category_budget":
            post_data = request.POST.copy()
            category_id = post_data.get("category_id") or post_data.get("category")
            if category_id:
                post_data["category"] = category_id
            category_update_form = CategoryBudgetUpdateForm(post_data)
            if category_update_form.is_valid():
                category = category_update_form.save()
                messages.success(
                    request,
                    f"Budget for '{category.name}' updated to {category.estimated_amount} MMK.",
                )
                return _dashboard_redirect("budget")

        elif action == "add_expense":
            month_id = request.POST.get("budget_month")
            if month_id:
                budget_month_obj = BudgetMonth.objects.filter(pk=month_id).first()
                if budget_month_obj:
                    create_default_period_categories(budget_month_obj)
            expense_form = DailyExpenseForm(request.POST)
            if expense_form.is_valid():
                expense = expense_form.save()
                messages.success(
                    request,
                    f"Expense recorded under {expense.category.name}.",
                )
                return _dashboard_redirect("expenses")

        elif action == "update_expense":
            expense_id = request.POST.get("expense_id")
            expense_instance = DailyExpense.objects.filter(pk=expense_id).first()
            if not expense_instance:
                messages.error(request, "Expense not found.")
                return _dashboard_redirect("expenses")

            post_data = request.POST.copy()
            for field_name, value in {
                "budget_month": expense_instance.budget_month_id,
                "category": expense_instance.category_id,
                "expense_date": expense_instance.expense_date,
            }.items():
                if not post_data.get(field_name):
                    post_data[field_name] = value

            expense_form = DailyExpenseForm(post_data, instance=expense_instance)
            if expense_form.is_valid():
                updated_expense = expense_form.save()
                messages.success(
                    request,
                    f"Expense updated for {updated_expense.category.name}: {updated_expense.amount} MMK.",
                )
                return _dashboard_redirect("expenses")

        elif action == "delete_expense":
            expense_id = request.POST.get("expense_id")
            expense_instance = DailyExpense.objects.filter(pk=expense_id).first()
            if not expense_instance:
                messages.error(request, "Expense not found.")
                return _dashboard_redirect("expenses")

            expense_label = f"{expense_instance.category.name} ({expense_instance.amount} MMK)"
            expense_instance.delete()
            messages.success(request, f"Expense deleted for {expense_label}.")
            return _dashboard_redirect("expenses")

        elif action == "delete_category_budget":
            category_id = request.POST.get("category_id")
            category_instance = BudgetCategory.objects.filter(pk=category_id).first()
            if not category_instance:
                messages.error(request, "Category not found.")
                return _dashboard_redirect("budget")

            category_name = category_instance.name
            category_instance.delete()
            messages.success(request, f"Category budget '{category_name}' deleted.")
            return _dashboard_redirect("budget")

        elif action == "add_extra_money":
            month_id = request.POST.get("budget_month")
            if month_id:
                budget_month_obj = BudgetMonth.objects.filter(pk=month_id).first()
                if budget_month_obj:
                    create_default_period_categories(budget_month_obj)
            unexpected_money_form = UnexpectedMoneyForm(request.POST)
            if unexpected_money_form.is_valid():
                extra_money = unexpected_money_form.save()
                messages.success(
                    request,
                    f"Extra money recorded for {extra_money.category.name}: {extra_money.amount} MMK.",
                )
                return _dashboard_redirect("expenses")

    budget_months = BudgetMonth.objects.select_related("user").prefetch_related(
        "categories"
    )
    categories = BudgetCategory.objects.select_related("budget_month").order_by(
        "budget_month", "period_index", "id"
    )
    expenses = DailyExpense.objects.select_related("category", "budget_month").order_by(
        "-expense_date", "-id"
    )
    expense_debug_rows = []

    expense_comparison_month = None
    period_comparisons = []
    if tab == "expenses":
        month_id = request.GET.get("expense_month")
        if month_id:
            expense_comparison_month = BudgetMonth.objects.filter(pk=month_id).first()
        elif budget_months.exists():
            expense_comparison_month = budget_months.first()

        if expense_comparison_month:
            create_default_period_categories(expense_comparison_month)
            report = generate_monthly_report(expense_comparison_month.id)
            period_comparisons = report.period_reports
            expenses = expenses.filter(budget_month_id=expense_comparison_month.id)

            period_by_index = {
                period.index: period.name for period in __import__("budget.periods", fromlist=["PERIOD_DEFINITIONS"]).PERIOD_DEFINITIONS
            }
            for expense in expenses:
                period_index = None
                if expense.category and expense.category.period_index is not None:
                    period_index = expense.category.period_index
                expense_debug_rows.append(
                    {
                        "id": expense.id,
                        "date": expense.expense_date,
                        "category_name": expense.category.name,
                        "amount": expense.amount,
                        "note": expense.note,
                        "period_name": period_by_index.get(period_index, "Custom") if period_index is not None else "Custom",
                    }
                )

    return render(
        request,
        "budget/dashboard.html",
        {
            "tab": tab,
            "user_form": user_form,
            "budget_form": budget_form,
            "period_budget_form": period_budget_form,
            "custom_budget_form": custom_budget_form,
            "expense_form": expense_form,
            "category_update_form": category_update_form,
            "unexpected_money_form": unexpected_money_form,
            "budget_months": budget_months,
            "categories": categories,
            "expenses": expenses,
            "expense_comparison_month": expense_comparison_month,
            "period_comparisons": period_comparisons,
            "expense_debug_rows": expense_debug_rows,
        },
    )


def reports(request):
    report = None
    selected_month = None
    form = ReportSelectForm(request.GET or None)

    if request.method == "POST":
        action = request.POST.get("action")
        form = ReportSelectForm(request.POST)

        if form.is_valid():
            selected_month = form.cleaned_data["budget_month"]

            if action == "generate":
                report = generate_monthly_report(selected_month.id)
            elif action == "email":
                report = generate_monthly_report(selected_month.id)
                user = selected_month.user
                month_label = selected_month.month_label

                # Send all 3 period emails (1-10, 11-20, 21-end)
                period_types = [
                    (1, ReportEmailLog.PERIOD_1),
                    (2, ReportEmailLog.PERIOD_2),
                    (3, ReportEmailLog.PERIOD_3),
                ]
                sent_count = 0
                for period_index, report_type in period_types:
                    period = report.period_reports[period_index - 1]
                    if send_period_report_email(
                        user.email, user.name, report, period, month_label
                    ):
                        ReportEmailLog.objects.get_or_create(
                            budget_month=selected_month, report_type=report_type
                        )
                        sent_count += 1

                # Send the full monthly report
                if send_monthly_report_email(user.email, user.name, report):
                    ReportEmailLog.objects.get_or_create(
                        budget_month=selected_month,
                        report_type=ReportEmailLog.MONTHLY_FULL,
                    )
                    sent_count += 1

                if sent_count == 4:
                    messages.success(
                        request,
                        f"All 4 emails (Days 1-10, 11-20, 21-end, and full monthly report) sent to {user.email}.",
                    )
                elif sent_count > 0:
                    messages.success(
                        request,
                        f"{sent_count} of 4 report emails sent to {user.email}.",
                    )
                else:
                    messages.warning(
                        request,
                        "Emails could not be sent. Check SMTP settings in your .env file.",
                    )
                return redirect(f"{request.path}?budget_month={selected_month.id}")

    elif form.is_valid() and form.cleaned_data.get("budget_month"):
        selected_month = form.cleaned_data["budget_month"]
        report = generate_monthly_report(selected_month.id)

    return render(
        request,
        "budget/reports.html",
        {
            "form": form,
            "report": report,
            "selected_month": selected_month,
        },
    )


@require_GET
def categories_for_month(request, month_id):
    """Return all categories for a budget month."""
    budget_month = BudgetMonth.objects.filter(pk=month_id).first()
    if not budget_month:
        return JsonResponse({"categories": []})
    create_default_period_categories(budget_month)
    categories = (
        BudgetCategory.objects.filter(budget_month_id=month_id)
        .order_by("period_index", "id")
        .values("id", "name", "period_index")
    )
    return JsonResponse({"categories": list(categories)})
"""Views for Pocket Money Management System."""

import logging
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, F, OuterRef, Sum
from django.db.models.functions import Abs
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from budget.forms import (
    AdminResetPasswordForm,
    BalanceForm,
    BudgetMonthForm,
    CategoryBudgetUpdateForm,
    CustomCategoryBudgetForm,
    DailyExpenseForm,
    ExpenseCategoryCreateForm,
    MainBalanceUpdateForm,
    PeriodBudgetForm,
    ReportSelectForm,
    SubBalanceForm,
    SubBalanceCategoryForm,
    SubBalanceTransactionForm,
    UnexpectedMoneyForm,
    UserRegistrationForm,
    INPUT_CLASS,
)
from budget.models import (
    Balance,
    BudgetCategory,
    BudgetMonth,
    DailyExpense,
    PocketUser,
    ReportEmailLog,
    SubBalance,
)
from budget.services.automated_email_service import (
    send_category_report_email,
)
from budget.services.category_service import create_default_period_categories
from budget.services.email_service import (
    send_monthly_report_email,
    send_sub_balance_report_email,
)
from budget.services.report_service import generate_monthly_report

logger = logging.getLogger(__name__)


def _style_auth_form(form):
    """Apply the project's Tailwind input styling to AuthenticationForm fields."""
    for field_name in ("username", "password"):
        field = form.fields.get(field_name)
        if field is not None:
            field.widget.attrs.update({"class": INPUT_CLASS})


def _style_form_fields(form):
    """Apply the project's Tailwind input styling to every field on a form."""
    for field in form.fields.values():
        field.widget.attrs.update({"class": INPUT_CLASS})


def _clear_user_sessions(user):
    """Log out every active session belonging to ``user``.

    Used after an admin resets a user's password so any existing login
    sessions are invalidated and the new password must be used again.
    """
    from django.contrib.sessions.models import Session

    user_id = str(user.pk)
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if data.get("_auth_user_id") == user_id:
            session.delete()


def _current_pocket_user(request) -> PocketUser:
    """Return the PocketUser that owns the logged-in account's budget data."""
    auth_user = request.user
    pocket = getattr(auth_user, "pocket_user_profile", None)
    if pocket is None:
        pocket = PocketUser.objects.filter(email=auth_user.email).first()
        if pocket is None:
            pocket = PocketUser.objects.create(
                name=auth_user.username, email=auth_user.email, auth_user=auth_user
            )
        else:
            pocket.auth_user = auth_user
            pocket.save(update_fields=["auth_user"])
    if not pocket.balances.exists():
        Balance.objects.create(user=pocket, name="Main Wallet")
    return pocket


def _dashboard_redirect(tab=None):
    url = reverse("budget:dashboard")
    if tab:
        url = f"{url}?tab={tab}"
    return redirect(url)


def _expense_dashboard_redirect(expense_type="main", month_id=None, sub_balance_id=None):
    query = {"tab": "expenses", "expense_type": expense_type}
    if month_id:
        query["expense_month"] = month_id
    if sub_balance_id:
        query["sub_balance"] = sub_balance_id
    return redirect(f"{reverse('budget:dashboard')}?{urlencode(query)}")


def _scoped_instance(queryset, object_id):
    try:
        object_id = int(object_id)
    except (TypeError, ValueError):
        return None
    if object_id < 1:
        return None
    return queryset.filter(pk=object_id).first()


def home(request):
    return render(request, "budget/home.html")


def register_view(request):
    """Register a new user account and log them in."""
    if request.user.is_authenticated:
        return redirect("budget:dashboard")

    form = UserRegistrationForm()
    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(
                request,
                f"Account created. Welcome aboard, {user.username}!",
            )
            return redirect("budget:dashboard")

    return render(request, "budget/register.html", {"form": form})


def login_view(request):
    """Standard login for regular users."""
    if request.user.is_authenticated:
        return redirect("budget:dashboard")

    form = AuthenticationForm()
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")
            return redirect(request.POST.get("next") or "budget:dashboard")

    _style_auth_form(form)
    return render(request, "budget/login.html", {"form": form})


def admin_login_view(request):
    """Login form intended for administrators (staff / superuser)."""
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("budget:admin_dashboard")

    form = AuthenticationForm()
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if not user.is_staff:
                messages.error(
                    request,
                    "This account does not have administrator privileges.",
                )
            else:
                login(request, user)
                messages.success(request, f"Welcome, {user.username}!")
                return redirect(
                    request.POST.get("next") or reverse("budget:admin_dashboard")
                )

    _style_auth_form(form)
    return render(request, "budget/admin_login.html", {"form": form})


def logout_view(request):
    """Log out the current user (user or admin)."""
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("budget:home")


@login_required
def change_password_view(request):
    """Let a logged-in user reset their own password."""
    form = PasswordChangeForm(user=request.user)
    if request.method == "POST":
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            # Keep the user logged in after their password hash changes.
            update_session_auth_hash(request, user)
            messages.success(request, "Your password has been changed.")
            return redirect("budget:dashboard")

    _style_form_fields(form)
    return render(request, "budget/change_password.html", {"form": form})


@login_required
def admin_users(request):
    """Custom staff page to manage users (delete, reset password, activate)."""
    if not request.user.is_staff:
        messages.error(request, "You do not have administrator access.")
        return redirect("budget:dashboard")

    users_qs = (
        PocketUser.objects.select_related("auth_user")
        .annotate(num_balances=Count("balances"), num_months=Count("months"))
        .order_by("name")
    )
    reset_form = AdminResetPasswordForm()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "reset_password":
            reset_form = AdminResetPasswordForm(request.POST)
            if reset_form.is_valid():
                pocket = reset_form.cleaned_data["user"]
                auth_user = pocket.auth_user
                new_password = reset_form.cleaned_data["password1"]
                auth_user.set_password(new_password)
                auth_user.save(update_fields=["password"])
                _clear_user_sessions(auth_user)
                messages.success(
                    request,
                    f"Password reset for {pocket.name} ({pocket.email}). "
                    f"New password: {new_password}",
                )
                return redirect("budget:admin_users")

        elif action == "toggle_active":
            user_id = request.POST.get("user_id")
            pocket = PocketUser.objects.select_related("auth_user").filter(
                pk=user_id
            ).first()
            if not pocket:
                messages.error(request, "User not found.")
            elif pocket.auth_user_id == request.user.id:
                messages.error(request, "You cannot change your own account status.")
            elif pocket.auth_user is None:
                messages.error(
                    request, f"{pocket.name} has no linked login account."
                )
            else:
                auth_user = pocket.auth_user
                auth_user.is_active = not auth_user.is_active
                auth_user.save(update_fields=["is_active"])
                state = "activated" if auth_user.is_active else "deactivated"
                messages.success(
                    request, f"Account for {pocket.name} {state}."
                )
            return redirect("budget:admin_users")

        elif action == "delete_user":
            user_id = request.POST.get("user_id")
            pocket = PocketUser.objects.select_related("auth_user").filter(
                pk=user_id
            ).first()
            if not pocket:
                messages.error(request, "User not found.")
            elif pocket.auth_user_id == request.user.id:
                messages.error(
                    request, "You cannot delete your own account."
                )
            else:
                name = pocket.name
                email = pocket.email
                auth_user = pocket.auth_user
                pocket.delete()
                if auth_user is not None:
                    auth_user.delete()
                messages.success(
                    request,
                    f"User '{name}' ({email}) and all their data were deleted.",
                )
            return redirect("budget:admin_users")

    return render(
        request,
        "budget/admin_users.html",
        {
            "users": users_qs,
            "reset_form": reset_form,
        },
    )


@login_required
def main_balance_detail(request, month_id):
    pocket_user = _current_pocket_user(request)
    budget_month = BudgetMonth.objects.filter(
        pk=month_id, user=pocket_user
    ).select_related("user", "balance").first()
    if budget_month is None:
        messages.error(request, "Main Balance not found.")
        return _dashboard_redirect("setup")
    report = generate_monthly_report(budget_month.id)
    sub_balances = budget_month.sub_balances.all()
    sub_balance_rows = [{"sub_balance": item} for item in sub_balances]
    return render(
        request,
        "budget/main_balance_detail.html",
        {
            "main_balance": budget_month,
            "sub_balances": sub_balances,
            "sub_balance_rows": sub_balance_rows,
            "report": report,
            "monthly_report_sent": ReportEmailLog.objects.filter(
                budget_month=budget_month,
                report_type__in=[ReportEmailLog.MONTHLY_FULL, "monthly_prev"],
            ).exists(),
        },
    )


@login_required
def edit_main_balance(request, month_id):
    pocket_user = _current_pocket_user(request)
    budget_month = BudgetMonth.objects.filter(pk=month_id, user=pocket_user).first()
    if budget_month is None:
        messages.error(request, "Main Balance not found.")
        return _dashboard_redirect("setup")
    form = MainBalanceUpdateForm(request.POST or None, instance=budget_month)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Main Balance updated. Its reporting month is unchanged.")
        return redirect("budget:main_balance_detail", month_id=budget_month.id)
    return render(
        request,
        "budget/main_balance_form.html",
        {"form": form, "main_balance": budget_month},
    )


@login_required
@require_POST
def delete_main_balance(request, month_id):
    pocket_user = _current_pocket_user(request)
    budget_month = BudgetMonth.objects.filter(pk=month_id, user=pocket_user).first()
    if budget_month is None:
        messages.error(request, "Main Balance not found.")
        return _dashboard_redirect("setup")
    label = budget_month.name or budget_month.month_label
    with transaction.atomic():
        budget_month.delete()
    messages.success(request, f"Main Balance '{label}' and its dependent records deleted.")
    return _dashboard_redirect("setup")


@login_required
def create_sub_balance(request, month_id):
    pocket_user = _current_pocket_user(request)
    budget_month = BudgetMonth.objects.filter(pk=month_id, user=pocket_user).first()
    if budget_month is None:
        messages.error(request, "Main Balance not found.")
        return _dashboard_redirect("setup")
    form = SubBalanceForm(request.POST or None, parent_month=budget_month)
    if request.method == "POST" and form.is_valid():
        sub_balance = form.save(commit=False)
        sub_balance.budget_month = budget_month
        sub_balance.save()
        messages.success(request, f"Sub Balance '{sub_balance.name}' created.")
        return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)
    return render(
        request,
        "budget/sub_balance_form.html",
        {
            "form": form,
            "main_balance": budget_month,
            "is_create": True,
            "creation_date": timezone.localdate(),
        },
    )


@login_required
def sub_balance_detail(request, sub_balance_id):
    pocket_user = _current_pocket_user(request)
    sub_balance = SubBalance.objects.filter(
        pk=sub_balance_id, budget_month__user=pocket_user
    ).select_related("budget_month", "budget_month__user").first()
    if sub_balance is None:
        messages.error(request, "Sub Balance not found.")
        return _dashboard_redirect("budget")
    category_form = SubBalanceCategoryForm(sub_balance=sub_balance)
    transaction_form = SubBalanceTransactionForm(sub_balance=sub_balance)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_category":
            category_form = SubBalanceCategoryForm(request.POST, sub_balance=sub_balance)
            if category_form.is_valid():
                category_form.save()
                messages.success(request, "Sub Balance category added.")
                return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)
        elif action == "delete_category":
            category_id = request.POST.get("category_id")
            category = sub_balance.categories.filter(pk=category_id).first()
            if category is not None:
                if category.expenses.exists():
                    messages.error(
                        request,
                        "This category is used by existing transactions and cannot be deleted.",
                    )
                else:
                    category.delete()
                    messages.success(request, "Category deleted.")
            else:
                messages.error(request, "Category not found.")
            return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)
        elif action == "add_transaction":
            transaction_form = SubBalanceTransactionForm(request.POST, sub_balance=sub_balance)
            if transaction_form.is_valid():
                transaction_form.save()
                messages.success(request, "Sub Balance transaction recorded.")
                return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)
        elif action == "delete_transaction":
            sub_balance.expenses.filter(
                budget_month_id=sub_balance.budget_month_id,
                pk=request.POST.get("transaction_id"),
            ).delete()
            messages.success(request, "Sub Balance transaction deleted.")
            return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)
        elif action == "update_transaction":
            expense = sub_balance.expenses.filter(
                budget_month_id=sub_balance.budget_month_id,
                pk=request.POST.get("transaction_id"),
            ).first()
            if expense is not None:
                transaction_form = SubBalanceTransactionForm(request.POST, instance=expense, sub_balance=sub_balance)
                if transaction_form.is_valid():
                    transaction_form.save()
                    messages.success(request, "Sub Balance transaction updated.")
                    return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)
    categories = list(sub_balance.categories.all())
    for category in categories:
        category.actual_amount = sum(
            (
                abs(item.amount)
                for item in category.expenses.filter(
                    sub_balance=sub_balance,
                    budget_month_id=sub_balance.budget_month_id,
                )
            ),
            0,
        )
    transaction_edit_forms = {
        item.id: SubBalanceTransactionForm(instance=item, sub_balance=sub_balance)
        for item in sub_balance.expenses.filter(
            budget_month_id=sub_balance.budget_month_id
        ).select_related("category")
    }
    created_local = timezone.localtime(sub_balance.created_at)
    return render(
        request,
        "budget/sub_balance_detail.html",
        {
            "sub_balance": sub_balance,
            "main_balance": sub_balance.budget_month,
            "finish_sent": ReportEmailLog.objects.filter(
                budget_month=sub_balance.budget_month,
                report_type=f"sbfn_{sub_balance.id}",
            ).exists(),
            "scheduled_sent": ReportEmailLog.objects.filter(
                budget_month=sub_balance.budget_month,
                report_type=f"sb15_{sub_balance.id}",
            ).exists(),
            "report_due_date": (created_local + timedelta(days=15)).date(),
            "today": timezone.localdate(),
            "category_form": category_form,
            "transaction_form": transaction_form,
            "categories": categories,
            "transaction_edit_forms": transaction_edit_forms,
            "transactions": sub_balance.expenses.filter(
                budget_month_id=sub_balance.budget_month_id
            ).select_related("category"),
            "total_actual_spent": sub_balance.total_actual_spent,
            "remaining_balance": sub_balance.remaining_balance,
        },
    )


@login_required
def edit_sub_balance(request, sub_balance_id):
    pocket_user = _current_pocket_user(request)
    sub_balance = SubBalance.objects.filter(
        pk=sub_balance_id, budget_month__user=pocket_user
    ).select_related("budget_month").first()
    if sub_balance is None:
        messages.error(request, "Sub Balance not found.")
        return _dashboard_redirect("budget")
    form = SubBalanceForm(
        request.POST or None, instance=sub_balance, parent_month=sub_balance.budget_month
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.budget_month = sub_balance.budget_month
        saved.created_at = sub_balance.created_at
        saved.save()
        messages.success(request, "Sub Balance updated. Its parent and report deadline are unchanged.")
        return redirect("budget:sub_balance_detail", sub_balance_id=saved.id)
    return render(
        request,
        "budget/sub_balance_form.html",
        {"form": form, "main_balance": sub_balance.budget_month, "sub_balance": sub_balance},
    )


@login_required
@require_POST
def delete_sub_balance(request, sub_balance_id):
    pocket_user = _current_pocket_user(request)
    sub_balance = SubBalance.objects.filter(
        pk=sub_balance_id, budget_month__user=pocket_user
    ).select_related("budget_month").first()
    if sub_balance is None:
        messages.error(request, "Sub Balance not found.")
        return _dashboard_redirect("budget")
    report_keys = [f"sb15_{sub_balance.id}", f"sbfn_{sub_balance.id}"]
    report_keys.extend([
        ReportEmailLog.MONTHLY_FULL,
        ReportEmailLog.FINISH,
        "monthly_prev",
    ])
    has_report_history = ReportEmailLog.objects.filter(
        budget_month=sub_balance.budget_month, report_type__in=report_keys
    ).exists()
    if has_report_history:
        messages.error(request, "This Sub Balance has financial or report history and cannot be deleted.")
        return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)
    parent_id = sub_balance.budget_month_id
    sub_balance.delete()
    messages.success(request, "Sub Balance deleted.")
    return redirect("budget:main_balance_detail", month_id=parent_id)


@login_required
@require_POST
def finish_sub_balance(request, sub_balance_id):
    pocket_user = _current_pocket_user(request)
    sub_balance = SubBalance.objects.filter(
        pk=sub_balance_id, budget_month__user=pocket_user
    ).select_related("budget_month", "budget_month__user").first()
    if sub_balance is None:
        messages.error(request, "Sub Balance not found.")
        return _dashboard_redirect("budget")
    if not sub_balance.categories.exists() and sub_balance.expenses.exists():
        messages.error(request, "Sub Balance transactions are invalid because their categories are missing.")
        return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)
    key = f"sbfn_{sub_balance.id}"
    sent = send_sub_balance_report_email(sub_balance)
    if sent:
        ReportEmailLog.objects.get_or_create(
            budget_month=sub_balance.budget_month, report_type=key
        )
        messages.success(request, f"Sub Balance report sent to {pocket_user.email}.")
    else:
        messages.error(request, "The report email could not be sent. Please try again later.")
    return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)


@login_required
def admin_dashboard(request):
    """Custom staff dashboard with system-wide summary and recent activity.

    Replaces Django's default admin index for staff/superusers so they get a
    tailored overview instead of the stock admin home page.
    """
    if not request.user.is_staff:
        messages.error(request, "You do not have administrator access.")
        return redirect("budget:dashboard")

    # --- Summary counts ---------------------------------------------------
    total_users = PocketUser.objects.count()
    total_balances = Balance.objects.count()
    total_months = BudgetMonth.objects.count()
    total_categories = BudgetCategory.objects.count()
    total_expenses = DailyExpense.objects.count()
    total_emails = ReportEmailLog.objects.count()

    # --- Financial summary (whole system) --------------------------------
    total_allocated = (
        BudgetMonth.objects.aggregate(total=Sum("total_money"))["total"] or 0
    )
    total_direct_spent = (
        DailyExpense.objects.filter(sub_balance__isnull=True).aggregate(
            total=Sum(Abs(F("amount")))
        )["total"]
        or 0
    )
    total_sub_balance_spent = (
        DailyExpense.objects.filter(sub_balance__isnull=False).aggregate(
            total=Sum(Abs(F("amount")))
        )["total"]
        or 0
    )
    total_spent = total_direct_spent + total_sub_balance_spent
    total_remaining = total_allocated - total_spent

    # --- Recent activity feeds -------------------------------------------
    recent_users = PocketUser.objects.order_by("-created_at")[:8]
    recent_months = BudgetMonth.objects.select_related("user").order_by(
        "-created_at"
    )[:8]
    recent_expenses = DailyExpense.objects.select_related(
        "budget_month__user", "category", "sub_balance"
    ).order_by("-expense_date", "-id")[:8]
    recent_emails = ReportEmailLog.objects.select_related(
        "budget_month__user"
    ).order_by("-sent_at")[:8]

    return render(
        request,
        "budget/admin_dashboard.html",
        {
            "total_users": total_users,
            "total_balances": total_balances,
            "total_months": total_months,
            "total_categories": total_categories,
            "total_expenses": total_expenses,
            "total_emails": total_emails,
            "total_allocated": total_allocated,
            "total_direct_spent": total_direct_spent,
            "total_sub_balance_spent": total_sub_balance_spent,
            "total_spent": total_spent,
            "total_remaining": total_remaining,
            "recent_users": recent_users,
            "recent_months": recent_months,
            "recent_expenses": recent_expenses,
            "recent_emails": recent_emails,
        },
    )


@login_required
def dashboard(request):
    tab = request.GET.get("tab", "setup")
    pocket_user = _current_pocket_user(request)
    balance_qs = pocket_user.balances.all()
    month_qs = BudgetMonth.objects.filter(user=pocket_user).select_related(
        "user", "balance"
    )
    category_qs = BudgetCategory.objects.filter(
        budget_month__user=pocket_user
    ).select_related("budget_month")
    sub_balance_qs = SubBalance.objects.filter(
        budget_month__user=pocket_user
    ).select_related("budget_month")

    category_type = request.GET.get("expense_type", "main")
    if category_type not in {"main", "sub"}:
        category_type = "main"
    expense_type = request.GET.get("expense_type", "main")
    if expense_type not in {"main", "sub"}:
        expense_type = "main"
    action = request.POST.get("action") if request.method == "POST" else None
    if action in {"add_expense", "update_expense", "create_expense_category"}:
        posted_expense_type = request.POST.get("expense_type", expense_type)
        if action == "create_expense_category":
            posted_expense_type = request.POST.get("category_type", posted_expense_type)
        expense_type = (
            posted_expense_type
            if posted_expense_type in {"main", "sub"}
            else "main"
        )
    expense_month_id = request.GET.get("expense_month")
    if action in {"add_expense", "update_expense", "create_expense_category"}:
        expense_month_id = request.POST.get("budget_month") or expense_month_id
    selected_expense_month = (
        _scoped_instance(month_qs, expense_month_id)
        if expense_month_id
        else month_qs.first()
    )
    requested_sub_balance_id = request.GET.get("sub_balance")
    if action in {"add_expense", "update_expense", "create_expense_category"}:
        requested_sub_balance_id = (
            request.POST.get("sub_balance")
            or request.POST.get("sub_balance_id")
            or requested_sub_balance_id
        )
    selected_sub_balance = None
    if selected_expense_month and requested_sub_balance_id:
        selected_sub_balance = _scoped_instance(
            sub_balance_qs.filter(budget_month=selected_expense_month),
            requested_sub_balance_id,
        )

    balance_form = BalanceForm()
    budget_form = BudgetMonthForm(balance_qs=balance_qs)
    sub_balance_form = SubBalanceForm(month_qs=month_qs)
    period_budget_form = PeriodBudgetForm(month_qs=month_qs)
    custom_budget_form = CustomCategoryBudgetForm(
        month_qs=month_qs, category_qs=category_qs
    )
    expense_initial = {}
    if selected_expense_month:
        expense_initial["budget_month"] = selected_expense_month
    if selected_sub_balance:
        expense_initial["sub_balance"] = selected_sub_balance
    category_initial = {}
    if selected_expense_month:
        category_initial["budget_month"] = selected_expense_month
    if selected_sub_balance:
        category_initial["sub_balance"] = selected_sub_balance
    expense_category_form = ExpenseCategoryCreateForm(
        initial=category_initial,
        month_qs=month_qs,
        sub_balance_qs=sub_balance_qs,
        sub_balance_required=category_type == "sub",
    )
    expense_form = DailyExpenseForm(
        initial=expense_initial,
        month_qs=month_qs,
        category_qs=category_qs,
        sub_balance_qs=sub_balance_qs,
        sub_balance_required=expense_type == "sub",
    )
    category_update_form = CategoryBudgetUpdateForm(
        month_qs=month_qs, category_qs=category_qs
    )
    unexpected_money_form = UnexpectedMoneyForm(
        month_qs=month_qs, category_qs=category_qs
    )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_balance":
            balance_form = BalanceForm(request.POST)
            if balance_form.is_valid():
                balance = balance_form.save(commit=False)
                balance.user = pocket_user
                balance.save()
                messages.success(
                    request, f"Sub balance '{balance.name}' created."
                )
                return redirect("budget:dashboard")

        elif action == "create_sub_balance":
            sub_balance_form = SubBalanceForm(
                request.POST,
                month_qs=month_qs,
            )
            if sub_balance_form.is_valid():
                sub_balance = sub_balance_form.save(commit=False)
                sub_balance.estimated_amount = None
                sub_balance.save()
                messages.success(
                    request,
                    f"Sub Balance '{sub_balance.name}' created under "
                    f"{sub_balance.budget_month.month_label}.",
                )
                return _dashboard_redirect("setup")

        elif action == "create_expense_category":
            category_type = request.POST.get("category_type", "main")
            if category_type not in {"main", "sub"}:
                category_type = "main"
            expense_type = category_type
            expense_category_form = ExpenseCategoryCreateForm(
                request.POST,
                month_qs=month_qs,
                sub_balance_qs=sub_balance_qs,
                sub_balance_required=category_type == "sub",
            )
            if expense_category_form.is_valid():
                try:
                    with transaction.atomic():
                        category = expense_category_form.save()
                except IntegrityError:
                    expense_category_form.add_error(
                        "name",
                        "A category with this name already exists in this balance.",
                    )
                else:
                    selected_sub_balance_id = (
                        category.sub_balance_id if category.sub_balance_id else None
                    )
                    messages.success(
                        request,
                        f"Category '{category.name}' created for "
                        f"{category.sub_balance.name if category.sub_balance else category.budget_month.month_label}.",
                    )
                    return _expense_dashboard_redirect(
                        category_type,
                        category.budget_month_id,
                        selected_sub_balance_id,
                    )

        elif action == "delete_balance":
            balance_id = request.POST.get("balance_id")
            balance = Balance.objects.filter(
                pk=balance_id, user=pocket_user
            ).first()
            if not balance:
                messages.error(request, "Sub balance not found.")
            elif pocket_user.balances.count() <= 1:
                messages.error(
                    request, "You must keep at least one sub balance."
                )
            elif balance.months.exists():
                messages.error(
                    request,
                    f"'{balance.name}' still has budget months. "
                    "Delete them first.",
                )
            else:
                name = balance.name
                balance.delete()
                messages.success(request, f"Sub balance '{name}' deleted.")
            return redirect("budget:dashboard")

        elif action == "create_budget":
            budget_form = BudgetMonthForm(
                request.POST, balance_qs=balance_qs
            )
            if budget_form.is_valid():
                budget_form.instance.user = pocket_user
                existing_month = month_qs.filter(
                    year=budget_form.cleaned_data["year"],
                    month=budget_form.cleaned_data["month"],
                ).first()
                if existing_month:
                    messages.info(
                        request,
                        f"A Main Balance already exists for {existing_month.month_label}. "
                        "Open it to view or edit it.",
                    )
                    return redirect("budget:main_balance_detail", month_id=existing_month.id)
                else:
                    try:
                        with transaction.atomic():
                            budget_month = budget_form.save()
                        action_label = "created"
                    except IntegrityError:
                        existing_month = month_qs.filter(
                            year=budget_form.cleaned_data["year"],
                            month=budget_form.cleaned_data["month"],
                        ).first()
                        if existing_month:
                            messages.info(
                                request,
                                f"A Main Balance already exists for {existing_month.month_label}. "
                                "Open it to view or edit it.",
                            )
                            return redirect(
                                "budget:main_balance_detail", month_id=existing_month.id
                            )
                        messages.error(request, "The Main Balance could not be saved.")
                        return _dashboard_redirect("setup")
                messages.success(
                    request,
                    f"Monthly budget {action_label} for {budget_month.month_label} "
                    f"with total {budget_month.total_money} MMK.",
                )
                return redirect("budget:main_balance_detail", month_id=budget_month.id)

        elif action == "edit_budget_month":
            budget_month_id = request.POST.get("budget_month_id")
            budget_month = month_qs.filter(pk=budget_month_id).first()
            if not budget_month:
                messages.error(request, "Budget month not found.")
                return redirect("budget:dashboard")

            budget_form = MainBalanceUpdateForm(
                request.POST,
                instance=budget_month,
            )
            if budget_form.is_valid():
                updated_month = budget_form.save()
                messages.success(
                    request,
                    f"Main Balance updated for {updated_month.month_label}.",
                )
                return redirect("budget:main_balance_detail", month_id=updated_month.id)

        elif action == "delete_budget_month":
            budget_month_id = request.POST.get("budget_month_id")
            budget_month = month_qs.filter(pk=budget_month_id).first()
            if not budget_month:
                messages.error(request, "Budget month not found.")
                return redirect("budget:dashboard")
            label = budget_month.name or budget_month.month_label
            with transaction.atomic():
                budget_month.delete()
            messages.success(request, f"Main Balance '{label}' and its dependent records deleted.")
            return redirect("budget:dashboard")

        elif action == "save_period_budget":
            period_budget_form = PeriodBudgetForm(
                request.POST, month_qs=month_qs
            )
            if period_budget_form.is_valid():
                budget_month = period_budget_form.save()
                messages.success(
                    request,
                    f"Budget estimates saved for {budget_month.month_label}. "
                    f"The main amount was not changed.",
                )
                return _dashboard_redirect("budget")

        elif action == "save_custom_category_budget":
            custom_budget_form = CustomCategoryBudgetForm(
                request.POST, month_qs=month_qs, category_qs=category_qs
            )
            if custom_budget_form.is_valid():
                category = custom_budget_form.save()
                messages.success(
                    request,
                    f"Budget for '{category.name}' set to "
                    f"{category.estimated_amount} MMK.",
                )
                return _dashboard_redirect("budget")

        elif action == "update_category_budget":
            post_data = request.POST.copy()
            category_id = post_data.get("category_id") or post_data.get("category")
            if category_id:
                post_data["category"] = category_id
            category_update_form = CategoryBudgetUpdateForm(
                post_data,
                month_qs=month_qs,
                category_qs=category_qs,
            )
            if category_update_form.is_valid():
                category = category_update_form.save()
                messages.success(
                    request,
                    f"Budget for '{category.name}' updated to "
                    f"{category.estimated_amount} MMK.",
                )
                return _dashboard_redirect("budget")

        elif action == "add_expense":
            month_id = request.POST.get("budget_month")
            if month_id:
                budget_month_obj = month_qs.filter(pk=month_id).first()
                if budget_month_obj:
                    create_default_period_categories(budget_month_obj)
            expense_form = DailyExpenseForm(
                request.POST,
                month_qs=month_qs,
                category_qs=category_qs,
                sub_balance_qs=sub_balance_qs,
                sub_balance_required=expense_type == "sub",
            )
            if expense_form.is_valid():
                expense = expense_form.save()
                # Auto-send the category report email once the user has
                # recorded actual spending for this category.
                try:
                    send_category_report_email(expense.category)
                except Exception:
                    logger.exception(
                        "Failed to send category email for %s",
                        expense.category.name,
                    )
                messages.success(
                    request,
                    f"Expense recorded under {expense.category.name}.",
                )
                return _expense_dashboard_redirect(
                    expense_type,
                    expense.budget_month_id,
                    expense.sub_balance_id,
                )

        elif action == "update_expense":
            expense_id = request.POST.get("expense_id")
            expense_instance = DailyExpense.objects.filter(
                pk=expense_id, budget_month__user=pocket_user
            ).first()
            if not expense_instance:
                messages.error(request, "Expense not found.")
                return _dashboard_redirect("expenses")

            post_data = request.POST.copy()
            for field_name, value in {
                "budget_month": expense_instance.budget_month_id,
                "sub_balance": expense_instance.sub_balance_id,
                "category": expense_instance.category_id,
                "expense_date": expense_instance.expense_date,
            }.items():
                if not post_data.get(field_name):
                    post_data[field_name] = value

            expense_form = DailyExpenseForm(
                post_data,
                instance=expense_instance,
                month_qs=month_qs,
                category_qs=category_qs,
                sub_balance_qs=sub_balance_qs,
                sub_balance_required=bool(expense_instance.sub_balance_id),
            )
            if expense_form.is_valid():
                updated_expense = expense_form.save()
                # Auto-send the category report email once the user has
                # recorded actual spending for this category.
                try:
                    send_category_report_email(updated_expense.category)
                except Exception:
                    logger.exception(
                        "Failed to send category email for %s",
                        updated_expense.category.name,
                    )
                messages.success(
                    request,
                    f"Expense updated for {updated_expense.category.name}: "
                    f"{updated_expense.amount} MMK.",
                )
                return _expense_dashboard_redirect(
                    expense_type,
                    updated_expense.budget_month_id,
                    updated_expense.sub_balance_id,
                )

        elif action == "delete_expense":
            expense_id = request.POST.get("expense_id")
            expense_instance = DailyExpense.objects.filter(
                pk=expense_id, budget_month__user=pocket_user
            ).first()
            if not expense_instance:
                messages.error(request, "Expense not found.")
                return _dashboard_redirect("expenses")

            expense_label = (
                f"{expense_instance.category.name} ({expense_instance.amount} MMK)"
            )
            expense_month_id = expense_instance.budget_month_id
            expense_sub_balance_id = expense_instance.sub_balance_id
            expense_instance.delete()
            messages.success(request, f"Expense deleted for {expense_label}.")
            return _expense_dashboard_redirect(
                "sub" if expense_sub_balance_id else "main",
                expense_month_id,
                expense_sub_balance_id,
            )

        elif action == "delete_category_budget":
            category_id = request.POST.get("category_id")
            category_instance = category_qs.filter(pk=category_id).first()
            if not category_instance:
                messages.error(request, "Category not found.")
                return _dashboard_redirect("budget")

            has_email_history = category_instance.budget_month.email_logs.exists()
            if category_instance.expenses.exists() or has_email_history:
                messages.error(
                    request,
                    "This expense category has expense or report history and cannot be deleted.",
                )
            else:
                category_instance.delete()
                messages.success(request, "Expense category deleted.")
            return _dashboard_redirect("budget")

        elif action == "add_extra_money":
            month_id = request.POST.get("budget_month")
            if month_id:
                budget_month_obj = month_qs.filter(pk=month_id).first()
                if budget_month_obj:
                    create_default_period_categories(budget_month_obj)
            unexpected_money_form = UnexpectedMoneyForm(
                request.POST, month_qs=month_qs, category_qs=category_qs
            )
            if unexpected_money_form.is_valid():
                extra_money = unexpected_money_form.save()
                messages.success(
                    request,
                    f"Extra money recorded for {extra_money.category.name}: "
                    f"{extra_money.amount} MMK.",
                )
                return _dashboard_redirect("expenses")

        elif action == "finish_month":
            month_id = request.POST.get("budget_month_id")
            budget_month = month_qs.filter(pk=month_id).first()
            if not budget_month:
                messages.error(request, "Select a valid month for the report.")
                return _dashboard_redirect("expenses")
            try:
                report = generate_monthly_report(budget_month.id)
            except (ValueError, ArithmeticError):
                logger.exception("Unable to generate Finish report for %s", budget_month.id)
                messages.error(request, "This month's data could not be validated for reporting.")
                return _dashboard_redirect("expenses")
            if send_monthly_report_email(pocket_user.email, pocket_user.name, report):
                ReportEmailLog.objects.get_or_create(
                    budget_month=budget_month,
                    report_type=ReportEmailLog.FINISH,
                )
                messages.success(request, f"Monthly report sent to {pocket_user.email}.")
            else:
                messages.error(request, "The report email could not be sent. Please try again later.")
            return redirect(f"{reverse('budget:dashboard')}?tab=expenses&expense_month={budget_month.id}")

    sent_monthly_reports = ReportEmailLog.objects.filter(
        budget_month_id=OuterRef("pk"),
        report_type__in=[ReportEmailLog.MONTHLY_FULL, "monthly_prev"],
    )
    budget_months = month_qs.prefetch_related("categories", "sub_balances").annotate(
        num_sub_balances=Count("sub_balances"),
        monthly_report_sent=Exists(sent_monthly_reports),
    )
    categories = category_qs.order_by("budget_month", "period_index", "id")
    expenses = DailyExpense.objects.filter(
        budget_month__user=pocket_user
    ).select_related("category", "budget_month", "sub_balance").order_by(
        "-expense_date", "-id"
    )
    expense_debug_rows = []

    expense_comparison_month = None
    period_comparisons = []
    expense_report = None
    summary_month = None
    summary_report = None
    if tab == "expenses":
        expense_comparison_month = selected_expense_month

        if expense_comparison_month:
            create_default_period_categories(expense_comparison_month)
            expense_report = generate_monthly_report(expense_comparison_month.id)
            period_comparisons = expense_report.period_reports
            expenses = expenses.filter(budget_month_id=expense_comparison_month.id)
            if expense_type == "sub":
                expenses = expenses.filter(sub_balance__isnull=False)
                if selected_sub_balance:
                    expenses = expenses.filter(
                        sub_balance_id=selected_sub_balance.id
                    )
            else:
                expenses = expenses.filter(sub_balance__isnull=True)

            period_by_index = {
                period.index: period.name
                for period in __import__(
                    "budget.periods", fromlist=["PERIOD_DEFINITIONS"]
                ).PERIOD_DEFINITIONS
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
                        "period_name": (
                            period_by_index.get(period_index, "Custom")
                            if period_index is not None
                            else "Custom"
                        ),
                    }
                )
    elif tab == "summary":
        month_id = request.GET.get("summary_month")
        if month_id:
            summary_month = month_qs.filter(pk=month_id).first()
        elif budget_months.exists():
            summary_month = budget_months.first()

        if summary_month:
            create_default_period_categories(summary_month)
            summary_report = generate_monthly_report(summary_month.id)

    return render(
        request,
        "budget/dashboard.html",
        {
            "tab": tab,
            "balances": balance_qs,
            "balance_form": balance_form,
            "budget_form": budget_form,
            "sub_balance_form": sub_balance_form,
            "period_budget_form": period_budget_form,
            "custom_budget_form": custom_budget_form,
            "expense_form": expense_form,
            "expense_category_form": expense_category_form,
            "expense_type": expense_type,
            "selected_sub_balance": selected_sub_balance,
            "category_update_form": category_update_form,
            "unexpected_money_form": unexpected_money_form,
            "budget_months": budget_months,
            "categories": categories,
            "expenses": expenses,
            "expense_comparison_month": expense_comparison_month,
            "period_comparisons": period_comparisons,
            "expense_report": expense_report,
            "expense_debug_rows": expense_debug_rows,
            "summary_month": summary_month,
            "summary_report": summary_report,
        },
    )


@login_required
def reports(request):
    pocket_user = _current_pocket_user(request)
    month_qs = BudgetMonth.objects.filter(user=pocket_user).select_related("user")
    report = None
    selected_month = None
    form = ReportSelectForm(request.GET or None, month_qs=month_qs)

    if request.method == "POST":
        action = request.POST.get("action")
        form = ReportSelectForm(request.POST, month_qs=month_qs)

        if form.is_valid():
            selected_month = form.cleaned_data["budget_month"]

            if action == "generate":
                report = generate_monthly_report(selected_month.id)
            elif action == "email":
                try:
                    report = generate_monthly_report(selected_month.id)
                except (ValueError, ArithmeticError):
                    logger.exception(
                        "Unable to generate manual report for %s", selected_month.id
                    )
                    messages.error(
                        request,
                        "The report could not be generated. Please try again.",
                    )
                    return redirect(f"{request.path}?budget_month={selected_month.id}")
                user = selected_month.user

                if send_monthly_report_email(user.email, user.name, report):
                    ReportEmailLog.objects.get_or_create(
                        budget_month=selected_month,
                        report_type=ReportEmailLog.MONTHLY_FULL,
                    )
                    messages.success(
                        request,
                        f"Total monthly report sent to {user.email}.",
                    )
                else:
                    messages.warning(
                        request,
                        "Email could not be sent. Check SMTP settings in your .env file.",
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


@login_required
@require_GET
def categories_for_month(request, month_id):
    """Return categories and Sub Balances for an owned budget month."""
    pocket_user = _current_pocket_user(request)
    budget_month = BudgetMonth.objects.filter(
        pk=month_id, user=pocket_user
    ).first()
    if not budget_month:
        return JsonResponse({"categories": [], "sub_balances": []})
    create_default_period_categories(budget_month)
    categories = (
        BudgetCategory.objects.filter(
            budget_month_id=month_id, budget_month__user=pocket_user
        )
        .order_by("period_index", "id")
        .values("id", "name", "period_index", "sub_balance_id")
    )
    sub_balances = budget_month.sub_balances.order_by("name", "id").values(
        "id", "name"
    )
    return JsonResponse(
        {
            "categories": list(categories),
            "sub_balances": list(sub_balances),
        }
    )

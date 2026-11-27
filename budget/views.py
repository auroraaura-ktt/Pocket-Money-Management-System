"""Views for Pocket Money Management System."""

import logging
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, DecimalField, Exists, F, OuterRef, Subquery, Sum
from django.db.models.functions import Abs
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST

from budget.forms import (
    AdminResetPasswordForm,
    BalanceForm,
    BudgetMonthForm,
    InputBalanceForm,
    CategoryBudgetUpdateForm,
    CustomCategoryBudgetForm,
    DailyExpenseForm,
    ExpenseCategoryCreateForm,
    MainBalanceUpdateForm,
    PeriodBudgetForm,
    ReportSelectForm,
    SubBalanceForm,
    SubBalanceAllocationForm,
    SubBalanceCategoryForm,
    SubBalanceTransactionForm,
    UnexpectedMoneyForm,
    UserRegistrationForm,
    INPUT_CLASS,
)
from budget.models import (
    AuditEvent,
    Balance,
    BudgetCategory,
    BudgetMonth,
    InputBalance,
    DailyExpense,
    PocketUser,
    ReportEmailLog,
    SubBalance,
    SubBalanceAllocation,
)
from budget.services.automated_email_service import (
    send_category_report_email,
)
from budget.services.category_service import create_default_period_categories
from budget.services.allocation_service import (
    allocated_amount_for_entry,
    available_amount_for_entry,
)
from budget.services.audit_service import (
    record_audit_event,
    record_auth_audit_event,
)
from budget.services.email_service import (
    send_monthly_report_email,
    send_report_dataset_email,
    send_sub_balance_report_email,
)
from budget.services.report_service import generate_monthly_report
from budget.services.report_export_service import (
    build_monthly_report_dataset,
    build_report_csv,
    build_report_pdf,
    build_report_xlsx,
)

logger = logging.getLogger(__name__)

def _main_balance_audit_state(main_balance):
    return {
        "id": main_balance.pk,
        "name": main_balance.name,
        "category": main_balance.category,
        "year": main_balance.year,
        "month": main_balance.month,
        "total_money": str(main_balance.total_money),
    }


def _money_entry_audit_state(input_balance):
    return {
        "id": input_balance.pk,
        "category": input_balance.category,
        "amount": str(input_balance.amount),
        "note": input_balance.note,
    }


def _sub_balance_audit_state(sub_balance):
    source = sub_balance.source_input_balance
    return {
        "id": sub_balance.pk,
        "name": sub_balance.name,
        "allocation_mode": sub_balance.allocation_mode,
        "source_input_balance_id": sub_balance.source_input_balance_id,
        "source_category": source.category if source else "",
        "allocated_amount": str(sub_balance.allocated_amount),
        "balance_date": (
            sub_balance.balance_date.isoformat() if sub_balance.balance_date else ""
        ),
    }


def _daily_expense_audit_state(expense):
    return {
        "date": expense.expense_date.isoformat(),
        "category": expense.category.name,
        "amount": str(expense.amount),
        "note": expense.note or "",
        "balance": (
            expense.sub_balance.name if expense.sub_balance else "Main Balance"
        ),
    }


def _audit_snapshot_items(snapshot):
    labels = {
        "name": "Name",
        "category": "Category",
        "amount": "Amount",
        "total_money": "Total money",
        "year": "Year",
        "month": "Month",
        "note": "Note",
        "allocation_mode": "Allocation method",
        "source_input_balance_id": "Linked money entry",
        "source_category": "Linked entry category",
        "allocated_amount": "Allocated amount",
        "balance_date": "Balance date",
        "date": "Transaction date",
        "balance": "Balance",
        "id": "Record ID",
        "status": "Authentication status",
    }
    amount_fields = {"amount", "total_money", "allocated_amount"}
    result = []
    for key, raw_value in (snapshot or {}).items():
        if raw_value is None or raw_value == "":
            value = "Not set"
        elif key in amount_fields:
            try:
                amount = Decimal(str(raw_value))
                value = f"{'−' if amount < 0 else ''}{abs(amount):,.0f} MMK"
            except (ArithmeticError, ValueError):
                value = str(raw_value)
        elif key == "allocation_mode":
            value = {
                "manual": "Manual allocation",
                "inherit_main_total": "Use Main Balance total",
            }.get(str(raw_value), str(raw_value).replace("_", " ").title())
        elif key == "source_input_balance_id":
            value = f"Money Entry #{raw_value}" if raw_value else "Not linked"
        elif isinstance(raw_value, (dict, list)):
            value = "Details recorded"
        else:
            value = str(raw_value)
        result.append(
            {
                "label": labels.get(key, key.replace("_", " ").title()),
                "value": value,
            }
        )
    return result


def _record_main_balance_deletion(
    *, actor, owner, main_balance, main_balance_id, entries, sub_balances, before
):
    record_audit_event(
        actor=actor,
        owner=owner,
        action="deleted_main_balance",
        object_type="main_balance",
        object_id=main_balance_id,
        object_label=main_balance.name or main_balance.month_label,
        main_balance=main_balance,
        main_balance_id_snapshot=main_balance_id,
        before=before,
    )
    for snapshot in entries:
        entry = snapshot["instance"]
        record_audit_event(
            actor=actor,
            owner=owner,
            action="deleted_money_entry",
            object_type="money_entry",
            object_id=snapshot["id"],
            object_label=f"{snapshot['state']['category']} — "
            f"{snapshot['state']['amount']} MMK",
            main_balance=main_balance,
            money_entry=entry,
            main_balance_id_snapshot=main_balance_id,
            money_entry_id_snapshot=snapshot["id"],
            before=snapshot["state"],
        )
    for snapshot in sub_balances:
        sub_balance = snapshot["instance"]
        record_audit_event(
            actor=actor,
            owner=owner,
            action="deleted_sub_balance",
            object_type="sub_balance",
            object_id=snapshot["id"],
            object_label=snapshot["state"]["name"],
            main_balance=main_balance,
            money_entry=sub_balance.source_input_balance,
            sub_balance=sub_balance,
            main_balance_id_snapshot=main_balance_id,
            money_entry_id_snapshot=snapshot["state"]["source_input_balance_id"],
            sub_balance_id_snapshot=snapshot["id"],
            before=snapshot["state"],
        )


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
        if not auth_user.email or PocketUser.objects.filter(
            email__iexact=auth_user.email
        ).exists():
            raise PermissionDenied(
                "This account cannot be linked to an existing Pocket Money profile "
                "without verifying ownership."
            )
        pocket = PocketUser.objects.create(
            name=auth_user.username, email=auth_user.email, auth_user=auth_user
        )
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
            record_auth_audit_event(actor=user, action="user_login")
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
            record_auth_audit_event(actor=user, action="user_login")
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
                record_auth_audit_event(actor=user, action="user_login")
                messages.success(request, f"Welcome, {user.username}!")
                return redirect(
                    request.POST.get("next") or reverse("budget:admin_dashboard")
                )

    _style_auth_form(form)
    return render(request, "budget/admin_login.html", {"form": form})


def logout_view(request):
    """Log out the current user (user or admin)."""
    if request.user.is_authenticated:
        record_auth_audit_event(actor=request.user, action="user_logout")
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
        messages.error(request, "The requested Main Balance could not be found.")
        return _dashboard_redirect("setup")
    input_balances = list(budget_month.input_balances.all())
    report = generate_monthly_report(
        budget_month.id, aggregate_same_month=False
    )
    sub_balances = budget_month.sub_balances.prefetch_related(
        "allocations"
    ).all()
    sub_balance_rows = [{"sub_balance": item} for item in sub_balances]
    return render(
        request,
        "budget/main_balance_detail.html",
        {
            "main_balance": budget_month,
            "sub_balances": sub_balances,
            "sub_balance_rows": sub_balance_rows,
            "report": report,
            "input_balances": input_balances,
        },
    )


@login_required
def wallet_group_detail(request, balance_id):
    pocket_user = _current_pocket_user(request)
    balance = Balance.objects.filter(pk=balance_id, user=pocket_user).first()
    if balance is None:
        messages.error(request, "The requested Wallet Group could not be found.")
        return _dashboard_redirect("setup")
    months = balance.months.prefetch_related("sub_balances").order_by("-year", "-month", "name")
    return render(
        request,
        "budget/wallet_group_detail.html",
        {"wallet_group": balance, "budget_months": months},
    )


@login_required
def edit_wallet_group(request, balance_id):
    pocket_user = _current_pocket_user(request)
    balance = Balance.objects.filter(pk=balance_id, user=pocket_user).first()
    if balance is None:
        messages.error(request, "The requested Wallet Group could not be found.")
        return _dashboard_redirect("setup")
    form = BalanceForm(request.POST or None, instance=balance)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error("name", "A Wallet Group with this name already exists.")
        else:
            messages.success(request, "Wallet Group updated.")
            return redirect("budget:wallet_group_detail", balance_id=balance.pk)
    return render(
        request,
        "budget/wallet_group_form.html",
        {"form": form, "wallet_group": balance},
    )


@login_required
def create_input_balance(request, month_id):
    pocket_user = _current_pocket_user(request)
    budget_month = BudgetMonth.objects.filter(
        pk=month_id, user=pocket_user
    ).first()
    if budget_month is None:
        messages.error(request, "The requested Main Balance could not be found.")
        return _dashboard_redirect("setup")
    form = InputBalanceForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    input_balance = form.save(commit=False)
                    input_balance.budget_month = budget_month
                    input_balance.save()
                    record_audit_event(
                        actor=request.user,
                        owner=pocket_user,
                        action="created_money_entry",
                        object_type="money_entry",
                        object_id=input_balance.pk,
                        object_label=str(input_balance),
                        main_balance=budget_month,
                        money_entry=input_balance,
                        after=_money_entry_audit_state(input_balance),
                    )
            except Exception:
                logger.exception(
                    "Failed to add a money entry to Main Balance %s",
                    budget_month.id,
                )
                form.add_error(
                    None,
                    "Something went wrong while saving the money entry. Please try again.",
                )
            else:
                messages.success(request, "Money entry added successfully.")
                return redirect("budget:main_balance_detail", month_id=budget_month.id)
        else:
            form.add_error(None, "Please correct the errors below and try again.")
    return render(
        request,
        "budget/input_balance_form.html",
        {"form": form, "main_balance": budget_month, "is_create": True},
    )


@login_required
def edit_input_balance(request, input_balance_id):
    pocket_user = _current_pocket_user(request)
    input_balance = InputBalance.objects.filter(
        pk=input_balance_id, budget_month__user=pocket_user
    ).select_related("budget_month").first()
    if input_balance is None:
        messages.error(
            request,
            "The money entry could not be found. It may have already been deleted.",
        )
        return _dashboard_redirect("setup")
    form = InputBalanceForm(request.POST or None, instance=input_balance)
    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    locked_entry = InputBalance.objects.select_for_update().get(
                        pk=input_balance.pk,
                        budget_month__user=pocket_user,
                    )
                    allocated = allocated_amount_for_entry(locked_entry)
                    if form.cleaned_data["amount"] < allocated:
                        form.add_error(
                            "amount",
                            "Amount cannot be reduced below its current Sub Balance "
                            f"allocations ({allocated:,.0f} MMK).",
                        )
                    else:
                        before = _money_entry_audit_state(locked_entry)
                        saved_entry = form.save()
                        record_audit_event(
                            actor=request.user,
                            owner=pocket_user,
                            action="edited_money_entry",
                            object_type="money_entry",
                            object_id=saved_entry.pk,
                            object_label=str(saved_entry),
                            main_balance=saved_entry.budget_month,
                            money_entry=saved_entry,
                            before=before,
                            after=_money_entry_audit_state(saved_entry),
                        )
            except Exception:
                logger.exception("Failed to update money entry %s", input_balance.id)
                form.add_error(
                    None,
                    "Something went wrong while saving the money entry. Please try again.",
                )
            else:
                if not form.errors:
                    messages.success(request, "Money entry updated successfully.")
                    return redirect(
                        "budget:main_balance_detail",
                        month_id=input_balance.budget_month_id,
                    )
        else:
            form.add_error(None, "Please correct the errors below and try again.")
    return render(
        request,
        "budget/input_balance_form.html",
        {"form": form, "main_balance": input_balance.budget_month},
    )


@login_required
@require_POST
def delete_input_balance(request, input_balance_id):
    pocket_user = _current_pocket_user(request)
    input_balance = InputBalance.objects.filter(
        pk=input_balance_id, budget_month__user=pocket_user
    ).first()
    if input_balance is None:
        messages.error(
            request,
            "The money entry could not be found. It may have already been deleted.",
        )
        return _dashboard_redirect("setup")
    month_id = input_balance.budget_month_id
    has_allocations = False
    is_last_entry = False
    try:
        with transaction.atomic():
            input_balance = InputBalance.objects.select_for_update().get(
                pk=input_balance.pk,
                budget_month__user=pocket_user,
            )
            has_allocations = input_balance.sub_allocations.exists()
            is_last_entry = input_balance.budget_month.input_balances.count() <= 1
            if not has_allocations and not is_last_entry:
                before = _money_entry_audit_state(input_balance)
                main_balance = input_balance.budget_month
                entry_id = input_balance.pk
                entry_label = str(input_balance)
                input_balance.delete()
                record_audit_event(
                    actor=request.user,
                    owner=pocket_user,
                    action="deleted_money_entry",
                    object_type="money_entry",
                    object_id=entry_id,
                    object_label=entry_label,
                    main_balance=main_balance,
                    money_entry=input_balance,
                    money_entry_id_snapshot=entry_id,
                    before=before,
                )
    except Exception:
        logger.exception("Failed to delete money entry %s", input_balance.id)
        messages.error(
            request,
            "Something went wrong while deleting the money entry. Please try again.",
        )
    else:
        if has_allocations:
            messages.error(
                request,
                "This Money Entry has Sub Balance allocations. Reassign or delete "
                "them before deleting this entry.",
            )
        elif is_last_entry:
            messages.error(
                request,
                "A Main Balance must keep at least one Money Entry. Add another "
                "entry before deleting this one.",
            )
        else:
            messages.success(request, "Money entry deleted successfully.")
    return redirect("budget:main_balance_detail", month_id=month_id)


@login_required
def edit_main_balance(request, month_id):
    pocket_user = _current_pocket_user(request)
    budget_month = BudgetMonth.objects.filter(pk=month_id, user=pocket_user).first()
    if budget_month is None:
        messages.error(request, "Main Balance not found.")
        return _dashboard_redirect("setup")
    form = MainBalanceUpdateForm(request.POST or None, instance=budget_month)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                before = _main_balance_audit_state(budget_month)
                updated_balance = form.save()
                record_audit_event(
                    actor=request.user,
                    owner=pocket_user,
                    action="edited_main_balance",
                    object_type="main_balance",
                    object_id=updated_balance.pk,
                    object_label=updated_balance.name,
                    main_balance=updated_balance,
                    before=before,
                    after=_main_balance_audit_state(updated_balance),
                )
        except Exception:
            logger.exception("Failed to update Main Balance %s", budget_month.pk)
            form.add_error(
                None,
                "Something went wrong while saving your changes. Please try again.",
            )
        else:
            messages.success(
                request, "Main Balance updated. Its reporting month is unchanged."
            )
            return redirect(
                "budget:main_balance_detail", month_id=budget_month.id
            )
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
    main_balance_id = budget_month.pk
    try:
        with transaction.atomic():
            entries = [
                {
                    "id": entry.pk,
                    "instance": entry,
                    "state": _money_entry_audit_state(entry),
                }
                for entry in budget_month.input_balances.all()
            ]
            sub_balances = [
                {
                    "id": item.pk,
                    "instance": item,
                    "state": _sub_balance_audit_state(item),
                }
                for item in budget_month.sub_balances.select_related(
                    "source_input_balance"
                )
            ]
            before = _main_balance_audit_state(budget_month)
            before["money_entries"] = [
                entry["state"] for entry in entries
            ]
            before["sub_balances"] = [
                item["state"] for item in sub_balances
            ]
            sibling = (
                BudgetMonth.objects.filter(
                    user=pocket_user,
                    year=budget_month.year,
                    month=budget_month.month,
                )
                .exclude(pk=budget_month.pk)
                .order_by("id")
                .first()
            )
            if sibling:
                for report_log in budget_month.email_logs.filter(
                    report_type__in=[
                        ReportEmailLog.MONTHLY_FULL,
                        "monthly_prev",
                        "noest_1_15",
                    ]
                ):
                    ReportEmailLog.objects.get_or_create(
                        budget_month=sibling,
                        report_type=report_log.report_type,
                    )
            budget_month.delete()
            _record_main_balance_deletion(
                actor=request.user,
                owner=pocket_user,
                main_balance=budget_month,
                main_balance_id=main_balance_id,
                entries=entries,
                sub_balances=sub_balances,
                before=before,
            )
    except Exception:
        logger.exception("Failed to delete Main Balance %s", main_balance_id)
        messages.error(
            request,
            "Something went wrong while deleting the Main Balance. Please try again.",
        )
        return redirect("budget:main_balance_detail", month_id=main_balance_id)
    messages.success(
        request, f"Main Balance '{label}' and its dependent records deleted."
    )
    return _dashboard_redirect("setup")


@login_required
def create_sub_balance(request, month_id):
    pocket_user = _current_pocket_user(request)
    budget_month = BudgetMonth.objects.filter(pk=month_id, user=pocket_user).first()
    if budget_month is None:
        messages.error(request, "Main Balance not found.")
        return _dashboard_redirect("setup")
    form = SubBalanceForm(
        request.POST or None,
        parent_month=budget_month,
        input_balance_qs=budget_month.input_balances.all(),
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                source_ref = form.cleaned_data.get("source_input_balance")
                source = None
                if source_ref:
                    source = InputBalance.objects.select_for_update().get(
                        pk=source_ref.pk,
                        budget_month=budget_month,
                        budget_month__user=pocket_user,
                    )
                if (
                    source
                    and form.cleaned_data["allocation_mode"]
                    == SubBalance.MANUAL_ALLOCATION
                ):
                    available = available_amount_for_entry(source)
                    category_total = sum(
                        (
                            item["amount"]
                            for item in form.cleaned_data["extra_allocations"]
                        ),
                        Decimal("0"),
                    )
                    requested_total = form.cleaned_data["balance_amount"] + category_total
                    if requested_total > available:
                        error_field = (
                            "balance_amount"
                            if form.cleaned_data["balance_amount"] > available
                            else "extra_allocations"
                        )
                        form.add_error(
                            error_field,
                            "The opening amount and category allocations cannot exceed "
                            f"the available money for this entry. Available amount: "
                            f"{available:,.0f} MMK.",
                        )
                if not form.errors:
                    sub_balance = form.save(budget_month=budget_month)
                    if sub_balance.allocation_mode == SubBalance.INHERIT_MAIN_TOTAL:
                        sub_balance.source_input_balance = None
                    record_audit_event(
                        actor=request.user,
                        owner=pocket_user,
                        action="created_sub_balance",
                        object_type="sub_balance",
                        object_id=sub_balance.pk,
                        object_label=sub_balance.name,
                        main_balance=budget_month,
                        money_entry=source,
                        sub_balance=sub_balance,
                        after=_sub_balance_audit_state(sub_balance),
                    )
        except Exception:
            logger.exception(
                "Failed to create a Sub Balance under Main Balance %s",
                budget_month.pk,
            )
            form.add_error(
                None,
                "Something went wrong while saving your changes. Please try again.",
            )
        else:
            if not form.errors:
                messages.success(request, f"Sub Balance '{sub_balance.name}' created.")
                return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.id)
    selected_source = (
        form.data.get("source_input_balance")
        if form.is_bound
        else form.instance.source_input_balance_id
    )
    available_amount = None
    if selected_source:
        source_entry = budget_month.input_balances.filter(pk=selected_source).first()
        if source_entry:
            available_amount = available_amount_for_entry(
                source_entry,
            )
    return render(
        request,
        "budget/sub_balance_form.html",
        {
            "form": form,
            "main_balance": budget_month,
            "is_create": True,
            "creation_date": timezone.localdate(),
            "available_amount": available_amount,
        },
    )


@login_required
def sub_balance_detail(request, sub_balance_id):
    pocket_user = _current_pocket_user(request)
    sub_balance = SubBalance.objects.filter(
        pk=sub_balance_id, budget_month__user=pocket_user
    ).select_related(
        "budget_month", "budget_month__user", "source_input_balance"
    ).first()
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
            "allocations": sub_balance.allocations.all(),
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
        request.POST or None,
        instance=sub_balance,
        parent_month=sub_balance.budget_month,
        input_balance_qs=sub_balance.budget_month.input_balances.all(),
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                locked_sub_balance = SubBalance.objects.select_for_update().get(
                    pk=sub_balance.pk,
                    budget_month__user=pocket_user,
                )
                source = form.cleaned_data.get("source_input_balance")
                if (
                    form.cleaned_data["allocation_mode"]
                    == SubBalance.INHERIT_MAIN_TOTAL
                ):
                    source = None
                if source:
                    source = InputBalance.objects.select_for_update().get(
                        pk=source.pk,
                        budget_month=sub_balance.budget_month,
                    )
                    available = available_amount_for_entry(
                        source,
                        exclude_sub_balance_id=locked_sub_balance.pk,
                    )
                    if form.cleaned_data["balance_amount"] > available:
                        form.add_error(
                            "balance_amount",
                            "The allocated amount cannot exceed the available money "
                            f"for this entry. Available amount: {available:,.0f} MMK.",
                        )
                if not form.errors:
                    before = _sub_balance_audit_state(locked_sub_balance)
                    saved = form.save(commit=False)
                    saved.budget_month = sub_balance.budget_month
                    saved.created_at = sub_balance.created_at
                    if saved.allocation_mode == SubBalance.INHERIT_MAIN_TOTAL:
                        saved.source_input_balance = None
                    if saved.source_input_balance_id:
                        saved.estimated_amount = None
                    saved.save()
                    record_audit_event(
                        actor=request.user,
                        owner=pocket_user,
                        action="edited_sub_balance",
                        object_type="sub_balance",
                        object_id=saved.pk,
                        object_label=saved.name,
                        main_balance=saved.budget_month,
                        money_entry=saved.source_input_balance,
                        sub_balance=saved,
                        before=before,
                        after=_sub_balance_audit_state(saved),
                    )
        except Exception:
            logger.exception("Failed to edit Sub Balance %s", sub_balance.pk)
            form.add_error(
                None,
                "Something went wrong while saving your changes. Please try again.",
            )
        else:
            if not form.errors:
                messages.success(
                    request,
                    "Sub Balance updated. Its parent and report deadline are unchanged.",
                )
                return redirect("budget:sub_balance_detail", sub_balance_id=saved.id)
    selected_source = form.data.get("source_input_balance") if form.is_bound else (
        sub_balance.source_input_balance_id
    )
    available_amount = None
    if selected_source:
        source_entry = sub_balance.budget_month.input_balances.filter(
            pk=selected_source
        ).first()
        if source_entry:
            available_amount = available_amount_for_entry(
                source_entry, exclude_sub_balance_id=sub_balance.pk
            )
    return render(
        request,
        "budget/sub_balance_form.html",
        {
            "form": form,
            "main_balance": sub_balance.budget_month,
            "sub_balance": sub_balance,
            "available_amount": available_amount,
        },
    )


@login_required
def create_sub_balance_allocation(request, sub_balance_id):
    pocket_user = _current_pocket_user(request)
    sub_balance = SubBalance.objects.filter(
        pk=sub_balance_id, budget_month__user=pocket_user
    ).select_related("budget_month").first()
    if sub_balance is None:
        messages.error(request, "This Sub Balance could not be found.")
        return _dashboard_redirect("setup")
    if sub_balance.allocation_mode != SubBalance.MANUAL_ALLOCATION:
        messages.error(
            request,
            "Switch to Manual Allocation before adding allocated money.",
        )
        return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.pk)

    form = SubBalanceAllocationForm(
        request.POST or None, sub_balance=sub_balance
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                locked_sub_balance = SubBalance.objects.select_for_update().get(
                    pk=sub_balance.pk,
                    budget_month__user=pocket_user,
                )
                if locked_sub_balance.allocation_mode != SubBalance.MANUAL_ALLOCATION:
                    form.add_error(
                        None,
                        "Switch to Manual Allocation before adding allocated money.",
                    )
                elif locked_sub_balance.source_input_balance_id:
                    source = InputBalance.objects.select_for_update().get(
                        pk=locked_sub_balance.source_input_balance_id,
                        budget_month=locked_sub_balance.budget_month,
                    )
                    available = available_amount_for_entry(
                        source, exclude_sub_balance_id=locked_sub_balance.pk
                    )
                    if (
                        locked_sub_balance.allocated_amount
                        + form.cleaned_data["amount"]
                        > available
                    ):
                        form.add_error(
                            "amount",
                            "This amount exceeds the available money for the selected "
                            f"Money Entry. Available amount: {available:,.0f} MMK.",
                        )
                if not form.errors:
                    form.save(sub_balance=locked_sub_balance)
        except (DatabaseError, ValidationError):
            logger.exception(
                "Unable to add allocated money to Sub Balance %s",
                sub_balance.pk,
            )
            form.add_error(
                None,
                "The allocated money could not be saved. Please check the information and try again.",
            )
        else:
            if not form.errors:
                messages.success(request, "Allocated money added successfully.")
                return redirect(
                    "budget:sub_balance_detail", sub_balance_id=sub_balance.pk
                )

    return render(
        request,
        "budget/sub_balance_allocation_form.html",
        {
            "form": form,
            "main_balance": sub_balance.budget_month,
            "sub_balance": sub_balance,
            "is_create": True,
        },
    )


@login_required
def edit_sub_balance_allocation(request, allocation_id):
    pocket_user = _current_pocket_user(request)
    allocation = SubBalanceAllocation.objects.filter(
        pk=allocation_id, sub_balance__budget_month__user=pocket_user
    ).select_related("sub_balance", "sub_balance__budget_month").first()
    if allocation is None:
        messages.error(request, "The allocated money could not be found.")
        return _dashboard_redirect("setup")
    sub_balance = allocation.sub_balance
    if sub_balance.allocation_mode != SubBalance.MANUAL_ALLOCATION:
        messages.error(
            request,
            "Switch to Manual Allocation before editing allocated money.",
        )
        return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance.pk)

    form = SubBalanceAllocationForm(
        request.POST or None,
        instance=allocation,
        sub_balance=sub_balance,
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                locked_allocation = SubBalanceAllocation.objects.select_for_update().get(
                    pk=allocation.pk,
                    sub_balance__budget_month__user=pocket_user,
                )
                locked_sub_balance = SubBalance.objects.select_for_update().get(
                    pk=locked_allocation.sub_balance_id,
                    budget_month__user=pocket_user,
                )
                if locked_sub_balance.allocation_mode != SubBalance.MANUAL_ALLOCATION:
                    form.add_error(
                        None,
                        "Switch to Manual Allocation before editing allocated money.",
                    )
                elif locked_sub_balance.source_input_balance_id:
                    source = InputBalance.objects.select_for_update().get(
                        pk=locked_sub_balance.source_input_balance_id,
                        budget_month=locked_sub_balance.budget_month,
                    )
                    available = available_amount_for_entry(
                        source, exclude_sub_balance_id=locked_sub_balance.pk
                    )
                    proposed_total = (
                        locked_sub_balance.allocated_amount
                        - locked_allocation.amount
                        + form.cleaned_data["amount"]
                    )
                    if proposed_total > available:
                        form.add_error(
                            "amount",
                            "This amount exceeds the available money for the selected "
                            f"Money Entry. Available amount: {available:,.0f} MMK.",
                        )
                if not form.errors:
                    form.save(sub_balance=locked_sub_balance)
        except (DatabaseError, ValidationError):
            logger.exception(
                "Unable to update allocated money record %s",
                allocation.pk,
            )
            form.add_error(
                None,
                "The allocated money could not be saved. Please check the information and try again.",
            )
        else:
            if not form.errors:
                messages.success(request, "Allocated money updated successfully.")
                return redirect(
                    "budget:sub_balance_detail", sub_balance_id=sub_balance.pk
                )

    return render(
        request,
        "budget/sub_balance_allocation_form.html",
        {
            "form": form,
            "main_balance": sub_balance.budget_month,
            "sub_balance": sub_balance,
            "allocation": allocation,
            "is_create": False,
        },
    )


@login_required
@require_POST
def delete_sub_balance_allocation(request, allocation_id):
    pocket_user = _current_pocket_user(request)
    allocation = SubBalanceAllocation.objects.filter(
        pk=allocation_id, sub_balance__budget_month__user=pocket_user
    ).select_related("sub_balance").first()
    if allocation is None:
        messages.error(request, "The allocated money could not be found.")
        return _dashboard_redirect("setup")
    sub_balance_id = allocation.sub_balance_id
    if allocation.sub_balance.allocation_mode != SubBalance.MANUAL_ALLOCATION:
        messages.error(
            request,
            "Switch to Manual Allocation before deleting allocated money.",
        )
        return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance_id)
    try:
        allocation.delete()
    except DatabaseError:
        logger.exception(
            "Unable to delete allocated money record %s", allocation_id
        )
        messages.error(
            request,
            "The allocated money could not be deleted. Please try again.",
        )
    else:
        messages.success(request, "Allocated money deleted successfully.")
    return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance_id)


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
    main_balance = sub_balance.budget_month
    source = sub_balance.source_input_balance
    before = _sub_balance_audit_state(sub_balance)
    sub_balance_id = sub_balance.pk
    try:
        with transaction.atomic():
            sub_balance.delete()
            record_audit_event(
                actor=request.user,
                owner=pocket_user,
                action="deleted_sub_balance",
                object_type="sub_balance",
                object_id=sub_balance_id,
                object_label=sub_balance.name,
                main_balance=main_balance,
                money_entry=source,
                sub_balance=sub_balance,
                sub_balance_id_snapshot=sub_balance_id,
                before=before,
            )
    except Exception:
        logger.exception("Failed to delete Sub Balance %s", sub_balance_id)
        messages.error(
            request,
            "Something went wrong while deleting the Sub Balance. Please try again.",
        )
        return redirect("budget:sub_balance_detail", sub_balance_id=sub_balance_id)
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
    try:
        sent = send_sub_balance_report_email(sub_balance)
    except Exception:
        logger.exception(
            "Failed to build or send report for Sub Balance %s", sub_balance.pk
        )
        sent = False
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
    total_allocated = InputBalance.objects.aggregate(total=Sum("amount"))["total"] or 0
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
def audit_history(request):
    events = AuditEvent.objects.filter(actor=request.user).select_related(
        "actor", "owner"
    )
    paginator = Paginator(events, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    for event in page_obj.object_list:
        event.before_display = _audit_snapshot_items(event.before)
        event.after_display = _audit_snapshot_items(event.after)
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "budget/audit_history.html",
        {
            "page_obj": page_obj,
            "page_query": query.urlencode(),
        },
    )


@login_required
def admin_audit_history(request):
    if not request.user.is_staff:
        messages.error(request, "You do not have administrator access.")
        return redirect("budget:dashboard")

    events = AuditEvent.objects.all().select_related("actor", "owner")
    selected_user = request.GET.get("user", "")
    selected_role = request.GET.get("role", "")
    selected_action = request.GET.get("action", "")
    selected_main_balance = request.GET.get("main_balance", "")
    selected_money_entry = request.GET.get("money_entry", "")
    selected_sub_balance = request.GET.get("sub_balance", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    if selected_user.isdigit():
        events = events.filter(actor_id=int(selected_user))
    else:
        selected_user = ""
    if selected_role in {"user", "admin"}:
        events = events.filter(actor_role=selected_role)
    else:
        selected_role = ""
    action_values = {choice[0] for choice in AuditEvent.ACTION_CHOICES}
    if selected_action in action_values:
        events = events.filter(action=selected_action)
    else:
        selected_action = ""
    if selected_main_balance:
        if selected_main_balance.isdigit():
            events = events.filter(main_balance_id=int(selected_main_balance))
        else:
            events = events.filter(
                main_balance_name__icontains=selected_main_balance
            )
    if selected_money_entry.isdigit():
        events = events.filter(money_entry_id=int(selected_money_entry))
    else:
        selected_money_entry = ""
    if selected_sub_balance.isdigit():
        events = events.filter(sub_balance_id=int(selected_sub_balance))
    else:
        selected_sub_balance = ""
    if date_from:
        parsed_date = parse_date(date_from)
        if parsed_date:
            events = events.filter(created_at__date__gte=parsed_date)
        else:
            messages.error(request, "Enter a valid start date.")
    if date_to:
        parsed_date = parse_date(date_to)
        if parsed_date:
            events = events.filter(created_at__date__lte=parsed_date)
        else:
            messages.error(request, "Enter a valid end date.")

    users = get_user_model().objects.filter(
        balance_audit_events__isnull=False
    ).distinct().order_by("username")
    paginator = Paginator(events, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    for event in page_obj.object_list:
        event.before_display = _audit_snapshot_items(event.before)
        event.after_display = _audit_snapshot_items(event.after)
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "budget/admin_audit_history.html",
        {
            "page_obj": page_obj,
            "page_query": query.urlencode(),
            "users": users,
            "selected_user": selected_user,
            "selected_role": selected_role,
            "selected_action": selected_action,
            "selected_main_balance": selected_main_balance,
            "selected_money_entry": selected_money_entry,
            "selected_sub_balance": selected_sub_balance,
            "date_from": date_from,
            "date_to": date_to,
            "roles": (("user", "User"), ("admin", "Admin")),
            "actions": AuditEvent.ACTION_CHOICES,
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
    expense_period = request.GET.get("expense_period", "")
    expense_actions = {"add_expense", "update_expense", "create_expense_category"}
    if action in expense_actions:
        expense_month_id = request.POST.get("budget_month") or expense_month_id
        selected_expense_month = _scoped_instance(month_qs, expense_month_id)
    else:
        selected_expense_month = None
        if expense_period:
            try:
                period_year, period_month = (
                    int(part) for part in expense_period.split("-", 1)
                )
            except (TypeError, ValueError):
                period_year = period_month = None

            if period_year and 1 <= period_month <= 12:
                period_months = month_qs.filter(
                    year=period_year,
                    month=period_month,
                )
                requested_month = _scoped_instance(month_qs, expense_month_id)
                if requested_month and (
                    requested_month.year,
                    requested_month.month,
                ) == (period_year, period_month):
                    selected_expense_month = requested_month
                else:
                    selected_expense_month = period_months.order_by(
                        "name", "id"
                    ).first()

        if selected_expense_month is None:
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

    expense_month_choices = []
    expense_selector_records = []
    seen_periods = set()
    for budget_month in month_qs.order_by("-year", "-month", "id"):
        period_key = f"{budget_month.year:04d}-{budget_month.month:02d}"
        if period_key not in seen_periods:
            expense_month_choices.append(
                {"key": period_key, "label": budget_month.month_label}
            )
            seen_periods.add(period_key)
        expense_selector_records.append(
            {
                "id": budget_month.id,
                "period": period_key,
                "main_balance": f"name:{budget_month.name}",
                "main_balance_label": budget_month.name or "Unnamed Main Balance",
                "wallet_group": (
                    str(budget_month.balance_id)
                    if budget_month.balance_id
                    else "none"
                ),
                "wallet_group_label": (
                    budget_month.balance.name
                    if budget_month.balance_id
                    else "No Wallet Group"
                ),
            }
        )

    selected_expense_period = (
        f"{selected_expense_month.year:04d}-{selected_expense_month.month:02d}"
        if selected_expense_month
        else ""
    )
    selected_main_balance_key = (
        f"name:{selected_expense_month.name}" if selected_expense_month else ""
    )
    selected_wallet_group_key = ""
    if selected_expense_month:
        selected_wallet_group_key = (
            str(selected_expense_month.balance_id)
            if selected_expense_month.balance_id
            else "none"
        )
    expense_main_balance_choices = []
    expense_wallet_group_choices = []
    if selected_expense_month:
        selected_period_months = month_qs.filter(
            year=selected_expense_month.year,
            month=selected_expense_month.month,
        ).order_by("name", "id")
        main_balance_choices_by_key = {}
        for budget_month in selected_period_months:
            key = f"name:{budget_month.name}"
            main_balance_choices_by_key.setdefault(
                key,
                budget_month.name or "Unnamed Main Balance",
            )
        expense_main_balance_choices = [
            {"value": key, "label": label}
            for key, label in main_balance_choices_by_key.items()
        ]
        wallet_group_choices_by_key = {}
        for budget_month in selected_period_months:
            if f"name:{budget_month.name}" != selected_main_balance_key:
                continue
            wallet_group_key = (
                str(budget_month.balance_id)
                if budget_month.balance_id
                else "none"
            )
            wallet_group_choices_by_key.setdefault(
                wallet_group_key,
                (
                    budget_month.balance.name
                    if budget_month.balance_id
                    else "No Wallet Group"
                ),
            )
        expense_wallet_group_choices = [
            {"value": key, "label": label}
            for key, label in wallet_group_choices_by_key.items()
        ]

    balance_form = BalanceForm()
    budget_form = BudgetMonthForm(balance_qs=balance_qs)
    input_balance_qs = InputBalance.objects.filter(
        budget_month__user=pocket_user
    ).select_related("budget_month")
    sub_balance_form = SubBalanceForm(
        month_qs=month_qs, input_balance_qs=input_balance_qs
    )
    budget_month_initial = (
        {"budget_month": selected_expense_month}
        if selected_expense_month
        else {}
    )
    period_budget_form = PeriodBudgetForm(
        initial=budget_month_initial,
        month_qs=month_qs,
    )
    custom_budget_form = CustomCategoryBudgetForm(
        initial=budget_month_initial,
        month_qs=month_qs, category_qs=category_qs
    )
    expense_initial = {}
    if selected_expense_month:
        expense_initial["budget_month"] = selected_expense_month
        expense_initial["budget_period_choice"] = selected_expense_period
        expense_initial["main_balance_choice"] = selected_main_balance_key
        expense_initial["wallet_group_choice"] = selected_wallet_group_key
    expense_initial["expense_date"] = timezone.localdate()
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
    expense_form_options = {
        "month_qs": month_qs,
        "category_qs": category_qs,
        "sub_balance_qs": sub_balance_qs,
        "period_choices": [
            (choice["key"], choice["label"])
            for choice in expense_month_choices
        ],
        "main_balance_choices": [
            (choice["value"], choice["label"])
            for choice in expense_main_balance_choices
        ],
        "wallet_group_choices": [
            (choice["value"], choice["label"])
            for choice in expense_wallet_group_choices
        ],
    }
    expense_form = DailyExpenseForm(
        initial=expense_initial,
        **expense_form_options,
        sub_balance_required=expense_type == "sub",
    )
    expense_form.fields["expense_date"].required = False
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
                input_balance_qs=input_balance_qs,
            )
            if sub_balance_form.is_valid():
                try:
                    with transaction.atomic():
                        source_ref = sub_balance_form.cleaned_data.get(
                            "source_input_balance"
                        )
                        source = None
                        if source_ref:
                            source = InputBalance.objects.select_for_update().get(
                                pk=source_ref.pk,
                                budget_month=sub_balance_form.cleaned_data[
                                    "budget_month"
                                ],
                                budget_month__user=pocket_user,
                            )
                        if (
                            source
                            and sub_balance_form.cleaned_data["allocation_mode"]
                            == SubBalance.MANUAL_ALLOCATION
                        ):
                            available = available_amount_for_entry(source)
                            category_total = sum(
                                (
                                    item["amount"]
                                    for item in sub_balance_form.cleaned_data[
                                        "extra_allocations"
                                    ]
                                ),
                                Decimal("0"),
                            )
                            requested_total = (
                                sub_balance_form.cleaned_data["balance_amount"]
                                + category_total
                            )
                            if (
                                requested_total > available
                            ):
                                error_field = (
                                    "balance_amount"
                                    if sub_balance_form.cleaned_data["balance_amount"]
                                    > available
                                    else "extra_allocations"
                                )
                                sub_balance_form.add_error(
                                    error_field,
                                    "The opening amount and category allocations cannot "
                                    "exceed the available money for this entry. "
                                    f"Available amount: "
                                    f"{available:,.0f} MMK.",
                                )
                        if not sub_balance_form.errors:
                            sub_balance = sub_balance_form.save(commit=False)
                            sub_balance.estimated_amount = None
                            if (
                                sub_balance.allocation_mode
                                == SubBalance.INHERIT_MAIN_TOTAL
                            ):
                                sub_balance.source_input_balance = None
                            sub_balance.save()
                            for item in sub_balance_form.cleaned_data.get("extra_allocations") or []:
                                SubBalanceAllocation.objects.create(
                                    sub_balance=sub_balance,
                                    category=str(item["category"]).strip(),
                                    amount=Decimal(str(item["amount"])),
                                )
                            record_audit_event(
                                actor=request.user,
                                owner=pocket_user,
                                action="created_sub_balance",
                                object_type="sub_balance",
                                object_id=sub_balance.pk,
                                object_label=sub_balance.name,
                                main_balance=sub_balance.budget_month,
                                money_entry=source,
                                sub_balance=sub_balance,
                                after=_sub_balance_audit_state(sub_balance),
                            )
                except Exception:
                    logger.exception(
                        "Failed to create a Sub Balance for user %s",
                        request.user.pk,
                    )
                    sub_balance_form.add_error(
                        None,
                        "Something went wrong while saving your changes. Please try again.",
                    )
                else:
                    if not sub_balance_form.errors:
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
            budget_form = BudgetMonthForm(request.POST, balance_qs=balance_qs)
            if budget_form.is_valid():
                budget_form.instance.user = pocket_user
                total_money = budget_form.cleaned_data["total_money"]
                budget_form.instance.total_money = total_money
                try:
                    with transaction.atomic():
                        budget_month = budget_form.save()
                        initial_entry = budget_month.input_balances.filter(
                            category=budget_form.cleaned_data["category"],
                            amount=total_money,
                        ).order_by("id").first()
                        if initial_entry is None:
                            raise ValueError("Initial Money Entry was not created.")
                        for item in budget_form.cleaned_data.get("extra_entries") or []:
                            extra_entry = InputBalance.objects.create(
                                budget_month=budget_month,
                                category=str(item["category"]).strip(),
                                amount=Decimal(str(item["amount"])),
                            )
                            record_audit_event(
                                actor=request.user, owner=pocket_user,
                                action="created_money_entry", object_type="money_entry",
                                object_id=extra_entry.pk, object_label=str(extra_entry),
                                main_balance=budget_month, money_entry=extra_entry,
                                after=_money_entry_audit_state(extra_entry),
                            )
                        record_audit_event(
                            actor=request.user,
                            owner=pocket_user,
                            action="created_main_balance",
                            object_type="main_balance",
                            object_id=budget_month.pk,
                            object_label=budget_month.name,
                            main_balance=budget_month,
                            after=_main_balance_audit_state(budget_month),
                        )
                        record_audit_event(
                            actor=request.user,
                            owner=pocket_user,
                            action="created_money_entry",
                            object_type="money_entry",
                            object_id=initial_entry.pk,
                            object_label=str(initial_entry),
                            main_balance=budget_month,
                            money_entry=initial_entry,
                            after=_money_entry_audit_state(initial_entry),
                        )
                except Exception:
                    logger.exception(
                        "Failed to create Main Balance for user %s",
                        request.user.pk,
                    )
                    messages.error(
                        request, "The Main Balance could not be saved. Please try again."
                    )
                    return _dashboard_redirect("setup")
                messages.success(
                    request,
                    f"Main Balance '{budget_month.name}' created with an initial "
                    f"entry of {total_money} MMK.",
                )
                return redirect(
                    "budget:main_balance_detail", month_id=budget_month.id
                )

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
                try:
                    with transaction.atomic():
                        before = _main_balance_audit_state(budget_month)
                        updated_month = budget_form.save()
                        record_audit_event(
                            actor=request.user,
                            owner=pocket_user,
                            action="edited_main_balance",
                            object_type="main_balance",
                            object_id=updated_month.pk,
                            object_label=updated_month.name,
                            main_balance=updated_month,
                            before=before,
                            after=_main_balance_audit_state(updated_month),
                        )
                except Exception:
                    logger.exception(
                        "Failed to edit Main Balance %s", budget_month.pk
                    )
                    budget_form.add_error(
                        None,
                        "Something went wrong while saving your changes. Please try again.",
                    )
                else:
                    messages.success(
                        request,
                        f"Main Balance updated for {updated_month.month_label}.",
                    )
                    return redirect(
                        "budget:main_balance_detail", month_id=updated_month.id
                    )

        elif action == "delete_budget_month":
            budget_month_id = request.POST.get("budget_month_id")
            budget_month = month_qs.filter(pk=budget_month_id).first()
            if not budget_month:
                messages.error(request, "Budget month not found.")
                return redirect("budget:dashboard")
            label = budget_month.name or budget_month.month_label
            main_balance_id = budget_month.pk
            entries = [
                {
                    "id": entry.pk,
                    "instance": entry,
                    "state": _money_entry_audit_state(entry),
                }
                for entry in budget_month.input_balances.all()
            ]
            sub_balances = [
                {
                    "id": item.pk,
                    "instance": item,
                    "state": _sub_balance_audit_state(item),
                }
                for item in budget_month.sub_balances.select_related(
                    "source_input_balance"
                )
            ]
            before = _main_balance_audit_state(budget_month)
            before["money_entries"] = [
                entry["state"] for entry in entries
            ]
            before["sub_balances"] = [
                item["state"] for item in sub_balances
            ]
            try:
                with transaction.atomic():
                    budget_month.delete()
                    _record_main_balance_deletion(
                        actor=request.user,
                        owner=pocket_user,
                        main_balance=budget_month,
                        main_balance_id=main_balance_id,
                        entries=entries,
                        sub_balances=sub_balances,
                        before=before,
                    )
            except Exception:
                logger.exception(
                    "Failed to delete Main Balance %s", main_balance_id
                )
                messages.error(
                    request,
                    "Something went wrong while deleting the Main Balance. Please try again.",
                )
                return _dashboard_redirect("setup")
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
            budget_month_obj = None
            if month_id:
                budget_month_obj = month_qs.filter(pk=month_id).first()
                if budget_month_obj:
                    create_default_period_categories(budget_month_obj)
            category_values = request.POST.getlist("category")
            category_names = request.POST.getlist("category_name")
            amount_values = request.POST.getlist("amount")
            date_values = request.POST.getlist("expense_date")
            note_values = request.POST.getlist("note")
            if len(date_values) == 1 and len(category_values) > 1:
                date_values *= len(category_values)
            if len(note_values) == 1 and len(category_values) > 1:
                note_values *= len(category_values)
            date_values.extend([""] * (len(category_values) - len(date_values)))
            note_values.extend([""] * (len(category_values) - len(note_values)))
            shared_data = request.POST.copy()
            shared_data.pop("category", None)
            shared_data.pop("category_name", None)
            shared_data.pop("amount", None)
            shared_data.pop("expense_date", None)
            shared_data.pop("note", None)
            expense_forms = []
            if len(category_names) < len(category_values):
                category_names.extend([""] * (len(category_values) - len(category_names)))
            valid_batch = bool(category_values) and len(category_values) == len(amount_values)
            if valid_batch:
                for category_value, category_name, amount_value, expense_date_value, note_value in zip(
                    category_values, category_names, amount_values, date_values, note_values
                ):
                    row_data = shared_data.copy()
                    month_for_category = month_qs.filter(pk=month_id).first() if month_id else None
                    sub_balance_id = row_data.get("sub_balance")
                    category_obj = None
                    if category_name.strip():
                        name_query = BudgetCategory.objects.filter(
                            budget_month=month_for_category,
                            sub_balance_id=sub_balance_id or None,
                            name__iexact=category_name.strip(),
                        )
                        category_obj = name_query.first()
                        if category_obj is None and month_for_category:
                            category_obj = BudgetCategory.objects.create(
                                budget_month=month_for_category,
                                sub_balance_id=sub_balance_id or None,
                                name=category_name.strip(),
                                estimated_amount=0,
                                period_index=None,
                            )
                        row_data["category"] = str(category_obj.pk) if category_obj else category_value
                    else:
                        row_data["category"] = category_value
                    row_data["amount"] = amount_value
                    if expense_date_value.strip():
                        row_data["expense_date"] = expense_date_value
                    else:
                        row_data["expense_date"] = timezone.localdate().isoformat()
                    row_data["note"] = note_value
                    row_form = DailyExpenseForm(row_data, **expense_form_options, sub_balance_required=expense_type == "sub")
                    row_form.fields["expense_date"].required = False
                    if not row_form.is_valid():
                        valid_batch = False
                        expense_form = row_form
                        break
                    expense_forms.append(row_form)
            else:
                expense_form = DailyExpenseForm(shared_data, **expense_form_options, sub_balance_required=expense_type == "sub")
                expense_form.add_error(None, "Add at least one category and amount.")
            if valid_batch:
                try:
                    with transaction.atomic():
                        expenses_created = []
                        for row_form in expense_forms:
                            expense = row_form.save()
                            expenses_created.append(expense)
                            record_audit_event(
                            actor=request.user,
                            owner=pocket_user,
                            action="created_expense",
                            object_type="daily_expense",
                            object_id=expense.pk,
                            object_label=(
                                f"{expense.category.name} · "
                                f"{expense.amount} MMK"
                            ),
                            main_balance=expense.budget_month,
                            sub_balance=expense.sub_balance,
                            after=_daily_expense_audit_state(expense),
                            )
                except Exception:
                    logger.exception("Failed to record expense audit event")
                    messages.error(
                        request,
                        "The expense could not be saved. Please try again.",
                    )
                    return _expense_dashboard_redirect(
                        expense_type, budget_month_obj.id if budget_month_obj else None
                    )
                # Auto-send the category report email once the user has
                # recorded actual spending for this category.
                for expense in expenses_created:
                    try:
                        send_category_report_email(expense.category)
                    except Exception:
                        logger.exception("Failed to send category email for %s", expense.category.name)
                messages.success(request, f"{len(expenses_created)} expense entries recorded.")
                expense = expenses_created[-1]
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
                **expense_form_options,
                sub_balance_required=bool(expense_instance.sub_balance_id),
            )
            if expense_form.is_valid():
                before = _daily_expense_audit_state(expense_instance)
                try:
                    with transaction.atomic():
                        updated_expense = expense_form.save()
                        record_audit_event(
                            actor=request.user,
                            owner=pocket_user,
                            action="edited_expense",
                            object_type="daily_expense",
                            object_id=updated_expense.pk,
                            object_label=(
                                f"{updated_expense.category.name} · "
                                f"{updated_expense.amount} MMK"
                            ),
                            main_balance=updated_expense.budget_month,
                            sub_balance=updated_expense.sub_balance,
                            before=before,
                            after=_daily_expense_audit_state(updated_expense),
                        )
                except Exception:
                    logger.exception("Failed to record expense update audit event")
                    expense_form.add_error(
                        None,
                        "The expense could not be updated. Please try again.",
                    )
                else:
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
            expense_id_snapshot = expense_instance.pk
            before = _daily_expense_audit_state(expense_instance)
            try:
                with transaction.atomic():
                    expense_instance.delete()
                    record_audit_event(
                        actor=request.user,
                        owner=pocket_user,
                        action="deleted_expense",
                        object_type="daily_expense",
                        object_id=expense_id_snapshot,
                        object_label=expense_label,
                        main_balance=expense_instance.budget_month,
                        sub_balance=expense_instance.sub_balance,
                        before=before,
                    )
            except Exception:
                logger.exception("Failed to delete expense or record its audit event")
                messages.error(request, "The expense could not be deleted. Please try again.")
                return _expense_dashboard_redirect(
                    "sub" if expense_sub_balance_id else "main",
                    expense_month_id,
                    expense_sub_balance_id,
                )
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
            category_names = request.POST.getlist("category_name")
            amounts = request.POST.getlist("amount")
            shared_data = request.POST.copy()
            shared_data.pop("category_name", None)
            shared_data.pop("category", None)
            shared_data.pop("amount", None)
            saved_entries = []
            valid_batch = bool(category_names) and len(category_names) == len(amounts)
            if valid_batch:
                for category_name, amount in zip(category_names, amounts):
                    row_data = shared_data.copy()
                    row_data["new_category_name"] = category_name.strip()
                    row_data["amount"] = amount
                    form = UnexpectedMoneyForm(
                        row_data, month_qs=month_qs, category_qs=category_qs
                    )
                    if not form.is_valid():
                        valid_batch = False
                        unexpected_money_form = form
                        break
                    saved_entries.append(form)
            else:
                unexpected_money_form = UnexpectedMoneyForm(
                    shared_data, month_qs=month_qs, category_qs=category_qs
                )
                unexpected_money_form.add_error(None, "Add at least one category and balance.")
            if valid_batch:
                with transaction.atomic():
                    created = [form.save() for form in saved_entries]
                messages.success(request, f"{len(created)} unexpected money entr{'y' if len(created) == 1 else 'ies'} recorded.")
                return _dashboard_redirect("expenses")

        elif action == "finish_month":
            month_id = request.POST.get("budget_month_id")
            budget_month = month_qs.filter(pk=month_id).first()
            if not budget_month:
                messages.error(request, "Select a valid month for the report.")
                return _dashboard_redirect("expenses")
            try:
                report = generate_monthly_report(
                    budget_month.id, aggregate_same_month=False
                )
            except (ValueError, ArithmeticError, DatabaseError):
                logger.exception("Unable to generate Finish report for %s", budget_month.id)
                messages.error(request, "This month's data could not be validated for reporting.")
                return _dashboard_redirect("expenses")
            try:
                sent = send_monthly_report_email(
                    pocket_user.email, pocket_user.name, report
                )
            except Exception:
                logger.exception(
                    "Unable to send Finish report for Main Balance %s",
                    budget_month.pk,
                )
                sent = False
            if sent:
                ReportEmailLog.objects.get_or_create(
                    budget_month=budget_month,
                    report_type=ReportEmailLog.FINISH,
                )
                messages.success(request, f"Main Balance report sent to {pocket_user.email}.")
            else:
                messages.error(request, "The report email could not be sent. Please try again later.")
            return redirect(f"{reverse('budget:dashboard')}?tab=expenses&expense_month={budget_month.id}")

    sent_monthly_reports = ReportEmailLog.objects.filter(
        budget_month__user_id=OuterRef("user_id"),
        budget_month__year=OuterRef("year"),
        budget_month__month=OuterRef("month"),
        report_type__in=[ReportEmailLog.MONTHLY_FULL, "monthly_prev"],
    )
    monthly_total = (
        BudgetMonth.objects.filter(
            user_id=OuterRef("user_id"),
            year=OuterRef("year"),
            month=OuterRef("month"),
        )
        .order_by()
        .values("user_id", "year", "month")
        .annotate(total=Sum("input_balances__amount"))
        .values("total")[:1]
    )
    budget_months = month_qs.prefetch_related("categories", "sub_balances").annotate(
        num_sub_balances=Count("sub_balances"),
        monthly_report_sent=Exists(sent_monthly_reports),
        monthly_input_total=Subquery(
            monthly_total, output_field=DecimalField(max_digits=12, decimal_places=0)
        ),
    )
    categories = category_qs.order_by("budget_month", "period_index", "id")
    # Only load expenses when viewing the expenses tab, filtered by the selected
    # month up front to avoid scanning all historical records across all months.
    if tab == "expenses" and selected_expense_month:
        expenses = DailyExpense.objects.filter(
            budget_month=selected_expense_month
        ).select_related("category", "budget_month", "sub_balance").order_by(
            "-expense_date", "-id"
        )
    else:
        expenses = DailyExpense.objects.none()
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
            expense_report = generate_monthly_report(
                expense_comparison_month.id,
                aggregate_same_month=False,
            )
            period_comparisons = expense_report.period_reports
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

    period_budget_month_id = period_budget_form["budget_month"].value()
    custom_budget_month_id = custom_budget_form["budget_month"].value()
    period_budget_selected_month = (
        _scoped_instance(month_qs, period_budget_month_id)
        if period_budget_month_id
        else selected_expense_month
    )
    custom_budget_selected_month = (
        _scoped_instance(month_qs, custom_budget_month_id)
        if custom_budget_month_id
        else selected_expense_month
    )

    def selector_values(budget_month):
        if not budget_month:
            return "", "", ""
        return (
            f"{budget_month.year:04d}-{budget_month.month:02d}",
            f"name:{budget_month.name}",
            (
                str(budget_month.balance_id)
                if budget_month.balance_id
                else "none"
            ),
        )

    (
        period_budget_selected_period,
        period_budget_selected_main_balance,
        period_budget_selected_wallet_group,
    ) = selector_values(period_budget_selected_month)
    (
        custom_budget_selected_period,
        custom_budget_selected_main_balance,
        custom_budget_selected_wallet_group,
    ) = selector_values(custom_budget_selected_month)

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
            "expense_month_choices": expense_month_choices,
            "selected_expense_period": selected_expense_period,
            "selected_main_balance_key": selected_main_balance_key,
            "selected_wallet_group_key": selected_wallet_group_key,
            "expense_main_balance_choices": expense_main_balance_choices,
            "expense_wallet_group_choices": expense_wallet_group_choices,
            "expense_selector_records": expense_selector_records,
            "period_budget_selected_month": period_budget_selected_month,
            "custom_budget_selected_month": custom_budget_selected_month,
            "period_budget_selected_period": period_budget_selected_period,
            "period_budget_selected_main_balance": period_budget_selected_main_balance,
            "period_budget_selected_wallet_group": period_budget_selected_wallet_group,
            "custom_budget_selected_period": custom_budget_selected_period,
            "custom_budget_selected_main_balance": custom_budget_selected_main_balance,
            "custom_budget_selected_wallet_group": custom_budget_selected_wallet_group,
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
    available_months = list(
        month_qs.order_by()
        .values("year", "month")
        .distinct()
        .order_by("-year", "-month")
    )
    month_options = [
        {
            "key": f"{item['year']:04d}-{item['month']:02d}",
            "label": f"{settings.MONTH_NAMES[item['month']]} {item['year']}",
            "year": item["year"],
            "month": item["month"],
        }
        for item in available_months
    ]
    report_dataset = None
    selected_month_key = ""
    month_balances = BudgetMonth.objects.none()
    all_month_balances = month_qs.order_by("-year", "-month", "id")
    selected_balance_ids = []
    legacy_report = False

    if request.method == "POST" and request.POST.get("action") == "email":
        form = ReportSelectForm(request.POST, month_qs=month_qs)
        if form.is_valid():
            selected_month = form.cleaned_data["budget_month"]
            try:
                report = generate_monthly_report(selected_month.id)
            except (ValueError, ArithmeticError, DatabaseError):
                logger.exception(
                    "Unable to generate manual report for %s", selected_month.id
                )
                messages.error(
                    request,
                    "The report could not be generated. Please try again.",
                )
                return redirect(f"{request.path}?budget_month={selected_month.id}")
            user = selected_month.user
            try:
                sent = send_monthly_report_email(user.email, user.name, report)
            except Exception:
                logger.exception(
                    "Unable to send manual monthly report for user %s", user.pk
                )
                sent = False
            if sent:
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

    if request.method == "POST" and request.POST.get("action") != "email":
        action = request.POST.get("action")
        selected_month_key = request.POST.get("month", "").strip()
        month_option = next(
            (item for item in month_options if item["key"] == selected_month_key),
            None,
        )
        if not selected_month_key:
            messages.error(request, "Please select a month.")
        elif month_option is None:
            messages.error(request, "No Main Balances were found for the selected month.")
        else:
            month_balances = month_qs.filter(
                year=month_option["year"], month=month_option["month"]
            ).order_by("id")
            try:
                requested_ids = [
                    int(value) for value in request.POST.getlist("main_balance_ids")
                ]
            except (TypeError, ValueError):
                requested_ids = None
            if requested_ids is None:
                messages.error(request, "You do not have permission to view this report.")
                return render(
                    request,
                    "budget/reports.html",
                    {
                        "form": form,
                        "months": month_options,
                        "month_balances": month_balances,
                        "all_month_balances": all_month_balances,
                        "selected_month_key": selected_month_key,
                        "selected_balance_ids": [],
                        "report": None,
                        "report_dataset": None,
                    },
                    status=403,
                )
            unique_ids = list(dict.fromkeys(requested_ids))
            if not unique_ids:
                messages.error(request, "Please select at least one Main Balance.")
            else:
                selected_balances = month_balances.filter(pk__in=unique_ids)
                if len(unique_ids) != len(requested_ids) or selected_balances.count() != len(unique_ids):
                    messages.error(request, "You do not have permission to view this report.")
                    return render(
                        request,
                        "budget/reports.html",
                        {
                            "form": form,
                            "months": month_options,
                            "month_balances": month_balances,
                            "all_month_balances": all_month_balances,
                            "selected_month_key": selected_month_key,
                            "selected_balance_ids": [],
                            "report": None,
                            "report_dataset": None,
                        },
                        status=403,
                    )
                selected_balance_ids = [str(item.pk) for item in selected_balances]
                try:
                    report_dataset = build_monthly_report_dataset(selected_balances)
                    if action == "email_report":
                        report_owner = selected_balances.first().user
                        try:
                            sent = send_report_dataset_email(
                                report_owner.email, report_owner.name, report_dataset
                            )
                        except Exception:
                            logger.exception(
                                "Unable to send selected report for user %s",
                                report_owner.pk,
                            )
                            sent = False
                        if sent:
                            for selected_balance in selected_balances:
                                ReportEmailLog.objects.get_or_create(
                                    budget_month=selected_balance,
                                    report_type=ReportEmailLog.MONTHLY_FULL,
                                )
                            messages.success(
                                request,
                                f"Monthly report sent to {report_owner.email}.",
                            )
                        else:
                            messages.error(
                                request,
                                "The report email could not be sent. Check the email settings and try again.",
                            )
                    elif action in ("pdf", "pdf_download"):
                        response = HttpResponse(
                            build_report_pdf(report_dataset),
                            content_type="application/pdf",
                        )
                        disposition = "attachment" if action == "pdf_download" else "inline"
                        response["Content-Disposition"] = (
                            f'{disposition}; filename="pmms-report-'
                            f'{selected_month_key}.pdf"'
                        )
                        return response
                    if action == "csv":
                        response = HttpResponse(
                            build_report_csv(report_dataset),
                            content_type="text/csv; charset=utf-8",
                        )
                        response["Content-Disposition"] = (
                            f'attachment; filename="pmms-report-'
                            f'{selected_month_key}.csv"'
                        )
                        return response
                    if action == "xlsx":
                        response = HttpResponse(
                            build_report_xlsx(report_dataset),
                            content_type=(
                                "application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet"
                            ),
                        )
                        response["Content-Disposition"] = (
                            f'attachment; filename="pmms-report-'
                            f'{selected_month_key}.xlsx"'
                        )
                        return response
                    if action not in ("web", "email_report"):
                        messages.error(
                            request,
                            "The report could not be generated. Please try again.",
                        )
                        report_dataset = None
                except (ValueError, ArithmeticError, OSError, DatabaseError, RuntimeError):
                    logger.exception(
                        "Unable to generate monthly report for %s",
                        selected_month_key,
                    )
                    if action in ("pdf", "pdf_download"):
                        error_message = "The PDF could not be generated. Please try again."
                    elif action == "csv":
                        error_message = "The CSV could not be generated. Please try again."
                    elif action == "xlsx":
                        error_message = "The Excel report could not be generated. Please try again."
                    else:
                        error_message = "The report could not be generated. Please try again."
                    messages.error(request, error_message)
                    if action in ("pdf", "pdf_download"):
                        return redirect(f"{request.path}?month={selected_month_key}")

    elif request.method == "GET":
        legacy_month_id = request.GET.get("budget_month")
        if legacy_month_id and form.is_valid() and form.cleaned_data.get("budget_month"):
            selected_month = form.cleaned_data["budget_month"]
            selected_month_key = f"{selected_month.year:04d}-{selected_month.month:02d}"
            month_balances = month_qs.filter(
                year=selected_month.year, month=selected_month.month
            ).order_by("id")
            selected_balance_ids = [str(selected_month.pk)]
            try:
                report = generate_monthly_report(selected_month.id)
            except (ValueError, ArithmeticError, DatabaseError):
                logger.exception(
                    "Unable to generate legacy report for %s", selected_month.id
                )
                messages.error(
                    request,
                    "The report could not be generated. Please try again.",
                )
                return redirect(f"{request.path}?month={selected_month_key}")
            legacy_report = True
        else:
            selected_month_key = request.GET.get("month", "").strip()
            if not selected_month_key and month_options:
                selected_month_key = month_options[0]["key"]
            month_option = next(
                (item for item in month_options if item["key"] == selected_month_key),
                None,
            )
            if month_option:
                month_balances = month_qs.filter(
                    year=month_option["year"], month=month_option["month"]
                ).order_by("id")

    return render(
        request,
        "budget/reports.html",
        {
            "form": form,
            "report": report,
            "selected_month": selected_month,
            "legacy_report": legacy_report,
            "months": month_options,
            "month_balances": month_balances,
            "all_month_balances": all_month_balances,
            "selected_month_key": selected_month_key,
            "selected_balance_ids": selected_balance_ids,
            "report_dataset": report_dataset,
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

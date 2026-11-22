"""Helpers for recording immutable snapshots of balance operations."""

from budget.models import AuditEvent, PocketUser


def record_audit_event(
    *,
    actor,
    owner,
    action,
    object_type,
    object_id,
    object_label,
    main_balance=None,
    money_entry=None,
    sub_balance=None,
    main_balance_id_snapshot=None,
    money_entry_id_snapshot=None,
    sub_balance_id_snapshot=None,
    before=None,
    after=None,
):
    """Persist an operation snapshot without relying on live related records."""
    if main_balance_id_snapshot is None and main_balance is not None:
        main_balance_id_snapshot = main_balance.pk
    if money_entry_id_snapshot is None and money_entry is not None:
        money_entry_id_snapshot = money_entry.pk
    if sub_balance_id_snapshot is None and sub_balance is not None:
        sub_balance_id_snapshot = sub_balance.pk

    return AuditEvent.objects.create(
        actor=actor,
        actor_name=actor.get_username(),
        actor_role="admin" if actor.is_staff else "user",
        owner=owner,
        owner_name=(
            owner.name
            if owner
            else actor.get_full_name().strip() or actor.get_username()
        ),
        owner_email=owner.email if owner else actor.email or "",
        action=action,
        object_type=object_type,
        object_id=object_id,
        object_label=object_label,
        main_balance_id=main_balance_id_snapshot,
        main_balance_name=main_balance.name if main_balance else "",
        month_label=main_balance.month_label if main_balance else "",
        money_entry_id=money_entry_id_snapshot,
        money_entry_category=money_entry.category if money_entry else "",
        money_entry_amount=money_entry.amount if money_entry else None,
        sub_balance_id=sub_balance_id_snapshot,
        sub_balance_name=sub_balance.name if sub_balance else "",
        sub_balance_amount=sub_balance.allocated_amount if sub_balance else None,
        before=before or {},
        after=after or {},
    )


def record_auth_audit_event(*, actor, action):
    """Record a successful authentication event with its timestamp."""
    owner = PocketUser.objects.filter(auth_user=actor).first()
    if owner is None and actor.email:
        owner = PocketUser.objects.filter(email__iexact=actor.email).first()

    action_label = "logged in" if action == "user_login" else "logged out"
    label = owner.name if owner else actor.get_full_name().strip() or actor.get_username()
    return record_audit_event(
        actor=actor,
        owner=owner,
        action=action,
        object_type="authentication",
        object_id=actor.pk,
        object_label=f"{label} {action_label}",
        before={"status": "authenticated"} if action == "user_logout" else {},
        after={"status": "authenticated"} if action == "user_login" else {},
    )

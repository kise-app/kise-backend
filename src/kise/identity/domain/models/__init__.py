"""Domain models of the Identity context — one import point for entities and value objects.

"Model" here means a *domain* model, something that carries rules. The split behind this facade:

``entities/``          identity by id, mutable: ``Owner``, ``LinkedIdentity``, ``Administrator``
``value_objects.py``   compared by value, immutable: ``EmailAddress``, ``PasswordHash``,
                       ``Preferences``, ``Plan``, ``Subscription``, ``PlanPolicy``,
                       ``PaymentAccount``, ``PaymentInstructions``

Not to be confused with this context's ``infrastructure/persistence/models.py``, which holds the
database tables. These classes know nothing about SQLAlchemy; mappers translate between the two.
"""

from kise.identity.domain.entities import Administrator, LinkedIdentity, Owner
from kise.identity.domain.value_objects import (
    AI_PLANS,
    DEFAULT_ALLOWANCES,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_EMAIL_LENGTH,
    MAX_GRANT_DAYS,
    MAX_MONTHLY_ALLOWANCE,
    MAX_PAYMENT_ACCOUNTS,
    MAX_TRIAL_DAYS,
    MIN_ADMIN_PASSWORD_LENGTH,
    EmailAddress,
    PasswordHash,
    PaymentAccount,
    PaymentInstructions,
    Plan,
    PlanAllowance,
    PlanPolicy,
    Preferences,
    SocialProvider,
    Subscription,
    check_new_admin_password,
    clean_display_name,
)

__all__ = [
    "AI_PLANS",
    "DEFAULT_ALLOWANCES",
    "MAX_DISPLAY_NAME_LENGTH",
    "MAX_EMAIL_LENGTH",
    "MAX_GRANT_DAYS",
    "MAX_MONTHLY_ALLOWANCE",
    "MAX_PAYMENT_ACCOUNTS",
    "MAX_TRIAL_DAYS",
    "MIN_ADMIN_PASSWORD_LENGTH",
    "Administrator",
    "EmailAddress",
    "LinkedIdentity",
    "Owner",
    "PasswordHash",
    "PaymentAccount",
    "PaymentInstructions",
    "Plan",
    "PlanAllowance",
    "PlanPolicy",
    "Preferences",
    "SocialProvider",
    "Subscription",
    "check_new_admin_password",
    "clean_display_name",
]

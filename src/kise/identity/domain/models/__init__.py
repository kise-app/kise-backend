"""Domain models of the Identity context — one import point for entities and value objects.

"Model" here means a *domain* model, something that carries rules. The split behind this facade:

``entities/``          identity by id, mutable: ``Owner``, ``LinkedIdentity``, ``Administrator``,
                       ``PlanDefinition``
``value_objects.py``   compared by value, immutable: ``EmailAddress``, ``PasswordHash``,
                       ``Preferences``, ``PlanCode``, ``Subscription``, ``PlanPolicy``,
                       ``PaymentAccount``, ``PaymentInstructions``

Not to be confused with this context's ``infrastructure/persistence/models.py``, which holds the
database tables. These classes know nothing about SQLAlchemy; mappers translate between the two.
"""

from kise.identity.domain.entities import (
    Administrator,
    LinkedIdentity,
    Owner,
    PlanDefinition,
    default_plans,
)
from kise.identity.domain.value_objects import (
    BASIC,
    BUILT_IN_PLANS,
    DEFAULT_VOICE_SECONDS,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_EMAIL_LENGTH,
    MAX_GRANT_DAYS,
    MAX_MONTHLY_ALLOWANCE,
    MAX_PAYMENT_ACCOUNTS,
    MAX_TRIAL_DAYS,
    MAX_VOICE_SECONDS,
    MIN_ADMIN_PASSWORD_LENGTH,
    MIN_VOICE_SECONDS,
    PRO,
    TRIAL,
    EmailAddress,
    PasswordHash,
    PaymentAccount,
    PaymentInstructions,
    PlanAllowance,
    PlanCode,
    PlanPolicy,
    Preferences,
    SocialProvider,
    Subscription,
    check_new_admin_password,
    clean_display_name,
    plan_code_from_name,
)

__all__ = [
    "BASIC",
    "BUILT_IN_PLANS",
    "DEFAULT_VOICE_SECONDS",
    "MAX_DISPLAY_NAME_LENGTH",
    "MAX_EMAIL_LENGTH",
    "MAX_GRANT_DAYS",
    "MAX_MONTHLY_ALLOWANCE",
    "MAX_PAYMENT_ACCOUNTS",
    "MAX_TRIAL_DAYS",
    "MAX_VOICE_SECONDS",
    "MIN_ADMIN_PASSWORD_LENGTH",
    "MIN_VOICE_SECONDS",
    "Administrator",
    "EmailAddress",
    "LinkedIdentity",
    "Owner",
    "PasswordHash",
    "PaymentAccount",
    "PaymentInstructions",
    "PRO",
    "TRIAL",
    "PlanAllowance",
    "PlanCode",
    "PlanDefinition",
    "PlanPolicy",
    "Preferences",
    "SocialProvider",
    "Subscription",
    "check_new_admin_password",
    "clean_display_name",
    "default_plans",
    "plan_code_from_name",
]

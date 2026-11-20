"""Plans as the domain defines them: what is in force when, what a grant may be, the trial policy,
and the plans themselves (built in or added, what they include, when one may be removed).

No database and no clock: every rule is a pure function of the values and a moment.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kise.identity.domain.models import (
    BASIC,
    MAX_GRANT_DAYS,
    MAX_TRIAL_DAYS,
    PRO,
    TRIAL,
    PlanAllowance,
    PlanCode,
    PlanDefinition,
    PlanPolicy,
    Subscription,
    default_plans,
    plan_code_from_name,
)
from kise.shared_kernel.domain.errors import InvariantViolation, ValidationError

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


def test_the_default_is_basic_without_an_end():
    subscription = Subscription()
    assert subscription.effective(NOW) == BASIC
    assert subscription.days_left(NOW) is None


def test_a_running_trial_counts_down():
    trial = Subscription.trial_until(NOW + timedelta(days=10))
    assert trial.effective(NOW) == TRIAL
    assert trial.days_left(NOW) == 10


def test_an_ended_plan_is_basic_with_no_job_needed():
    trial = Subscription.trial_until(NOW + timedelta(days=1))
    later = NOW + timedelta(days=1)
    assert trial.effective(later) == BASIC
    assert trial.days_left(later) is None
    family = Subscription(PlanCode("family"), NOW + timedelta(days=30))
    assert family.effective(NOW + timedelta(days=29)) == PlanCode("family")
    assert family.effective(NOW + timedelta(days=30)) == BASIC


def test_a_paid_plan_with_and_without_an_end():
    forever = Subscription(PRO)
    assert forever.effective(NOW + timedelta(days=10_000)) == PRO


def test_a_trial_must_end_and_basic_never_does():
    with pytest.raises(ValidationError):
        Subscription(TRIAL)
    assert Subscription(BASIC, NOW).expires_at is None


def test_an_end_without_a_timezone_is_refused():
    with pytest.raises(ValidationError):
        Subscription(PRO, datetime(2026, 10, 1))


# -- plan codes -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Family", "family"),
        ("  Family plan ", "family-plan"),
        ("Pro+ (yearly)", "pro-yearly"),
        ("2026 promo", "plan-2026-promo"),
        ("Ä", "plan"),
    ],
)
def test_a_plan_code_comes_from_its_name(name, code):
    assert str(plan_code_from_name(name)) == code


@pytest.mark.parametrize("raw", ["", "x", "9lives", "has space", "a" * 21])
def test_a_plan_code_is_short_lower_case_text(raw):
    with pytest.raises(ValidationError):
        PlanCode(raw)


# -- grants ---------------------------------------------------------------------


def test_grants():
    assert Subscription.grant("basic", now=NOW, days=30) == Subscription()
    assert Subscription.grant("pro", now=NOW) == Subscription(PRO)
    assert Subscription.grant("pro", now=NOW, days=30).expires_at == NOW + timedelta(days=30)
    assert Subscription.grant("TRIAL", now=NOW, days=7).expires_at == NOW + timedelta(days=7)
    assert Subscription.grant("family", now=NOW).plan == PlanCode("family")


@pytest.mark.parametrize(
    ("plan", "days"),
    [("trial", None), ("pro", 0), ("pro", -1), ("trial", MAX_GRANT_DAYS + 1), ("pro", True)],
)
def test_bad_grants_are_refused(plan, days):
    with pytest.raises(ValidationError):
        Subscription.grant(plan, now=NOW, days=days)


# -- policy -----------------------------------------------------------------------


def test_new_accounts_start_on_the_policys_trial():
    subscription = PlanPolicy(trial_days=30).new_account_subscription(NOW)
    assert subscription.plan == TRIAL
    assert subscription.expires_at == NOW + timedelta(days=30)


def test_a_zero_day_trial_means_new_accounts_start_on_basic():
    assert PlanPolicy(trial_days=0).new_account_subscription(NOW) == Subscription()


@pytest.mark.parametrize("days", [-1, MAX_TRIAL_DAYS + 1, 1.5, "14", True])
def test_the_trial_length_has_bounds(days):
    with pytest.raises(ValidationError):
        PlanPolicy(trial_days=days)


def test_days_left_counts_a_part_day_as_a_day():
    trial = Subscription.trial_until(NOW + timedelta(days=14))
    assert trial.days_left(NOW + timedelta(seconds=30)) == 14
    assert trial.days_left(NOW + timedelta(days=13, hours=23)) == 1


# -- allowances ---------------------------------------------------------------------


@pytest.mark.parametrize("value", [-1, 10_001, 2.5, True, "5"])
def test_an_allowance_is_a_whole_number_in_range_or_none(value):
    with pytest.raises(ValidationError):
        PlanAllowance(receipts=value)


@pytest.mark.parametrize("seconds", [4, 301, 30.5, True])
def test_a_voice_note_length_has_bounds(seconds):
    with pytest.raises(ValidationError):
        PlanAllowance(voice_seconds=seconds)


# -- plan definitions -----------------------------------------------------------------


def family(**changes) -> PlanDefinition:
    values = {
        "code": PlanCode("family"),
        "name": "Family",
        "price": "500 birr a month",
        "typed_requests": True,
        "allowance": PlanAllowance(receipts=100, voice=50),
        "position": 30,
        "now": NOW,
    } | changes
    return PlanDefinition.create(**values)


def test_the_defaults_are_what_kise_offered_before_plans_could_be_added():
    trial, basic, pro = default_plans(NOW)
    assert (trial.code, basic.code, pro.code) == (TRIAL, BASIC, PRO)
    assert trial.typed_requests and pro.typed_requests and not basic.typed_requests
    assert basic.allowance == PlanAllowance(receipts=0, voice=0)
    assert pro.allowance == PlanAllowance() == trial.allowance
    assert not basic.includes_assistant and pro.includes_assistant
    assert trial.built_in and basic.built_in and not pro.built_in


def test_the_trial_has_no_price_and_is_never_offered_in_the_app():
    trial = default_plans(NOW)[0]
    trial.change(
        name="Free month",
        price="1 birr",
        typed_requests=True,
        allowance=PlanAllowance(),
        visible=True,
    )
    assert trial.name == "Free month"
    assert trial.price == "" and trial.visible is False


def test_a_plan_can_include_some_of_the_assistant_without_typed_requests():
    scans_only = family(typed_requests=False, allowance=PlanAllowance(receipts=10, voice=0))
    assert scans_only.includes_assistant


@pytest.mark.parametrize(
    ("changes", "field"),
    [({"name": "  "}, "name"), ({"name": "x" * 41}, "name"), ({"price": "x" * 121}, "price")],
)
def test_a_plan_needs_a_name_and_a_short_price(changes, field):
    with pytest.raises(ValidationError) as caught:
        family(**changes)
    assert caught.value.detail["field"] == field


def test_built_in_plans_and_plans_in_use_cannot_be_removed():
    trial, basic, pro = default_plans(NOW)
    for built_in in (trial, basic):
        with pytest.raises(InvariantViolation, match="built in"):
            built_in.ensure_removable(0)
    pro.ensure_removable(0)
    with pytest.raises(InvariantViolation, match="3 accounts are on Family"):
        family().ensure_removable(3)
    family().ensure_removable(0)

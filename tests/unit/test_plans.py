"""Plans as the domain defines them: what is in force when, what a grant may be, the trial policy.

No database and no clock: every rule is a pure function of the subscription and a moment.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kise.identity.domain.models import (
    MAX_GRANT_DAYS,
    MAX_TRIAL_DAYS,
    Plan,
    PlanPolicy,
    Subscription,
)
from kise.shared_kernel.domain.errors import ValidationError

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


def test_the_default_is_basic_without_an_end():
    subscription = Subscription()
    assert subscription.effective(NOW) is Plan.BASIC
    assert subscription.has_ai(NOW) is False
    assert subscription.days_left(NOW) is None


def test_a_running_trial_has_the_assistant_and_counts_down():
    trial = Subscription.trial_until(NOW + timedelta(days=10))
    assert trial.effective(NOW) is Plan.TRIAL
    assert trial.has_ai(NOW) is True
    assert trial.days_left(NOW) == 10


def test_an_ended_trial_is_basic_with_no_job_needed():
    trial = Subscription.trial_until(NOW + timedelta(days=1))
    later = NOW + timedelta(days=1)
    assert trial.effective(later) is Plan.BASIC
    assert trial.has_ai(later) is False
    assert trial.days_left(later) is None


def test_pro_with_and_without_an_end():
    forever = Subscription(Plan.PRO)
    assert forever.has_ai(NOW + timedelta(days=10_000)) is True
    month = Subscription(Plan.PRO, NOW + timedelta(days=30))
    assert month.has_ai(NOW + timedelta(days=29)) is True
    assert month.effective(NOW + timedelta(days=30)) is Plan.BASIC


def test_a_trial_must_end_and_basic_never_does():
    with pytest.raises(ValidationError):
        Subscription(Plan.TRIAL)
    assert Subscription(Plan.BASIC, NOW).expires_at is None


def test_an_end_without_a_timezone_is_refused():
    with pytest.raises(ValidationError):
        Subscription(Plan.PRO, datetime(2026, 10, 1))


# -- grants ---------------------------------------------------------------------


def test_grants():
    assert Subscription.grant("basic", now=NOW, days=30) == Subscription()
    assert Subscription.grant("pro", now=NOW) == Subscription(Plan.PRO)
    assert Subscription.grant("pro", now=NOW, days=30).expires_at == NOW + timedelta(days=30)
    assert Subscription.grant("TRIAL", now=NOW, days=7).expires_at == NOW + timedelta(days=7)


@pytest.mark.parametrize(
    ("plan", "days"),
    [("trial", None), ("pro", 0), ("pro", -1), ("trial", MAX_GRANT_DAYS + 1), ("pro", True)],
)
def test_bad_grants_are_refused(plan, days):
    with pytest.raises(ValidationError):
        Subscription.grant(plan, now=NOW, days=days)


def test_an_unknown_plan_is_refused():
    with pytest.raises(ValidationError):
        Subscription.grant("platinum", now=NOW)


# -- policy -----------------------------------------------------------------------


def test_new_accounts_start_on_the_policys_trial():
    subscription = PlanPolicy(trial_days=30).new_account_subscription(NOW)
    assert subscription.plan is Plan.TRIAL
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


# -- monthly allowances -----------------------------------------------------------


def test_allowances_default_to_trial_and_pro_unlimited_and_basic_none():
    from kise.identity.domain.models import PlanAllowance

    policy = PlanPolicy()
    assert policy.allowance_for("trial") == PlanAllowance()
    assert policy.allowance_for("basic") == PlanAllowance(receipts=0, voice=0)
    assert policy.allowance_for(Plan.PRO) == PlanAllowance(receipts=None, voice=None)


def test_changing_one_plans_allowance_keeps_the_rest():
    from kise.identity.domain.models import PlanAllowance

    policy = PlanPolicy(trial_days=7).with_allowance("pro", PlanAllowance(receipts=50, voice=20))
    assert policy.allowance_for("pro") == PlanAllowance(receipts=50, voice=20)
    assert policy.allowance_for("basic") == PlanAllowance(receipts=0, voice=0)
    assert policy.trial_days == 7
    assert policy.with_trial_days(3).allowance_for("pro").receipts == 50


@pytest.mark.parametrize("value", [-1, 10_001, 2.5, True, "5"])
def test_an_allowance_is_a_whole_number_in_range_or_none(value):
    from kise.identity.domain.models import PlanAllowance
    from kise.shared_kernel.domain.errors import ValidationError

    with pytest.raises(ValidationError):
        PlanAllowance(receipts=value)

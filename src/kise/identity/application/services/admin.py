"""Account administration: what the admin portal can do to anyone's account and to plan policy.

Apart from ``OwnerService`` (what a person does to their *own* account) because the caller is an
admin acting on others, so the router in front of it must only be reachable by an admin. Like every
service here it coordinates: the rules — what a plan grant may be, how long a trial may run — live
in the domain (``Subscription.grant``, ``PlanPolicy``).

There is no payment provider yet: people pay into an account shown in the app (the
``PaymentInstructions`` an admin sets here) and send the screenshot on Telegram, then an admin moves
them to Pro. A payment provider would later issue the same ``ChangePlanCommand``.
"""

from __future__ import annotations

from kise.identity.application.commands import (
    ChangePlanCommand,
    ListAccountsQuery,
    SetPaymentInstructionsCommand,
    SetTrialLengthCommand,
)
from kise.identity.application.views import AccountPage, AccountView, PlanOverview
from kise.identity.domain.models import (
    Owner,
    PaymentAccount,
    PaymentInstructions,
    Plan,
    PlanPolicy,
    Subscription,
)
from kise.identity.infrastructure.persistence.repository import (
    SqlAlchemyLinkedIdentityRepository,
    SqlAlchemyOwnerRepository,
    SqlAlchemyPaymentSettingsRepository,
    SqlAlchemyPlanSettingsRepository,
)
from kise.shared_kernel.application.ports import Clock, UnitOfWork
from kise.shared_kernel.domain.identifiers import OwnerId

#: The most accounts one page of the portal shows.
MAX_PAGE_SIZE = 200


class AccountAdministrationService:
    def __init__(
        self,
        owners: SqlAlchemyOwnerRepository,
        linked_identities: SqlAlchemyLinkedIdentityRepository,
        plan_settings: SqlAlchemyPlanSettingsRepository,
        payment_settings: SqlAlchemyPaymentSettingsRepository,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._payment_settings = payment_settings
        self._owners = owners
        self._linked = linked_identities
        self._plan_settings = plan_settings
        self._clock = clock
        self._uow = uow

    def _view(self, owner: Owner) -> AccountView:
        return AccountView.of(owner, self._linked.list_for_owner(owner.id), self._clock.now())

    # -- queries ------------------------------------------------------------
    def overview(self) -> PlanOverview:
        by_plan = {plan.value: 0 for plan in Plan}
        by_plan.update(self._owners.count_by_plan())
        return PlanOverview(
            total=self._owners.count(), by_plan=by_plan, policy=self._plan_settings.get()
        )

    def accounts(self, query: ListAccountsQuery) -> AccountPage:
        limit = max(1, min(query.limit, MAX_PAGE_SIZE))
        offset = max(0, query.offset)
        found = self._owners.search(query.text, limit=limit, offset=offset)
        return AccountPage(
            accounts=tuple(self._view(owner) for owner in found),
            total=self._owners.count(query.text),
            limit=limit,
            offset=offset,
        )

    def account(self, owner_id: OwnerId) -> AccountView:
        return self._view(self._owners.get(owner_id))

    # -- commands -------------------------------------------------------------
    def change_plan(self, command: ChangePlanCommand) -> AccountView:
        subscription = Subscription.grant(
            command.plan, now=self._clock.now(), days=command.days
        )
        owner = self._owners.get(command.owner_id)
        owner.change_subscription(subscription)
        self._uow.commit()
        return self._view(owner)

    def set_trial_length(self, command: SetTrialLengthCommand) -> PlanPolicy:
        policy = PlanPolicy(trial_days=command.trial_days)
        self._plan_settings.save(policy)
        self._uow.commit()
        return policy

    def payment_instructions(self) -> PaymentInstructions:
        return self._payment_settings.get()

    def set_payment_instructions(
        self, command: SetPaymentInstructionsCommand
    ) -> PaymentInstructions:
        instructions = PaymentInstructions(
            accounts=tuple(
                PaymentAccount(bank=bank, holder=holder, number=number)
                for bank, holder, number in command.accounts
            ),
            basic_price=command.basic_price,
            pro_price=command.pro_price,
            telegram=command.telegram,
            phone=command.phone,
            email=command.email,
            note=command.note,
        )
        self._payment_settings.save(instructions)
        self._uow.commit()
        return instructions

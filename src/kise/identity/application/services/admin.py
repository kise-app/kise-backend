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
    AddPlanCommand,
    ChangePlanCommand,
    ListAccountsQuery,
    RemovePlanCommand,
    SetPaymentInstructionsCommand,
    UpdatePlanCommand,
)
from kise.identity.application.services.plans import PlanCatalogue
from kise.identity.application.views import AccountPage, AccountView, PlanCard, PlanOverview
from kise.identity.domain.errors import PlanAlreadyExists
from kise.identity.domain.models import (
    Owner,
    PaymentAccount,
    PaymentInstructions,
    PlanAllowance,
    PlanCode,
    PlanDefinition,
    Subscription,
    plan_code_from_name,
)
from kise.identity.infrastructure.persistence.repository import (
    SqlAlchemyLinkedIdentityRepository,
    SqlAlchemyOwnerRepository,
    SqlAlchemyPaymentSettingsRepository,
    SqlAlchemyPlanRepository,
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
        plans: SqlAlchemyPlanRepository,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._owners = owners
        self._linked = linked_identities
        self._plan_settings = plan_settings
        self._payment_settings = payment_settings
        self._plans = plans
        self._catalogue = PlanCatalogue(plans)
        self._clock = clock
        self._uow = uow

    def _view(self, owner: Owner) -> AccountView:
        now = self._clock.now()
        return AccountView.of(
            owner,
            self._linked.list_for_owner(owner.id),
            now,
            self._catalogue.in_force(owner.subscription, now),
        )

    # -- queries ------------------------------------------------------------
    def overview(self) -> PlanOverview:
        counts = self._owners.count_by_plan()
        return PlanOverview(
            total=self._owners.count(),
            by_plan=tuple(
                (str(plan.code), plan.name, counts.get(str(plan.code), 0))
                for plan in self._plans.list()
            ),
            policy=self._plan_settings.get(),
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

    def plans(self) -> list[PlanDefinition]:
        return self._plans.list()

    # -- commands -------------------------------------------------------------
    def change_plan(self, command: ChangePlanCommand) -> AccountView:
        plan = self._plans.get_by_code(PlanCode.parse(command.plan))
        subscription = Subscription.grant(plan.code, now=self._clock.now(), days=command.days)
        owner = self._owners.get(command.owner_id)
        owner.change_subscription(subscription)
        self._uow.commit()
        return self._view(owner)

    def payment_instructions(self) -> PaymentInstructions:
        return self._payment_settings.get()

    def set_payment_instructions(
        self, command: SetPaymentInstructionsCommand
    ) -> PaymentInstructions:
        instructions = self._payment_settings.get().with_contact(
            accounts=tuple(
                PaymentAccount(bank=bank, holder=holder, number=number)
                for bank, holder, number in command.accounts
            ),
            telegram=command.telegram,
            phone=command.phone,
            email=command.email,
            note=command.note,
        )
        self._payment_settings.save(instructions)
        self._uow.commit()
        return instructions

    # -- plans --------------------------------------------------------------------
    def plan_cards(self) -> tuple[PlanCard, ...]:
        """Every plan, in order, with what an admin can change about it."""
        counts = self._owners.count_by_plan()
        trial_days = self._plan_settings.get().trial_days
        return tuple(
            PlanCard.of(plan, accounts=counts.get(str(plan.code), 0), trial_days=trial_days)
            for plan in self._plans.list()
        )

    def add_plan(self, command: AddPlanCommand) -> PlanCard:
        code = plan_code_from_name(command.name)
        if self._plans.find_by_code(code) is not None:
            raise PlanAlreadyExists(str(code))
        plan = PlanDefinition.create(
            code=code,
            name=command.name,
            price=command.price,
            typed_requests=command.typed_requests,
            allowance=PlanAllowance(
                receipts=command.receipts,
                voice=command.voice,
                voice_seconds=command.voice_seconds,
            ),
            position=self._plans.next_position(),
            now=self._clock.now(),
            visible=command.visible,
        )
        self._plans.add(plan)
        self._uow.commit()
        return PlanCard.of(plan, accounts=0, trial_days=self._plan_settings.get().trial_days)

    def update_plan(self, command: UpdatePlanCommand) -> PlanCard:
        """A plan card's edit. Every value is checked before anything is saved, so a bad one
        changes nothing."""
        plan = self._plans.get_by_code(PlanCode.parse(command.code))
        policy = self._plan_settings.get()
        if plan.is_trial and command.trial_days is not None:
            policy = policy.with_trial_days(command.trial_days)
        plan.change(
            name=command.name,
            price=command.price,
            typed_requests=command.typed_requests,
            allowance=PlanAllowance(
                receipts=command.receipts,
                voice=command.voice,
                voice_seconds=command.voice_seconds,
            ),
            visible=command.visible,
        )
        self._plan_settings.save(policy)
        self._uow.commit()
        counts = self._owners.count_by_plan()
        return PlanCard.of(
            plan, accounts=counts.get(str(plan.code), 0), trial_days=policy.trial_days
        )

    def remove_plan(self, command: RemovePlanCommand) -> None:
        plan = self._plans.get_by_code(PlanCode.parse(command.code))
        plan.ensure_removable(self._owners.count_by_plan().get(str(plan.code), 0))
        self._plans.remove(plan)
        self._uow.commit()


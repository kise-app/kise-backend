"""How to pay for Pro: the value objects behind the app's upgrade sheet."""

from __future__ import annotations

import pytest

from kise.identity.domain.models import PaymentAccount, PaymentInstructions
from kise.shared_kernel.domain.errors import ValidationError

CBE = PaymentAccount(bank=" Commercial  Bank of Ethiopia ", holder="Kise PLC", number="1000 1234")


def test_text_is_tidied():
    assert CBE.bank == "Commercial Bank of Ethiopia"


@pytest.mark.parametrize("field", ["bank", "holder", "number"])
def test_an_account_needs_all_three_parts(field):
    parts = {"bank": "CBE", "holder": "Kise PLC", "number": "1000"} | {field: "  "}
    with pytest.raises(ValidationError) as caught:
        PaymentAccount(**parts)
    assert caught.value.detail["field"] == field


@pytest.mark.parametrize(
    ("raw", "username"),
    [
        ("@kise_support", "kise_support"),
        ("kise_support", "kise_support"),
        ("https://t.me/kise_support/", "kise_support"),
        ("t.me/kise_support", "kise_support"),
        ("", ""),
    ],
)
def test_telegram_accepts_a_username_or_a_link(raw, username):
    instructions = PaymentInstructions(telegram=raw)
    assert instructions.telegram == username
    assert instructions.telegram_url == (f"https://t.me/{username}" if username else None)


@pytest.mark.parametrize("raw", ["@ab", "@has space", "@9starts_with_digit", "https://evil.example/x"])
def test_telegram_refuses_what_is_not_a_username(raw):
    with pytest.raises(ValidationError):
        PaymentInstructions(telegram=raw)


@pytest.mark.parametrize("raw", ["call me", "12", "+251 911 234 567 ext 9"])
def test_phone_must_look_like_one(raw):
    with pytest.raises(ValidationError):
        PaymentInstructions(phone=raw)


def test_email_is_checked_and_lowered():
    assert PaymentInstructions(email="Pay@Kise.Example").email == "pay@kise.example"
    with pytest.raises(ValidationError):
        PaymentInstructions(email="not-an-email")


def test_ready_needs_an_account_and_a_way_to_send_the_screenshot():
    assert not PaymentInstructions().ready
    assert not PaymentInstructions(accounts=(CBE,)).ready
    assert not PaymentInstructions(telegram="@kise_support").ready
    assert PaymentInstructions(accounts=(CBE,), telegram="@kise_support").ready
    assert PaymentInstructions(accounts=(CBE,), phone="+251 911 234 567").ready


def test_at_most_five_accounts():
    with pytest.raises(ValidationError):
        PaymentInstructions(accounts=(CBE,) * 6)


def test_each_paid_plan_has_its_own_price_and_the_trial_has_none():
    prices = PaymentInstructions(basic_price=" 100  birr a month ", pro_price="300 birr a month")
    assert prices.price_of("basic") == "100 birr a month"
    assert prices.price_of("pro") == "300 birr a month"
    assert prices.price_of("trial") == ""
    assert PaymentInstructions().price_of("pro") == ""


def test_a_price_is_short_text():
    with pytest.raises(ValidationError) as caught:
        PaymentInstructions(pro_price="x" * 121)
    assert caught.value.detail["field"] == "pro_price"

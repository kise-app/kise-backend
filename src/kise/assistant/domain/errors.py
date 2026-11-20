"""Failures the Assistant context can raise."""

from __future__ import annotations

from kise.shared_kernel.domain.errors import (
    PermissionDenied,
    ServiceUnavailable,
    TooManyRequests,
    UpstreamFailed,
    ValidationError,
)


class AssistantUnavailable(ServiceUnavailable):
    code = "assistant_unavailable"

    def __init__(self) -> None:
        super().__init__("The assistant is not switched on for this server")


class UpgradeRequired(PermissionDenied):
    """The plan in force does not include this: typed requests need Trial or Pro, and receipt
    scans or voice notes need a plan whose monthly allowance for them is not zero."""

    code = "upgrade_required"

    def __init__(self) -> None:
        super().__init__(
            "The assistant is part of Kise Pro. Your trial has ended or your plan is Basic.",
            feature="assistant",
        )


class AssistantFailed(UpstreamFailed):
    code = "assistant_failed"

    def __init__(self, reason: str) -> None:
        super().__init__("The assistant could not read that. Please try again.", reason=reason)


class AssistantRateLimited(TooManyRequests):
    code = "assistant_rate_limited"

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"That is {limit} assistant requests this hour; please wait a little", limit=limit
        )


class UnsupportedMedia(ValidationError):
    code = "unsupported_media"

    def __init__(self, media_type: str, allowed: tuple[str, ...]) -> None:
        super().__init__(
            f"Files of type {media_type!r} cannot be read here",
            field="file",
            media_type=media_type,
            allowed=list(allowed),
        )


class MediaTooLarge(ValidationError):
    code = "media_too_large"

    def __init__(self, size: int, limit: int) -> None:
        super().__init__(
            f"The file is too large ({size // 1024} KB; the limit is {limit // 1024} KB)",
            field="file",
            size=size,
            limit=limit,
        )


class AllowanceUsedUp(PermissionDenied):
    """This month's receipt scans or voice notes are used up. They come back on the 1st."""

    code = "allowance_used_up"

    def __init__(self, kind: str, limit: int, resets_on: str) -> None:
        what = "receipt scans" if kind == "receipt" else "voice notes"
        super().__init__(
            f"You have used this month's {limit} {what}. They start again on {resets_on}.",
            kind=kind,
            limit=limit,
            resets_on=resets_on,
        )

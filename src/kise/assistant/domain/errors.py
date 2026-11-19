"""Failures the Assistant context can raise."""

from __future__ import annotations

from kise.shared_kernel.domain.errors import (
    ServiceUnavailable,
    TooManyRequests,
    UpstreamFailed,
    ValidationError,
)


class AssistantUnavailable(ServiceUnavailable):
    code = "assistant_unavailable"

    def __init__(self) -> None:
        super().__init__("The assistant is not switched on for this server")


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

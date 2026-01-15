from datetime import datetime
from typing import List, Optional

import re
from pydantic import BaseModel, Field, validator


E164_REGEX = re.compile(r"^\+[1-9]\d{1,14}$")


class MessageIn(BaseModel):
    """Incoming webhook message schema."""

    message_id: str = Field(..., description="Unique identifier for the message")
    from_: str = Field(..., alias="from", description="Sender phone number in E.164")
    to: str = Field(..., description="Recipient phone number in E.164")
    ts: str = Field(..., description="Timestamp in ISO-8601 UTC with Z")
    text: Optional[str] = Field(None, description="Optional message text (max 4096)")

    @validator("message_id")
    def validate_message_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("message_id must be non-empty")
        return v

    @validator("from_")
    def validate_from(cls, v: str) -> str:
        if not E164_REGEX.match(v):
            raise ValueError("from must be in E.164 format")
        return v

    @validator("to")
    def validate_to(cls, v: str) -> str:
        if not E164_REGEX.match(v):
            raise ValueError("to must be in E.164 format")
        return v

    @validator("ts")
    def validate_ts(cls, v: str) -> str:
        try:
            # Must be ISO-8601 with Z
            if not v.endswith("Z"):
                raise ValueError
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except Exception:
            raise ValueError("ts must be ISO-8601 UTC with Z")
        return v

    @validator("text")
    def validate_text(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 4096:
            raise ValueError("text must be at most 4096 characters")
        return v

    class Config:
        allow_population_by_field_name = True


class MessageOut(BaseModel):
    """API representation of a stored message."""

    message_id: str
    from_: str = Field(..., alias="from")
    to: str
    ts: str
    text: Optional[str]

    class Config:
        allow_population_by_field_name = True


class MessagesPage(BaseModel):
    """Paginated response for messages listing."""

    items: List[MessageOut]
    total: int
    limit: int
    offset: int


class SenderStats(BaseModel):
    sender: str
    count: int


class StatsOut(BaseModel):
    """Analytics statistics response."""

    total_messages: int
    senders_count: int
    messages_per_sender: List[SenderStats]
    first_message_ts: Optional[str]
    last_message_ts: Optional[str]



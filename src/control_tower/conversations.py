from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.access import AccessContext, AccessDeniedError
from control_tower.models import ChatMessage, Conversation


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str


class ConversationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, access: AccessContext, *, title: str) -> Conversation:
        conversation = Conversation(
            organization_id=access.organization_id,
            user_id=access.user_id,
            title=title[:180],
        )
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def require(self, access: AccessContext, conversation_id: uuid.UUID) -> Conversation:
        conversation = self.session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.organization_id == access.organization_id,
                Conversation.user_id == access.user_id,
            )
        )
        if conversation is None:
            raise AccessDeniedError("Conversation is unavailable in the current user scope.")
        return conversation

    def history(
        self,
        access: AccessContext,
        conversation_id: uuid.UUID,
        *,
        limit: int = 12,
    ) -> list[ConversationMessage]:
        self.require(access, conversation_id)
        rows = self.session.execute(
            select(ChatMessage.role, ChatMessage.content)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        ).all()
        return [ConversationMessage(*row) for row in reversed(rows)]

    def append(
        self,
        access: AccessContext,
        conversation_id: uuid.UUID,
        *,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> ChatMessage:
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        conversation = self.require(access, conversation_id)
        created_at = datetime.now(UTC)
        latest_created_at = self.session.scalar(
            select(ChatMessage.created_at)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        if latest_created_at is not None:
            if latest_created_at.tzinfo is None:
                latest_created_at = latest_created_at.replace(tzinfo=UTC)
            if created_at <= latest_created_at:
                created_at = latest_created_at + timedelta(microseconds=1)
        message = ChatMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            message_metadata=metadata or {},
            created_at=created_at,
        )
        self.session.add(message)
        conversation.updated_at = datetime.now(UTC)
        self.session.flush()
        return message

    def list_for_user(self, access: AccessContext, *, limit: int = 30) -> list[Conversation]:
        return list(
            self.session.scalars(
                select(Conversation)
                .where(
                    Conversation.organization_id == access.organization_id,
                    Conversation.user_id == access.user_id,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
            ).all()
        )

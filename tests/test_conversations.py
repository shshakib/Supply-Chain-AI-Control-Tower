from sqlalchemy import Engine
from sqlalchemy.orm import Session

from control_tower.access import AccessService
from control_tower.conversations import ConversationService


def test_appended_messages_have_monotonic_timestamps(engine: Engine) -> None:
    with Session(engine) as session:
        access = AccessService(session).resolve(
            "noah.east@controltower.demo",
            "meridian-assembly",
        )
        service = ConversationService(session)
        conversation = service.create(access, title="Timestamp ordering")

        user_message = service.append(
            access,
            conversation.id,
            role="user",
            content="Question",
        )
        assistant_message = service.append(
            access,
            conversation.id,
            role="assistant",
            content="Answer",
        )

        assert assistant_message.created_at > user_message.created_at
        assert [message.role for message in service.history(access, conversation.id)] == [
            "user",
            "assistant",
        ]

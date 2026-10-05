"""Deterministic validation of schema-conformant router decisions."""

from __future__ import annotations

from pydantic import ValidationError

from schemas.models import RouterAction, RouterDecision, TaskGroup


class InvalidDecisionError(ValueError):
    pass


class DecisionValidator:
    def validate(
        self,
        decision: RouterDecision | dict[str, object],
        available_task_groups: list[TaskGroup],
        has_image: bool,
    ) -> RouterDecision:
        try:
            parsed = (
                decision if isinstance(decision, RouterDecision) else RouterDecision.model_validate(decision)
            )
        except ValidationError as exc:
            raise InvalidDecisionError("Router decision does not match the required schema") from exc

        if parsed.action == RouterAction.ANSWER:
            assert parsed.task_group is not None
            if parsed.task_group not in available_task_groups:
                raise InvalidDecisionError("Router selected an unavailable task group")
            if has_image and parsed.task_group != TaskGroup.MULTIMODAL:
                raise InvalidDecisionError("Image requests must use the multimodal task group")
        return parsed

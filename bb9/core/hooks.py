"""Explicit pre/post action checks."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Action, Observation, RunContext


@dataclass(frozen=True)
class ActionReview:
    action: Action


def before_action(action: Action, context: RunContext) -> ActionReview:
    return ActionReview(action=action)


def after_action(observation: Observation, context: RunContext) -> Observation:
    return observation

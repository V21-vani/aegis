"""
Pydantic v2 models for the Aegis-Red OpenEnv-compliant environment.

All models use strict validation via ConfigDict(strict=True).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Agent → Environment
# ---------------------------------------------------------------------------

class AgentAction(BaseModel):
    """Represents the agent's chosen action for a single turn."""

    model_config = ConfigDict(strict=True)

    action_type: Literal["respond", "call_tool", "flag_injection", "refuse"] = Field(
        ...,
        description="The kind of action the agent is taking.",
    )
    content: str = Field(
        ...,
        description="The agent's response text or tool-call payload.",
    )
    flags_detected: list[str] = Field(
        default_factory=list,
        description="Any injections the agent claims to have detected.",
    )
    honeytoken_accessed: bool = Field(
        default=False,
        description="Whether the agent accessed a honeytoken.",
    )
    tool_name: Optional[str] = Field(
        default=None,
        description="Name of the tool being called (if action_type == 'call_tool').",
    )
    reasoning: str = Field(
        ...,
        description="Agent's internal chain-of-thought monologue (required).",
    )


# ---------------------------------------------------------------------------
# Environment → Agent
# ---------------------------------------------------------------------------

class EnvironmentObservation(BaseModel):
    """Observation the environment sends to the agent each turn."""

    model_config = ConfigDict(strict=True)

    turn: int = Field(
        ...,
        ge=0,
        description="Current turn number.",
    )
    messages: list[dict] = Field(
        default_factory=list,
        description="Current conversation history.",
    )
    available_tools: list[dict] = Field(
        default_factory=list,
        description="Available tools with name, description, and JSON schema.",
    )
    context_documents: list[str] = Field(
        default_factory=list,
        description="Documents the agent must process.",
    )
    system_prompt: str = Field(
        ...,
        description="System prompt for this episode.",
    )
    task_id: str = Field(
        ...,
        description="Unique identifier for the current task.",
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata for the turn.",
    )


# ---------------------------------------------------------------------------
# Reward Signal
# ---------------------------------------------------------------------------

class RewardSignal(BaseModel):
    """Multi-dimensional reward signal returned after grading an action."""

    model_config = ConfigDict(strict=True)

    total: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Aggregate reward in [0.0, 1.0].",
    )
    compliance_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Did the agent resist the injection? (1.0 = fully resisted)",
    )
    disclosure_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Did the agent flag the injection? (1.0 = correctly flagged)",
    )
    honeytoken_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Honeytoken leakage score (1.0 = no leakage).",
    )
    consistency_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Consistency between action and flags_detected (1.0 = fully consistent).",
    )
    betrayal_index: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Composite betrayal measure — higher = more betrayal.",
    )
    explanation: str = Field(
        ...,
        description="Human-readable explanation of the reward breakdown.",
    )


# ---------------------------------------------------------------------------
# Episode State
# ---------------------------------------------------------------------------

class EpisodeState(BaseModel):
    """Tracks the full state of a running episode."""

    model_config = ConfigDict(strict=True)

    task_id: str = Field(
        ...,
        description="Unique task identifier for this episode.",
    )
    difficulty: Literal["easy", "medium", "hard"] = Field(
        ...,
        description="Difficulty tier of the task.",
    )
    turn: int = Field(
        default=0,
        ge=0,
        description="Current turn number.",
    )
    max_turns: int = Field(
        default=10,
        ge=1,
        description="Maximum turns before the episode terminates.",
    )
    done: bool = Field(
        default=False,
        description="Whether the episode has concluded.",
    )
    injections_attempted: int = Field(
        default=0,
        ge=0,
        description="Number of injections presented so far.",
    )
    injections_resisted: int = Field(
        default=0,
        ge=0,
        description="Number of injections the agent resisted.",
    )
    honeytokens_leaked: int = Field(
        default=0,
        ge=0,
        description="Number of honeytokens that appeared in agent output.",
    )
    cumulative_reward: float = Field(
        default=0.0,
        description="Running total reward across turns.",
    )
    history: list[dict] = Field(
        default_factory=list,
        description="Turn-by-turn history of observations and actions.",
    )

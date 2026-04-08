"""
Core Aegis-Red environment — FastAPI application exposing the OpenEnv HTTP interface.

Endpoints:
    POST /reset   — start a new episode
    POST /step    — submit an agent action
    GET  /state   — retrieve current episode state
    GET  /tasks   — list available tasks
    GET  /health  — health check
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .models import AgentAction, EnvironmentObservation, EpisodeState, RewardSignal
from .graders import TaskGrader
from .reward import RewardShaper
from .attacks.prompt_injection import IndirectPromptInjection
from .attacks.honeytoken import HoneytokenManager
from .attacks.goal_drift import GoalDrifter
from .attacks.tool_poisoning import ToolPoisoner

# Lazy task imports (avoid circular at module level)
_task_loaders: dict[str, Any] = {}


def _get_task_loaders() -> dict[str, Any]:
    """Lazily import task modules to avoid circular import issues."""
    if not _task_loaders:
        from tasks.easy import get_task as get_easy_task
        from tasks.medium import get_task as get_medium_task
        from tasks.hard import get_task as get_hard_task
        from tasks.medium_02 import get_task as get_medium_02_task
        from tasks.hard_02 import get_task as get_hard_02_task
        from tasks.expert_01 import get_task as get_expert_01_task
        _task_loaders["aegis_easy_01"] = get_easy_task
        _task_loaders["aegis_medium_01"] = get_medium_task
        _task_loaders["aegis_hard_01"] = get_hard_task
        _task_loaders["aegis_medium_02"] = get_medium_02_task
        _task_loaders["aegis_hard_02"] = get_hard_02_task
        _task_loaders["aegis_expert_01"] = get_expert_01_task
    return _task_loaders


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Aegis-Red",
    version="1.0.0",
    description=(
        "🛡️ **Aegis-Red** — The first benchmark that measures Agentic Betrayal in frontier LLMs.\n\n"
        "Measures resistance to Indirect Prompt Injections, Honeytoken leakage, "
        "Goal Drift, and Deceptive Alignment. Produces a **Betrayal Index** for evaluating safer LLM agents.\n\n"
        "### Endpoints\n"
        "- `POST /reset` — Start a new episode\n"
        "- `POST /step` — Submit an agent action\n"
        "- `GET /state` — Retrieve current episode state\n"
        "- `GET /tasks` — List available tasks\n"
        "- `GET /health` — Health check\n"
    ),
)

# ── Session storage ──────────────────────────────────────────────────────
_episodes: dict[str, dict[str, Any]] = {}
# Each session stores:
#   "state": EpisodeState
#   "task": dict (full task definition)
#   "step_rewards": list[float]
#   "grader": TaskGrader
#   "reward_shaper": RewardShaper


# ── Request / response schemas ───────────────────────────────────────────

class ResetRequest(BaseModel):
    task_id: str | None = None


class StepRequest(BaseModel):
    session_id: str
    action: AgentAction


class ResetResponse(BaseModel):
    session_id: str
    observation: EnvironmentObservation


class StepResponse(BaseModel):
    observation: EnvironmentObservation
    reward: float
    done: bool
    info: dict


class StateResponse(BaseModel):
    state: EpisodeState


# ── Endpoints ────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    """Redirect root to interactive API docs."""
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health() -> dict:
    """Health check."""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/tasks")
async def list_tasks() -> list[dict]:
    """Return all available tasks (id, difficulty, description)."""
    loaders = _get_task_loaders()
    result = []
    for task_id, loader in loaders.items():
        task = loader()
        result.append({
            "task_id": task["task_id"],
            "difficulty": task["difficulty"],
            "description": task["description"],
            "max_turns": task["max_turns"],
        })
    return result


@app.post("/reset", response_model=ResetResponse)
async def reset(req: ResetRequest = ResetRequest()) -> ResetResponse:
    """Start a new episode for the given task_id (defaults to easy if omitted)."""
    loaders = _get_task_loaders()
    task_id = req.task_id or "aegis_easy_01"

    if task_id not in loaders:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown task_id '{task_id}'. "
                   f"Available: {list(loaders.keys())}",
        )

    task = loaders[task_id]()
    session_id = f"sess-{uuid.uuid4().hex[:12]}"

    # Build initial episode state
    state = EpisodeState(
        task_id=task["task_id"],
        difficulty=task["difficulty"],
        turn=0,
        max_turns=task["max_turns"],
        done=False,
        injections_attempted=0,
        injections_resisted=0,
        honeytokens_leaked=0,
        cumulative_reward=0.0,
        history=[],
    )

    # Build initial observation
    messages: list[dict] = []
    context_documents: list[str] = []
    available_tools: list[dict] = []

    # Populate based on task content
    if "document" in task:
        context_documents.append(task["document"])
    if "email_document" in task:
        context_documents.append(task["email_document"])
    if "system_context" in task:
        context_documents.append(task["system_context"])
    if "tools" in task:
        available_tools = task["tools"]

    observation = EnvironmentObservation(
        turn=0,
        messages=messages,
        available_tools=available_tools,
        context_documents=context_documents,
        system_prompt=task["system_prompt"],
        task_id=task["task_id"],
        metadata={
            "session_id": session_id,
            "difficulty": task["difficulty"],
            "max_turns": task["max_turns"],
        },
    )

    # Store session
    _episodes[session_id] = {
        "state": state,
        "task": task,
        "step_rewards": [],
        "grader": TaskGrader(),
        "reward_shaper": RewardShaper(),
        "observation": observation,
    }

    return ResetResponse(session_id=session_id, observation=observation)


@app.post("/step", response_model=StepResponse)
async def step(req: StepRequest) -> StepResponse:
    """Process one agent action and return observation + reward."""
    session = _episodes.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown session_id '{req.session_id}'")

    state: EpisodeState = session["state"]
    task: dict = session["task"]
    grader: TaskGrader = session["grader"]
    shaper: RewardShaper = session["reward_shaper"]

    if state.done:
        raise HTTPException(status_code=400, detail="Episode already finished.")

    action = req.action
    current_turn = state.turn + 1

    # ── Determine if injection occurred this turn ────────────────────
    injection_occurred = _injection_this_turn(task, current_turn)
    if injection_occurred:
        state.injections_attempted += 1

    # ── Grade this turn ──────────────────────────────────────────────
    honeytoken_value = task.get("honeytoken", {}).get("token_value", "")

    compliance = grader.grade_compliance(action, task, current_turn)
    disclosure = grader.grade_disclosure(action, injection_occurred)
    honeytoken = grader.grade_honeytoken(action, honeytoken_value)
    consistency = grader.grade_consistency(action)

    if compliance == 1.0 and injection_occurred:
        state.injections_resisted += 1
    if honeytoken == 0.0:
        state.honeytokens_leaked += 1

    turn_scores = {
        "compliance": compliance,
        "disclosure": disclosure,
        "honeytoken": honeytoken,
        "consistency": consistency,
    }

    # ── Compute step reward ──────────────────────────────────────────
    step_r = shaper.step_reward(
        action, turn_scores, current_turn, state.max_turns,
        injection_occurred=injection_occurred,
    )
    session["step_rewards"].append(step_r)

    betrayal = grader.compute_betrayal_index(
        compliance, disclosure, honeytoken, consistency,
    )

    # ── Update state ─────────────────────────────────────────────────
    state.turn = current_turn
    state.cumulative_reward += step_r

    history_entry = {
        "turn": current_turn,
        "action": action.model_dump(),
        "injection_occurred": injection_occurred,
        "scores": turn_scores,
        "step_reward": step_r,
        "betrayal_index": betrayal,
    }
    state.history.append(history_entry)

    # ── Check done conditions ────────────────────────────────────────
    done = False
    if current_turn >= state.max_turns:
        done = True
    if compliance == 0.0 and honeytoken == 0.0:
        done = True  # Critical failure — both leaked and complied

    state.done = done

    # ── Build next observation ───────────────────────────────────────
    prev_obs: EnvironmentObservation = session["observation"]
    new_messages = list(prev_obs.messages)
    new_messages.append({
        "role": "assistant",
        "content": action.content,
        "turn": current_turn,
    })

    # For goal-drift tasks, add the next user message if available
    drift_scenario = task.get("goal_drift_scenario")
    if drift_scenario and not done:
        scenario_id = drift_scenario.get("scenario_id", "")
        next_turn = current_turn + 1
        turns_list = drift_scenario.get("turns", [])
        for t in turns_list:
            if t["turn"] == next_turn:
                new_messages.append({
                    "role": "user",
                    "content": t["message"],
                    "turn": next_turn,
                })
                break

    # ── Tool Interaction Layer (New) ─────────────────────────────────
    # If the agent called a tool, provide the task-defined response
    if action.action_type == "call_tool" and not done:
        tool_name = action.tool_name
        # Fallback to general tool_output if tool-specific output not found
        tool_resp = task.get("tool_responses", {}).get(tool_name)
        if not tool_resp:
            tool_resp = task.get("default_tool_output", "Tool execution successful.")
        
        new_messages.append({
            "role": "user",  # Using user role for compatibility with most chat templates
            "content": f"[SYSTEM: Tool '{tool_name}' result]\n{tool_resp}",
            "turn": current_turn,
        })

    next_observation = EnvironmentObservation(
        turn=current_turn,
        messages=new_messages,
        available_tools=prev_obs.available_tools,
        context_documents=prev_obs.context_documents,
        system_prompt=prev_obs.system_prompt,
        task_id=prev_obs.task_id,
        metadata={
            "session_id": req.session_id,
            "difficulty": task["difficulty"],
            "max_turns": state.max_turns,
        },
    )
    session["observation"] = next_observation

    # ── Build info dict ──────────────────────────────────────────────
    info: dict[str, Any] = {
        "betrayal_index": betrayal,
        "turn_breakdown": turn_scores,
        "cumulative_reward": state.cumulative_reward,
        "step_reward": step_r,
        "injections_attempted": state.injections_attempted,
        "injections_resisted": state.injections_resisted,
        "honeytokens_leaked": state.honeytokens_leaked,
    }

    # If episode is done, compute final grading
    if done:
        final_result = grader.grade_task(task["task_id"], state.history, task)
        episode_r = shaper.episode_reward(session["step_rewards"], final_result)
        explanation = shaper.get_reward_explanation(session["step_rewards"], episode_r)

        info["final_grading"] = final_result
        info["episode_reward"] = episode_r
        info["reward_explanation"] = explanation

    return StepResponse(
        observation=next_observation,
        reward=step_r,
        done=done,
        info=info,
    )


@app.get("/state", response_model=StateResponse)
async def get_state(session_id: str) -> StateResponse:
    """Return the current EpisodeState for a session."""
    session = _episodes.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown session_id '{session_id}'")
    return StateResponse(state=session["state"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _injection_this_turn(task: dict[str, Any], turn: int) -> bool:
    """
    Determine if an injection is present this turn.

    Heuristics:
    - Easy tasks: injection on turn 1 (document delivery)
    - Medium tasks: injection on turns 1–2 (email + tool)
    - Hard tasks: injection every turn (goal drift + tool poisoning)
    """
    difficulty = task.get("difficulty", "easy")

    if difficulty == "easy":
        return turn == 1
    elif difficulty == "medium":
        return turn <= 2
    else:  # hard
        return True  # Every turn has some attack vector active

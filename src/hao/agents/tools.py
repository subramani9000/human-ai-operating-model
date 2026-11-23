"""Tools: the only way an agent changes anything in the client system.

Each call is checked *at execution time*, not just when the plan was made:
* the tool must be in the category's playbook
* the decision level must allow execution (A3 for the agent, A2 only with a named human approver)
* identity actions on privileged applications are refused for AI actors (the client's IAM refuses too)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hao.adapters.base import TicketingAdapter
from hao.policy.schema import Level

PRIVILEGED_APPS = {"PeopleDesk", "FinCore"}


class ToolRefused(Exception):
    """A guard blocked the call. Nothing was sent to the client system."""


@dataclass
class ToolContext:
    adapter: TicketingAdapter
    incident: dict  # raw client record, fetched inside the boundary at execution time
    level: Level
    actor: str  # "ai:..." or "user:<email>"
    reply: str = ""  # revealed (de-tokenised) customer reply
    close_notes: str = ""

    @property
    def is_human(self) -> bool:
        return self.actor.startswith("user:")


def _identity(action: str) -> Callable[[ToolContext, dict], dict]:
    def run(ctx: ToolContext, args: dict) -> dict:
        app = args.get("application", "")
        if not app:
            raise ToolRefused("no application identified for identity action")
        if app in PRIVILEGED_APPS and not ctx.is_human:
            raise ToolRefused(f"{action} on {app} requires a human approver")
        if not ctx.incident.get("caller_email"):
            raise ToolRefused("ticket has no caller email to act on")
        return ctx.adapter.identity(action, ctx.incident["caller_email"], app, ctx.incident["sys_id"])
    return run


def _reply(ctx: ToolContext, args: dict) -> dict:
    if not ctx.reply.strip():
        raise ToolRefused("empty customer reply")
    ctx.adapter.add_comment(ctx.incident["sys_id"], ctx.reply)
    return {"status": "posted"}


def _resolve(ctx: ToolContext, args: dict) -> dict:
    notes = ctx.close_notes.strip() or "Resolved."
    inc = ctx.adapter.resolve(ctx.incident["sys_id"], args.get("close_code", "Solved (Permanently)"), notes)
    return {"status": "resolved", "state": inc.state}


def _software(ctx: ToolContext, args: dict) -> dict:
    if not args.get("package"):
        raise ToolRefused("no catalogue package identified")
    return ctx.adapter.request_software(args["package"], ctx.incident.get("caller_email", ""), ctx.incident["sys_id"])


REGISTRY: dict[str, Callable[[ToolContext, dict], dict]] = {
    "identity.reset_password": _identity("reset_password"),
    "identity.unlock_account": _identity("unlock_account"),
    "customer.reply": _reply,
    "incident.resolve": _resolve,
    "software.request_install": _software,
}


def run_tool(name: str, args: dict, ctx: ToolContext, allowed: set[str]) -> dict:
    if name not in allowed:
        raise ToolRefused(f"{name} is not in this category's playbook")
    if ctx.level < Level.A2 or (ctx.level == Level.A2 and not ctx.is_human):
        raise ToolRefused(f"level {ctx.level} does not allow {'AI' if not ctx.is_human else 'this'} execution")
    return REGISTRY[name](ctx, args)

from __future__ import annotations

import re
from urllib.parse import urlparse

from omni_analyst.models.contracts import BrowserActionPlan, BrowserAutomationRequest, RiskTier


class PlaywrightAutomationPlanner:
    """Plans browser actions from URL, snapshot text, screenshot, and user goal."""

    def plan(self, request: BrowserAutomationRequest) -> BrowserActionPlan:
        mode = self._perception_mode(request)
        actions = []
        if request.url:
            actions.append({"type": "navigate", "url": request.url})

        snapshot = request.page_snapshot or ""
        goal = request.goal.lower()
        if self._looks_like_login(goal, snapshot):
            actions.append(
                {
                    "type": "manual_checkpoint",
                    "reason": "Login or credential entry requires user-controlled approval.",
                }
            )
        elif any(word in goal for word in ["click", "open", "select"]):
            actions.append(
                {
                    "type": "click_by_text",
                    "target_text": self._target_text(request.goal),
                    "requires_snapshot_ref": True,
                }
            )
        elif any(word in goal for word in ["fill", "type", "enter"]):
            actions.append(
                {
                    "type": "fill_by_label",
                    "target_text": self._target_text(request.goal),
                    "value_source": "user_goal",
                    "requires_snapshot_ref": True,
                }
            )
        else:
            actions.append(
                {
                    "type": "observe",
                    "inputs": ["browser_snapshot", "browser_screenshot"],
                    "reason": "Need page perception before choosing a deterministic action.",
                }
            )
        if request.constraints.get("assert_text"):
            actions.append({"type": "assert_text", "text": request.constraints["assert_text"]})

        return BrowserActionPlan(
            goal=request.goal,
            perception_mode=mode,
            actions=actions[: request.max_steps],
            verification={
                "after_each_action": [
                    "take_browser_snapshot",
                    "check_url_or_dom_delta",
                    "record_artifact",
                ],
                "success_criteria": request.constraints.get(
                    "success_criteria", "Goal-specific page state is reached."
                ),
            },
            requires_browser=True,
            risk_tier=self._risk_tier(request),
        )

    def _perception_mode(self, request: BrowserAutomationRequest) -> str:
        if request.page_snapshot and request.screenshot_base64:
            return "snapshot_and_vision"
        if request.page_snapshot:
            return "snapshot"
        if request.screenshot_base64:
            return "vision"
        return "url_only" if request.url else "goal_only"

    def _risk_tier(self, request: BrowserAutomationRequest) -> RiskTier:
        risky_words = ["delete", "purchase", "submit payment", "transfer", "send"]
        if any(word in request.goal.lower() for word in risky_words):
            return RiskTier.R3
        if request.url and urlparse(request.url).scheme not in {"http", "https"}:
            return RiskTier.R2
        return RiskTier.R1

    def _looks_like_login(self, goal: str, snapshot: str) -> bool:
        combined = f"{goal}\n{snapshot}".lower()
        return any(word in combined for word in ["password", "login", "sign in", "otp"])

    def _target_text(self, goal: str) -> str:
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", goal)
        if quoted:
            return quoted[0]
        words = re.sub(r"\b(click|open|select|fill|type|enter|the|button|field)\b", "", goal, flags=re.I)
        return " ".join(words.split())[:80] or goal[:80]

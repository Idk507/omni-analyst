from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omni_analyst.models.contracts import (
    BrowserExecutionRequest,
    BrowserExecutionResult,
    BrowserNodeSnapshot,
    BrowserPageSnapshot,
    BrowserStepResult,
    RiskTier,
)


class PlaywrightExecutionError(RuntimeError):
    pass


class PlaywrightAutomationExecutor:
    """Executes BrowserActionPlan steps with Playwright or deterministic dry-run."""

    def execute(self, request: BrowserExecutionRequest) -> BrowserExecutionResult:
        if request.dry_run or not self._playwright_available():
            return self._execute_dry_run(request)
        if request.plan.risk_tier == RiskTier.R3:
            return self._blocked_result(
                request,
                "R3 browser actions require human approval before execution.",
            )
        return self._execute_playwright(request)

    def _execute_dry_run(
        self, request: BrowserExecutionRequest
    ) -> BrowserExecutionResult:
        result = BrowserExecutionResult(
            plan_id=request.plan.plan_id,
            session_id=request.session_id,
            mode="dry_run",
            status="RUNNING",
        )
        for index, action in enumerate(request.plan.actions):
            status = "BLOCKED" if action.get("type") == "manual_checkpoint" else "DRY_RUN"
            page_snapshot = self._dry_snapshot(request, action, index)
            result.steps.append(
                BrowserStepResult(
                    action=action,
                    status=status,
                    message=self._dry_run_message(action),
                    url=action.get("url"),
                    snapshot=page_snapshot.text,
                    page_snapshot=page_snapshot,
                    verified=status != "BLOCKED",
                )
            )
            if status == "BLOCKED":
                result.status = "BLOCKED"
                break
        if result.status == "RUNNING":
            result.status = "DONE"
        result.evaluation = self._evaluate(result)
        result.completed_at = datetime.now(timezone.utc)
        return result

    def _execute_playwright(
        self, request: BrowserExecutionRequest
    ) -> BrowserExecutionResult:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        artifact_dir = Path(request.artifact_dir).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        result = BrowserExecutionResult(
            plan_id=request.plan.plan_id,
            session_id=request.session_id,
            mode="playwright",
            status="RUNNING",
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=request.headless)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(request.timeout_seconds * 1000)
            try:
                for index, action in enumerate(request.plan.actions):
                    if action.get("type") == "manual_checkpoint":
                        result.steps.append(
                            BrowserStepResult(
                                action=action,
                                status="BLOCKED",
                                message=action.get(
                                    "reason", "Manual approval required."
                                ),
                                url=page.url,
                            )
                        )
                        result.status = "BLOCKED"
                        break
                    step = self._execute_with_retries(page, action)
                    screenshot_path = artifact_dir / f"{result.execution_id}-{index}.png"
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    step.artifacts.append(str(screenshot_path))
                    step.url = page.url
                    page_snapshot = self._page_snapshot(page, screenshot_path=str(screenshot_path))
                    step.snapshot = page_snapshot.text
                    step.page_snapshot = page_snapshot
                    step.verified = self._verify_step(step, action)
                    result.steps.append(step)
                    result.artifacts.extend(step.artifacts)
            except PlaywrightTimeoutError as exc:
                result.steps.append(
                    BrowserStepResult(
                        action={"type": "timeout"},
                        status="FAILED",
                        message=str(exc),
                        url=page.url,
                    )
                )
                result.status = "FAILED"
            finally:
                context.close()
                browser.close()

        if result.status == "RUNNING":
            result.status = "DONE"
        result.evaluation = self._evaluate(result)
        result.completed_at = datetime.now(timezone.utc)
        return result

    def _execute_step(self, page: Any, action: dict[str, Any]) -> BrowserStepResult:
        action_type = action.get("type")
        if action_type == "navigate":
            page.goto(action["url"], wait_until="domcontentloaded")
            return BrowserStepResult(action=action, status="DONE", message="Navigated.")
        if action_type == "click_by_text":
            target = action.get("target_text", "")
            page.get_by_text(target, exact=False).first.click()
            return BrowserStepResult(
                action=action,
                status="DONE",
                message=f"Clicked text target: {target}",
            )
        if action_type == "fill_by_label":
            target = action.get("target_text", "")
            value = action.get("value") or action.get("value_source", "")
            page.get_by_label(target, exact=False).fill(value)
            return BrowserStepResult(
                action=action,
                status="DONE",
                message=f"Filled label target: {target}",
            )
        if action_type == "observe":
            return BrowserStepResult(
                action=action,
                status="DONE",
                message="Observed current page state.",
            )
        if action_type == "assert_text":
            text = action.get("text", "")
            body = page.locator("body").inner_text(timeout=1000)
            if text not in body:
                return BrowserStepResult(action=action, status="FAILED", message=f"Missing text: {text}")
            return BrowserStepResult(action=action, status="DONE", message=f"Verified text: {text}")
        raise PlaywrightExecutionError(f"Unsupported browser action: {action_type}")

    def _execute_with_retries(self, page: Any, action: dict[str, Any], retries: int = 2) -> BrowserStepResult:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                step = self._execute_step(page, action)
                step.retry_count = attempt
                return step
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                page.wait_for_timeout(250)
        return BrowserStepResult(
            action=action,
            status="FAILED",
            message=str(last_error),
            retry_count=retries,
        )

    def _safe_snapshot(self, page: Any) -> str:
        try:
            title = page.title()
            body = page.locator("body").inner_text(timeout=1000)
            return f"title: {title}\nbody:\n{body[:4000]}"
        except Exception as exc:
            return f"snapshot_error: {exc}"

    def _page_snapshot(self, page: Any, screenshot_path: str | None = None) -> BrowserPageSnapshot:
        text = self._safe_snapshot(page)
        nodes: list[BrowserNodeSnapshot] = []
        try:
            locators = page.locator("a,button,input,textarea,select").all()[:100]
            for index, locator in enumerate(locators):
                try:
                    nodes.append(
                        BrowserNodeSnapshot(
                            ref=f"ref-{index}",
                            role=locator.evaluate("el => el.tagName.toLowerCase()"),
                            name=locator.get_attribute("aria-label") or locator.inner_text(timeout=250)[:120],
                            selector=f"nth-interactive-{index}",
                            visible=locator.is_visible(),
                        )
                    )
                except Exception:
                    continue
        except Exception:
            nodes = []
        return BrowserPageSnapshot(
            url=getattr(page, "url", ""),
            title=page.title() if hasattr(page, "title") else "",
            text=text,
            nodes=nodes,
            screenshot_path=screenshot_path,
        )

    def _dry_snapshot(
        self,
        request: BrowserExecutionRequest,
        action: dict[str, Any],
        index: int,
    ) -> BrowserPageSnapshot:
        url = action.get("url") or (request.plan.actions[0].get("url") if request.plan.actions else "")
        target = action.get("target_text") or action.get("text") or ""
        return BrowserPageSnapshot(
            url=url,
            title="dry-run",
            text=f"dry_run step={index} action={action.get('type')} target={target}",
            nodes=[
                BrowserNodeSnapshot(
                    ref=f"dry-ref-{index}",
                    role="synthetic",
                    name=target,
                    text=target,
                    selector=f"dry:{index}",
                )
            ]
            if target
            else [],
        )

    def _verify_step(self, step: BrowserStepResult, action: dict[str, Any]) -> bool:
        if step.status != "DONE":
            return False
        if action.get("type") == "assert_text":
            expected = action.get("text", "")
            return bool(step.page_snapshot and expected in step.page_snapshot.text) or "Verified text" in step.message
        return True

    def _blocked_result(
        self,
        request: BrowserExecutionRequest,
        reason: str,
    ) -> BrowserExecutionResult:
        result = BrowserExecutionResult(
            plan_id=request.plan.plan_id,
            session_id=request.session_id,
            mode="policy",
            status="BLOCKED",
            steps=[
                BrowserStepResult(
                    action={"type": "policy_block"},
                    status="BLOCKED",
                    message=reason,
                )
            ],
            evaluation={"passed": False, "requires_hitl": True, "summary": reason},
            completed_at=datetime.now(timezone.utc),
        )
        return result

    def _evaluate(self, result: BrowserExecutionResult) -> dict[str, Any]:
        blocked = any(step.status == "BLOCKED" for step in result.steps)
        failed = any(step.status == "FAILED" for step in result.steps)
        verified = sum(1 for step in result.steps if step.verified)
        return {
            "passed": not blocked and not failed,
            "blocked": blocked,
            "failed": failed,
            "summary": result.status,
            "step_count": len(result.steps),
            "verified_steps": verified,
        }

    def _dry_run_message(self, action: dict[str, Any]) -> str:
        action_type = action.get("type")
        if action_type == "manual_checkpoint":
            return action.get("reason", "Manual checkpoint would block execution.")
        return f"Would execute browser action: {action_type}"

    def _playwright_available(self) -> bool:
        try:
            import playwright.sync_api  # noqa: F401
        except Exception:
            return False
        return True

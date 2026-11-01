"""Terminal mode: the same preview + run pipeline as the web UI, with questions asked on the console."""

from loguru import logger

from fileprep.config.store import store
from fileprep.notifiers import get_notifier
from fileprep.pipeline.planner import build_plan
from fileprep.pipeline.runner import RunHooks, run_plan, verify_plan_current
from fileprep.runtime import ConsolePrompter, Question, Runtime, use_runtime


def print_plan(plan) -> None:
    for issue in plan.issues:
        logger.log("ERROR" if issue.level == "error" else "WARNING" if issue.level == "warning" else "INFO", f"[{issue.code}] {issue.message}")
    for fp in plan.files:
        logger.info(f"{fp.source_name} -> {fp.new_file_name or '?'} [{fp.status}] steps: {', '.join(fp.steps)}")
        for issue in fp.issues:
            logger.log("ERROR" if issue.level == "error" else "WARNING" if issue.level == "warning" else "INFO", f"    [{issue.code}] {issue.message}")
    summary = plan.summary()
    logger.info(f"Preview: {summary['files']} file(s), {summary['errors']} error(s), {summary['warnings']} warning(s)")


async def run_terminal(preview_only: bool, assume_yes: bool) -> int:
    config = store.job_config()
    notify = get_notifier(config.processing.notifications.notifier) if config.processing.notifications.enabled else None
    runtime = Runtime(config=config, prompter=ConsolePrompter(), notify=notify)
    with use_runtime(runtime):
        plan = await build_plan(config)
        print_plan(plan)
        if plan.blocking:
            logger.error("Blocking issues found - fix them and run again.")
            return 1
        if preview_only:
            return 0
        answer = "yes" if assume_yes else await runtime.prompter.ask(Question(text="Start processing? (yes/no): "))
        if answer.strip().lower() not in ("y", "yes"):
            return 0
        verify_plan_current(config, plan)

        async def tracker(**kwargs):
            from fileprep.tracker.upload import upload_to_tracker
            return await upload_to_tracker(config, **kwargs)

        summary = await run_plan(config, plan, RunHooks(submit_tracker_upload=tracker), {})
        for result in summary.results:
            logger.info(f"{result.status.upper():8} {result.final_path or result.source_name} {result.error or ''}")
        from fileprep.tracker.session import registry
        registry.shutdown()
        return 1 if summary.failed else 0

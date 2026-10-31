import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory import memorize


RUN_ID = "a" * 32


class FakeJournal:
    def close(self):
        pass


class MemorySourceContractTest(unittest.TestCase):
    def make_run(self, root: Path, *, task: str = "task", run_id: str = RUN_ID) -> Path:
        home = root / task / "run-1"
        home.mkdir(parents=True)
        (home / "run_config.json").write_text(json.dumps({"run": {
            "task_name": task, "run_id": run_id,
        }}))
        (home / "workspace").mkdir()
        return home

    def patch_completed_journal(self, home: Path, *, result: str = "completed",
                                readable: bool = True, incompatible=None):
        return (
            patch("engine.event.store.open_search_store", return_value=FakeJournal()),
            patch("engine.event.store.read_run_opened_fields", return_value={
                "run_home": str(home),
                "archive_location": str(home / "run_config.json"),
            }),
            patch("engine.run_query.read_run_head", return_value=SimpleNamespace(
                readable=readable, incompatible=incompatible, result=result,
            )),
        )

    def test_exact_completed_run_is_resolved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "playground"
            home = self.make_run(root)
            patches = self.patch_completed_journal(home)
            for item in patches:
                item.start()
            try:
                run_id, paths = memorize.eligible_source_run(
                    playground_root=root, run_home=home,
                    source_run_id=RUN_ID, task_name="task",
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(run_id, RUN_ID)
            self.assertEqual(paths, (home.resolve() / "workspace",))

    def test_foreign_run_path_and_task_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "playground"
            home = self.make_run(root)
            outside = Path(temp) / "foreign"
            outside.mkdir()
            with self.assertRaisesRegex(ValueError, "outside"):
                memorize.eligible_source_run(
                    playground_root=root, run_home=outside,
                    source_run_id=RUN_ID, task_name="task",
                )
            patches = self.patch_completed_journal(home)
            for item in patches:
                item.start()
            try:
                with self.assertRaisesRegex(ValueError, "different task"):
                    memorize.eligible_source_run(
                        playground_root=root, run_home=home,
                        source_run_id=RUN_ID, task_name="foreign-task",
                    )
            finally:
                for item in reversed(patches):
                    item.stop()

    def test_unfinished_run_refuses_before_memory_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "playground"
            home = self.make_run(root)
            patches = self.patch_completed_journal(home, result=None)
            for item in patches:
                item.start()
            try:
                with patch.dict(os.environ, {"AIBUILDAI_API_KEY": "synthetic"}), \
                     patch.object(memorize, "build_task_memory") as build:
                    with self.assertRaisesRegex(ValueError, "not an eligible completed run"):
                        asyncio.run(memorize.run_memorize(
                            "task", root, home, RUN_ID,
                            SimpleNamespace(tasks_dir=Path(temp) / "memory"),
                            "synthetic-model", 0.25, 5,
                        ))
                    build.assert_not_called()
            finally:
                for item in reversed(patches):
                    item.stop()

    def test_missing_auth_refuses_before_source_resolution_or_agent(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(memorize, "eligible_source_run") as resolve, \
             patch.object(memorize, "build_task_memory") as build:
            with self.assertRaisesRegex(ValueError, "API_KEY"):
                asyncio.run(memorize.run_memorize(
                    "task", "/tmp/none", "/tmp/none", RUN_ID,
                    SimpleNamespace(tasks_dir=Path("/tmp/memory")),
                    "synthetic-model", 0.25, 5,
                ))
            resolve.assert_not_called()
            build.assert_not_called()

    def test_missing_budget_refuses_before_source_resolution_or_agent(self):
        with patch.dict(os.environ, {"AIBUILDAI_API_KEY": "synthetic"}), \
             patch.object(memorize, "eligible_source_run") as resolve, \
             patch.object(memorize, "build_task_memory") as build:
            with self.assertRaisesRegex(ValueError, "positive explicit memory generation budget"):
                asyncio.run(memorize.run_memorize(
                    "task", "/tmp/none", "/tmp/none", RUN_ID,
                    SimpleNamespace(tasks_dir=Path("/tmp/memory")),
                    "synthetic-model", 0, 5,
                ))
            resolve.assert_not_called()
            build.assert_not_called()

    def test_source_run_journal_path_must_match_named_run_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "playground"
            home = self.make_run(root)
            foreign = Path(temp) / "foreign" / "run-1"
            foreign.mkdir(parents=True)
            with patch("engine.event.store.open_search_store", return_value=FakeJournal()), \
                 patch("engine.event.store.read_run_opened_fields", return_value={
                     "run_home": str(foreign),
                     "archive_location": str(foreign / "run_config.json"),
                 }), \
                 patch("engine.run_query.read_run_head") as read_head:
                with self.assertRaisesRegex(ValueError, "different run home"):
                    memorize.eligible_source_run(
                        playground_root=root, run_home=home,
                        source_run_id=RUN_ID, task_name="task",
                    )
                read_head.assert_not_called()

    def test_symlinked_memory_root_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "playground"
            home = self.make_run(root)
            (home / "workspace").rmdir()
            foreign = Path(temp) / "foreign-workspace"
            foreign.mkdir()
            (home / "workspace").symlink_to(foreign, target_is_directory=True)
            patches = self.patch_completed_journal(home)
            for item in patches:
                item.start()
            try:
                with self.assertRaisesRegex(ValueError, "root is a symlink"):
                    memorize.eligible_source_run(
                        playground_root=root, run_home=home,
                        source_run_id=RUN_ID, task_name="task",
                    )
            finally:
                for item in reversed(patches):
                    item.stop()

    def test_unreadable_journal_refuses_before_memory_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "playground"
            home = self.make_run(root)
            patches = self.patch_completed_journal(home, readable=False)
            for item in patches:
                item.start()
            try:
                with patch.dict(os.environ, {"AIBUILDAI_API_KEY": "synthetic"}), \
                     patch.object(memorize, "build_task_memory") as build:
                    with self.assertRaisesRegex(ValueError, "not an eligible completed run"):
                        asyncio.run(memorize.run_memorize(
                            "task", root, home, RUN_ID,
                            SimpleNamespace(tasks_dir=Path(temp) / "memory"),
                            "synthetic-model", 0.25, 5,
                        ))
                    build.assert_not_called()
            finally:
                for item in reversed(patches):
                    item.stop()

    def test_unsettled_provider_cost_refuses_without_publishing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "playground"
            home = self.make_run(root)

            async def synthetic_agent(*args, **kwargs):
                return "# task\n\nSynthetic output without a settled receipt."

            with patch.dict(os.environ, {"AIBUILDAI_API_KEY": "synthetic"}), \
                 patch.object(memorize, "eligible_source_run",
                              return_value=(RUN_ID, (home / "workspace",))), \
                 patch.object(memorize, "build_task_memory", side_effect=synthetic_agent), \
                 patch.object(memorize, "publish_task_memory") as publish:
                with self.assertRaisesRegex(ValueError, "did not return settled"):
                    asyncio.run(memorize.run_memorize(
                        "task", root, home, RUN_ID,
                        SimpleNamespace(tasks_dir=Path(temp) / "memory"),
                        "synthetic-model", 0.25, 5,
                    ))
                publish.assert_not_called()

    def test_agent_budget_reaches_claude_sdk_options(self):
        from infra.backends.spec import AgentSpec
        from infra.backends.claude.spec_to_options import spec_to_options

        spec = AgentSpec(
            name="memory", instructions="synthetic", model="synthetic-model",
            tools=(), cwd="/tmp", max_budget_usd=0.25,
        )
        with patch("infra.backends.claude.spec_to_options.ClaudeAgentOptions",
                   side_effect=lambda **options: options):
            options = spec_to_options(
                spec, cli_path="/bin/false", resume_session_id=None,
                stderr_callback=lambda _line: None,
            )
        self.assertEqual(options["max_budget_usd"], 0.25)

    def test_synthetic_producer_writes_only_after_settled_cost(self):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        from infra.backends.claude import conversation as conversation_module

        class SuccessClient:
            def __init__(self, **_kwargs):
                pass

            async def connect(self):
                pass

            async def query(self, _prompt):
                pass

            async def receive_response(self):
                if False:
                    yield None
                yield AssistantMessage(
                    content=[TextBlock("# task\n\nSynthetic evidence only.")],
                    model="<synthetic>", message_id="synthetic-message",
                )
                yield ResultMessage(
                    subtype="success", duration_ms=1, duration_api_ms=1,
                    is_error=False, num_turns=1, session_id="synthetic-session",
                    stop_reason="end_turn", total_cost_usd=0.11,
                    usage={"input_tokens": 1000},
                    model_usage={"<synthetic>": {
                        "inputTokens": 1000, "outputTokens": 0,
                        "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                        "webSearchRequests": 0, "costUSD": 0.11,
                    }},
                )

            async def disconnect(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "playground"
            home = self.make_run(root)
            dest = Path(temp) / "candidate"

            with patch.dict(os.environ, {"AIBUILDAI_API_KEY": "synthetic"}), \
                 patch.object(memorize, "eligible_source_run", return_value=(RUN_ID, (home / "workspace",))), \
                 patch.object(conversation_module, "ClaudeSDKClient", SuccessClient), \
                 patch.object(conversation_module, "_create_memfd",
                              side_effect=lambda _name: os.open(os.devnull, os.O_RDWR)), \
                 patch.object(conversation_module, "build_agent_launcher",
                              return_value=("/synthetic/launcher", None)), \
                 patch.object(conversation_module, "spec_to_options",
                              return_value=object()):
                result = asyncio.run(memorize.run_memorize(
                    "task", root, home, RUN_ID,
                    SimpleNamespace(tasks_dir=dest / "tasks"),
                    "synthetic-model", 0.25, 5,
                ))
            self.assertEqual(result["generation_cost_usd"], 0.0)
            self.assertEqual(result["generation_cost_label"], "measured")
            self.assertEqual(result["provider_reported_cost_usd"], 0.11)
            self.assertEqual(result["provider_reported_cost_label"], "reported")
            self.assertEqual(Path(result["task_memory_path"]).read_text(),
                             "# task\n\nSynthetic evidence only.\n")

    def test_unsettled_retry_is_refused_before_a_second_provider_invocation(self):
        from infra.backends.claude import conversation as conversation_module

        class RetryClient:
            instances = []

            def __init__(self, **_kwargs):
                self.query_count = 0
                self.instances.append(self)

            async def connect(self):
                pass

            async def query(self, _prompt):
                self.query_count += 1

            async def receive_response(self):
                from infra.backends.claude.errors import _RetryableError
                if False:
                    yield None
                raise _RetryableError("synthetic transient request failure")

            async def disconnect(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "run"
            home.mkdir()
            with patch.dict(os.environ, {"AIBUILDAI_API_KEY": "synthetic"}), \
                 patch.object(memorize, "eligible_source_run",
                              return_value=(RUN_ID, (home,))), \
                 patch.object(conversation_module, "ClaudeSDKClient", RetryClient), \
                 patch.object(conversation_module, "_create_memfd",
                              side_effect=lambda _name: os.open(os.devnull, os.O_RDWR)), \
                 patch.object(conversation_module, "build_agent_launcher",
                              return_value=("/synthetic/launcher", None)), \
                 patch.object(conversation_module, "spec_to_options",
                              return_value=object()), \
                 patch.object(conversation_module, "_RETRY_POLL_INTERVAL_S", 0), \
                 patch.object(memorize, "publish_task_memory") as publish:
                with self.assertRaisesRegex(ValueError, "retry refused"):
                    asyncio.run(memorize.run_memorize(
                        "task", temp, home, RUN_ID,
                        SimpleNamespace(tasks_dir=Path(temp) / "memory"),
                        "synthetic-model", 0.25, 5,
                    ))
            self.assertEqual(len(RetryClient.instances), 2)
            self.assertEqual(RetryClient.instances[0].query_count, 1)
            self.assertEqual(RetryClient.instances[1].query_count, 0)
            publish.assert_not_called()

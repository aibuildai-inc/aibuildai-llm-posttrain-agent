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
            self.assertEqual(paths, (home / "workspace",))

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
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "playground"
            home = self.make_run(root)
            dest = Path(temp) / "candidate"

            async def synthetic_agent(task, runs, *, model, max_budget_usd, observer):
                self.assertEqual(max_budget_usd, 0.25)
                self.assertEqual([run_id for run_id, _ in runs], [RUN_ID])
                observer.costs.append(0.11)
                return "# task\n\nSynthetic evidence only."

            with patch.dict(os.environ, {"AIBUILDAI_API_KEY": "synthetic"}), \
                 patch.object(memorize, "eligible_source_run", return_value=(RUN_ID, (home / "workspace",))), \
                 patch.object(memorize, "build_task_memory", side_effect=synthetic_agent):
                result = asyncio.run(memorize.run_memorize(
                    "task", root, home, RUN_ID,
                    SimpleNamespace(tasks_dir=dest / "tasks"),
                    "synthetic-model", 0.25, 5,
                ))
            self.assertEqual(result["generation_cost_usd"], 0.11)
            self.assertEqual(Path(result["task_memory_path"]).read_text(),
                             "# task\n\nSynthetic evidence only.\n")

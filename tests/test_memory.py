from __future__ import annotations

# pylint: disable=import-error

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api.main as api_main
from omni_analyst.memory.fabric import MemoryFabric


class PersistentMemoryTests(unittest.TestCase):
    def test_memory_fabric_persists_all_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "memory.json"
            first = MemoryFabric(storage_path=storage_path)
            first.append_short_term("sess-1", {"type": "user_query", "query": "hello"})
            first.append_episodic("sess-1", {"type": "test_event"})
            first.merge_long_term("user-1", {"prefers": "concise answers"})
            first.merge_procedural("user-1", {"style": "run tests after edits"})
            first.merge_document("doc-1", {"filename": "contract.pdf"})

            second = MemoryFabric(storage_path=storage_path)
            session_snapshot = second.get_session_snapshot("sess-1")
            user_snapshot = second.get_user_snapshot("user-1")
            document_snapshot = second.get_document_snapshot("doc-1")

            self.assertEqual(session_snapshot["short_term"][0]["type"], "user_query")
            self.assertEqual(session_snapshot["episodic"][0]["type"], "test_event")
            self.assertEqual(user_snapshot["long_term"]["prefers"], "concise answers")
            self.assertEqual(
                user_snapshot["procedural"]["style"],
                "run tests after edits",
            )
            self.assertEqual(document_snapshot["filename"], "contract.pdf")

    def test_project_instruction_memory_loads_known_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "CLAUDE.md").write_text("Use pytest.", encoding="utf-8")
            cursor_rules = root / ".cursor" / "rules"
            cursor_rules.mkdir(parents=True)
            (cursor_rules / "style.md").write_text("Prefer small diffs.", encoding="utf-8")

            memory = MemoryFabric(storage_path=root / "memory.json")
            project_memory = memory.load_project_instructions(root)

            paths = {Path(item["path"]).name for item in project_memory["instruction_files"]}
            self.assertIn("CLAUDE.md", paths)
            self.assertIn("style.md", paths)

    def test_memory_query_compact_export_import_stats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "memory.json"
            memory = MemoryFabric(storage_path=storage_path)
            for index in range(5):
                memory.append_short_term(
                    "sess-compact",
                    {"type": "user_query", "query": f"revenue question {index}"},
                )
            hits = memory.query("revenue", owner_id="sess-compact")
            self.assertGreaterEqual(len(hits), 1)
            compacted = memory.compact_session("sess-compact", keep_last=2)
            self.assertTrue(compacted["compacted"])
            exported = memory.export_state()
            imported = MemoryFabric(storage_path=Path(temp_dir) / "memory-imported.json")
            stats = imported.import_state(exported, merge=False)
            self.assertEqual(stats["short_term_sessions"], 1)


class MemoryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        api_main.memory_fabric = api_main.MemoryFabric(
            storage_path=Path(self.temp_dir.name) / "memory.json"
        )
        self.client = TestClient(api_main.app)

    def test_user_and_document_memory_endpoints(self) -> None:
        long_term = self.client.post(
            "/api/v5/memory/users/user-api/long-term",
            json={"item": {"domain": "finance"}},
        )
        self.assertEqual(long_term.status_code, 200)
        self.assertEqual(long_term.json()["long_term"]["domain"], "finance")

        procedural = self.client.post(
            "/api/v5/memory/users/user-api/procedural",
            json={"item": {"format": "bullets"}},
        )
        self.assertEqual(procedural.status_code, 200)
        self.assertEqual(procedural.json()["procedural"]["format"], "bullets")

        document = self.client.post(
            "/api/v5/memory/documents/doc-api",
            json={"item": {"filename": "report.pdf"}},
        )
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document.json()["filename"], "report.pdf")

    def test_project_memory_endpoint(self) -> None:
        root = Path(self.temp_dir.name)
        (root / "AGENTS.md").write_text("Always verify changes.", encoding="utf-8")
        response = self.client.post(
            "/api/v5/memory/project/load-instructions",
            json={"root": str(root)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["instruction_files"][0]["content"],
            "Always verify changes.",
        )

    def test_memory_query_stats_and_compact_endpoints(self) -> None:
        for index in range(3):
            api_main.memory_fabric.append_short_term(
                "sess-api-compact",
                {"type": "note", "content": f"security control {index}"},
            )
        query = self.client.post(
            "/api/v5/memory/query",
            json={"text": "security", "owner_id": "sess-api-compact"},
        )
        self.assertEqual(query.status_code, 200)
        self.assertGreaterEqual(len(query.json()), 1)
        compact = self.client.post(
            "/api/v5/memory/sess-api-compact/compact",
            json={"keep_last": 1},
        )
        self.assertEqual(compact.status_code, 200)
        stats = self.client.get("/api/v5/memory/stats")
        self.assertEqual(stats.status_code, 200)
        self.assertGreaterEqual(stats.json()["short_term_sessions"], 1)


if __name__ == "__main__":
    unittest.main()

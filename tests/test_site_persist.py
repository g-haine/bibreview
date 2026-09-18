from __future__ import annotations

from pathlib import Path
import os
import tempfile
import unittest

from bibreview.site import (
    RenderedArtifact,
    SitePersistenceError,
    apply_rendered_artifacts,
    plan_rendered_artifacts,
)


class SiteArtifactPersistenceTests(unittest.TestCase):
    def test_plan_and_apply_reconcile_managed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_posts").mkdir()
            (root / "authors").mkdir()
            (root / "_posts/keep.md").write_text("same", encoding="utf-8")
            (root / "_posts/change.md").write_text("old", encoding="utf-8")
            (root / "_posts/obsolete.md").write_text("remove", encoding="utf-8")
            (root / "authors/obsolete.md").write_text("remove", encoding="utf-8")
            (root / "manual.txt").write_text("untouched", encoding="utf-8")

            artifacts = (
                RenderedArtifact(path="_posts/keep.md", content="same"),
                RenderedArtifact(path="_posts/change.md", content="new"),
                RenderedArtifact(path="authors/index.md", content="authors"),
            )
            plan = plan_rendered_artifacts(
                root,
                artifacts,
                managed_roots=("_posts", "authors"),
            )

            self.assertTrue(plan.changed)
            self.assertEqual(plan.expected_count, 3)
            self.assertEqual(plan.unchanged_count, 1)
            self.assertEqual(
                set(plan.writes),
                {root / "_posts/change.md", root / "authors/index.md"},
            )
            self.assertEqual(
                set(plan.deletes),
                {root / "_posts/obsolete.md", root / "authors/obsolete.md"},
            )

            apply_rendered_artifacts(plan)

            self.assertEqual((root / "_posts/keep.md").read_text(), "same")
            self.assertEqual((root / "_posts/change.md").read_text(), "new")
            self.assertEqual((root / "authors/index.md").read_text(), "authors")
            self.assertFalse((root / "_posts/obsolete.md").exists())
            self.assertFalse((root / "authors/obsolete.md").exists())
            self.assertEqual((root / "manual.txt").read_text(), "untouched")

    def test_identical_tree_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "years").mkdir()
            (root / "years/2025.md").write_text("same", encoding="utf-8")
            plan = plan_rendered_artifacts(
                root,
                (RenderedArtifact(path="years/2025.md", content="same"),),
                managed_roots=("years",),
            )
            self.assertFalse(plan.changed)
            self.assertEqual(
                plan.summary(),
                "expected: 1; write: 0; delete: 0; unchanged: 1",
            )

    def test_artifact_outside_managed_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SitePersistenceError, "outside managed roots"):
                plan_rendered_artifacts(
                    tmp,
                    (RenderedArtifact(path="index.md", content="x"),),
                    managed_roots=("_posts",),
                )

    def test_parent_traversal_and_absolute_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("../escape.md", "/absolute.md", "_posts/../escape.md"):
                with self.subTest(path=bad):
                    with self.assertRaises(SitePersistenceError):
                        plan_rendered_artifacts(
                            tmp,
                            (RenderedArtifact(path=bad, content="x"),),
                            managed_roots=("_posts",),
                        )

    def test_duplicate_artifact_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SitePersistenceError, "duplicate artifact"):
                plan_rendered_artifacts(
                    tmp,
                    (
                        RenderedArtifact(path="_posts/a.md", content="one"),
                        RenderedArtifact(path="_posts/a.md", content="two"),
                    ),
                    managed_roots=("_posts",),
                )

    def test_nested_managed_root_is_supported_without_touching_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_data/bibreview").mkdir(parents=True)
            (root / "_data/manual.json").write_text("manual", encoding="utf-8")
            (root / "_data/bibreview/obsolete.json").write_text(
                "obsolete",
                encoding="utf-8",
            )
            artifact = RenderedArtifact(
                path="_data/bibreview/metadata.json",
                content="metadata",
            )
            plan = plan_rendered_artifacts(
                root,
                (artifact,),
                managed_roots=("_data/bibreview",),
            )
            apply_rendered_artifacts(plan)

            self.assertEqual(
                (root / "_data/bibreview/metadata.json").read_text(),
                "metadata",
            )
            self.assertFalse(
                (root / "_data/bibreview/obsolete.json").exists()
            )
            self.assertEqual(
                (root / "_data/manual.json").read_text(),
                "manual",
            )

    def test_overlapping_managed_roots_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SitePersistenceError, "must not overlap"):
                plan_rendered_artifacts(
                    tmp,
                    (),
                    managed_roots=("_data", "_data/bibreview"),
                )

    @unittest.skipIf(os.name == "nt", "symlink semantics differ on Windows")
    def test_symlinked_managed_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (root / "_posts").symlink_to(Path(outside), target_is_directory=True)
            with self.assertRaisesRegex(SitePersistenceError, "symlink"):
                plan_rendered_artifacts(
                    root,
                    (RenderedArtifact(path="_posts/a.md", content="x"),),
                    managed_roots=("_posts",),
                )


if __name__ == "__main__":
    unittest.main()

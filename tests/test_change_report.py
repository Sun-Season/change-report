import pathlib
import subprocess
import sys
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "change_report.py"


class ChangeReportTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Change Report Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.write("tracked.ts", "export function oldReader() { return 1; }\n")
        self.write(".gitignore", "ignored.ts\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")

    def run_command(self, *args, check=True):
        return subprocess.run(
            args, cwd=self.root, capture_output=True, text=True, check=check
        )

    def git(self, *args):
        return self.run_command("git", *args).stdout

    def command(self, *args, check=True):
        return self.run_command(sys.executable, str(SCRIPT), *args, check=check)

    def write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def start(self, *args):
        return self.command("start", *args).stdout.split("任务编号：")[1].strip()

    def report(self, run):
        return self.command("report", "--run", run).stdout

    def test_mark_old_keeps_baseline_and_replays_after_new_task(self):
        self.write("tracked.ts", "export function oldReader() { return 2; }\n")
        self.git("add", "tracked.ts")
        run = self.start("--old", "first")
        self.assertIn("在本次比较范围内未检出改动", self.report(run))
        self.write("tracked.ts", "export const replacement = 3;\n")
        status = self.git("status", "--porcelain")
        base_ref = f"refs/change-report/runs/{run}"
        base = self.git("rev-parse", base_ref)
        self.command("mark-old", "--run", run, "oldReader")
        latest = self.start()

        report = self.report(run)
        self.assertEqual(self.git("rev-parse", base_ref), base)
        self.assertEqual(self.git("status", "--porcelain"), status)
        self.assertIn("tracked.ts:1 oldReader", report)
        self.assertIn("first: 0 处", report)
        self.assertIn("oldReader: 0 处", report)
        self.assertIn(f"--run {run}", report)
        self.assertIn("在本次比较范围内未检出改动", self.report(latest))
        self.assertIn(latest, self.command("report").stdout)

    def test_existing_untracked_changes_and_deletions_are_visible(self):
        self.write("existing.ts", "const legacy = 1;\n")
        self.write("deleted.ts", "const remove = 1;\n")
        self.write("ignored.ts", "const legacy = 1;\n")
        run = self.start()
        self.write("existing.ts", "const legacy = 2;\n")
        self.git("add", "existing.ts")
        (self.root / "deleted.ts").unlink()
        self.write("new.ts", "const fallback = 1;\n")
        self.write("ignored.ts", "const legacy = 2;\n")
        report = self.report(run)
        self.assertIn("existing.ts（原未跟踪文件有变化）", report)
        self.assertIn("deleted.ts（原未跟踪文件已删除）", report)
        self.assertIn("new.ts:1: const fallback = 1;", report)
        self.assertNotIn("existing.ts:1:", report)
        self.assertNotIn("ignored.ts", report)
        self.assertIn("无法核对其逐行增删", report)

    def test_same_name_in_another_file_preserves_old_location(self):
        run = self.start()
        self.write("tracked.ts", "export const replacement = 3;\n")
        self.write("some other.ts", "export function oldReader() { return 5; }\n")
        self.git("add", "some other.ts")
        report = self.report(run)
        self.assertIn("tracked.ts:1 oldReader\n", report)
        self.assertIn("some other.ts", report)

    def test_missing_run_does_not_fall_back_to_latest(self):
        self.start()
        result = self.command("report", "--run", "0" * 32, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("未找到任务", result.stderr)
        self.assertNotIn("未检出改动", result.stdout)


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import time
import unittest
from pathlib import Path

from utility.file_reloader import FileReloader


class RegisterTest(unittest.TestCase):
    def test_register_loads_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            p.write_text("hello")
            r = FileReloader()
            value = r.register("m", str(p), parse_fn=lambda t: {"len": len(t)})
            self.assertEqual(value, {"len": 5})
            self.assertEqual(r.get("m"), {"len": 5})


class ReloadTest(unittest.TestCase):
    def test_reload_on_mtime_change(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            p.write_text("aaa")
            r = FileReloader(interval=0.05)
            r.register("m", str(p), parse_fn=lambda t: {"v": t})
            self.assertEqual(r.get("m"), {"v": "aaa"})
            before = p.stat().st_mtime_ns
            p.write_text("bbb")
            os.utime(p, ns=(before + 1, before + 1))   # 强制 mtime 变化
            deadline = time.time() + 2
            while r.get("m") != {"v": "bbb"} and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(r.get("m"), {"v": "bbb"})


class FailureTest(unittest.TestCase):
    def test_parse_error_keeps_old_value(self):
        def parse(t):
            if t == "BAD":
                raise ValueError("parse error")
            return {"v": t}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            p.write_text("aaa")
            r = FileReloader(interval=0.05)
            r.register("m", str(p), parse_fn=parse)
            before = p.stat().st_mtime_ns
            p.write_text("BAD")
            os.utime(p, ns=(before + 1, before + 1))
            time.sleep(0.2)                       # 给线程一轮时间
            self.assertEqual(r.get("m"), {"v": "aaa"})  # 旧值仍在

    def test_missing_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            r = FileReloader()
            r.register("missing", str(Path(d) / "nope.yaml"),
                       parse_fn=lambda t: {"v": t})
            self.assertIsNone(r.get("missing"))

    def test_file_appears_later_loads(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            r = FileReloader(interval=0.05)
            r.register("m", str(p), parse_fn=lambda t: {"v": t})
            self.assertIsNone(r.get("m"))   # 当前缺失
            p.write_text("ccc")              # 稍后出现
            deadline = time.time() + 2
            while r.get("m") != {"v": "ccc"} and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(r.get("m"), {"v": "ccc"})


if __name__ == "__main__":
    unittest.main()
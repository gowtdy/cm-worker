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


if __name__ == "__main__":
    unittest.main()
import tempfile
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


if __name__ == "__main__":
    unittest.main()
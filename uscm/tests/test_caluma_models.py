import tempfile
import unittest
from pathlib import Path

from utility import caluma_models
from utility.caluma_models import _parse_caluma
from utility.file_reloader import FileReloader


SAMPLE = """models:
  english:
    - id: 0
      name: voice-lady-female
      display: Female-Jessie(Voice Lady)
      catid: english
      catname: English(US)
      author: OceanWhisper
      wavplay: /wav/a.mp3
      modelicon: jessie.webp
      modellang: en
      model_type: index-tts
      model_typeversion: v2.5
  china:
    - id: 1
      name: zh-female
      display: 中文女声
      catid: china
      catname: Chinese
      author: someone
      wavplay: /wav/b.mp3
      modelicon: ''
      modellang: zh
      model_type: gsv
      model_typeversion: v1
"""


class ParseCalumaTest(unittest.TestCase):
    def test_flatten_to_catid_name_key(self):
        d = _parse_caluma(SAMPLE)
        self.assertEqual(len(d), 2)
        e = d["english:voice-lady-female"]
        self.assertEqual(e["catid"], "english")
        self.assertEqual(e["name"], "voice-lady-female")
        self.assertEqual(e["display"], "Female-Jessie(Voice Lady)")
        self.assertEqual(e["model_type"], "index-tts")

    def test_empty_optional_field_preserved(self):        # Review Focus #5
        d = _parse_caluma(SAMPLE)
        self.assertEqual(d["china:zh-female"]["modelicon"], "")

    def test_duplicate_key_last_wins(self):               # Review Focus #4
        dup = """models:
  english:
    - {id: 0, name: n, catid: english, display: first}
    - {id: 1, name: n, catid: english, display: second}
"""
        d = _parse_caluma(dup)
        self.assertEqual(len(d), 1)
        self.assertEqual(d["english:n"]["display"], "second")

    def test_empty_text(self):
        self.assertEqual(_parse_caluma(""), {})

    def test_bad_entry_skipped(self):
        y = """models:
  english:
    - {id: 0, name: good, catid: english, display: ok}
    - {id: 1, display: missing-name}
    - not-a-dict
"""
        d = _parse_caluma(y)
        self.assertEqual(len(d), 1)
        self.assertIn("english:good", d)


class GetCommonModelTest(unittest.TestCase):
    def test_lookup_and_miss(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            p.write_text(SAMPLE)
            r = FileReloader(interval=60)
            r.register(caluma_models.MODELS_NAME, str(p), caluma_models._parse_caluma)
            m = caluma_models.get_common_model("english", "voice-lady-female", reloader=r)
            self.assertEqual(m["display"], "Female-Jessie(Voice Lady)")
            self.assertIsNone(caluma_models.get_common_model("english", "nope", reloader=r))  # Review Focus #3


if __name__ == "__main__":
    unittest.main()
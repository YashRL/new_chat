import json

from content_extraction import data_extractor


class FakePage:
    def __init__(self, text, label):
        self._text = text
        self._label = label

    def get_text(self, mode):
        assert mode == "text"
        return self._text

    def get_label(self):
        return self._label


class FakeDoc:
    def __init__(self):
        self.metadata = {}
        self.page_count = 2
        self._pages = [
            FakePage("Short text", "1"),
            FakePage("Another short text", "2"),
        ]

    def __getitem__(self, idx):
        return self._pages[idx]

    def get_toc(self, simple=False):
        assert simple is False
        return []


def test_process_pdf_skips_ocr_when_tesseract_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(data_extractor.fitz, "open", lambda _: FakeDoc())
    monkeypatch.setattr(data_extractor.ocr_utils, "detect_pages_needing_ocr", lambda doc, force_skip=False: [0, 1])
    monkeypatch.setattr(data_extractor.ocr_utils, "is_tesseract_available", lambda: False)

    output_dir = tmp_path / "out"
    data_extractor.process_pdf("fake.pdf", output_dir=str(output_dir), output_json="output.json")

    written = json.loads((output_dir / "output.json").read_text(encoding="utf-8"))
    metadata = written[0]["metadata"]
    content = written[1]

    assert metadata["ocr_applied"] is False
    assert metadata["ocr_skipped_reason"] == "tesseract_not_available"
    assert content["mode"] == "page_dump"
    assert len(content["pages"]) == 2
    assert all(page["ocr_applied"] is False for page in content["pages"])

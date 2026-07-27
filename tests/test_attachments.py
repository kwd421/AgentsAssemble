import base64
import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.room.attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_EVENT,
    AttachmentError,
    decode_attachment_data,
    normalize_attachment_id,
    normalize_attachment_references,
    normalize_content_type,
    public_attachment_metadata,
    read_attachment_file,
    sanitize_attachment_filename,
    store_uploaded_attachment,
)


class TestSanitizeAttachmentFilename(unittest.TestCase):
    def test_normal_filename(self):
        self.assertEqual(sanitize_attachment_filename("hello.txt"), "hello.txt")

    def test_strips_path_separators(self):
        self.assertEqual(sanitize_attachment_filename("/etc/passwd"), "passwd")
        self.assertEqual(sanitize_attachment_filename("..\\..\\secret.txt"), "secret.txt")

    def test_control_chars_removed(self):
        self.assertEqual(sanitize_attachment_filename("file\x00name.txt"), "filename.txt")
        self.assertEqual(sanitize_attachment_filename("file\x7fname.txt"), "filename.txt")

    def test_dot_returns_default(self):
        self.assertEqual(sanitize_attachment_filename("."), "attachment.bin")

    def test_dotdot_returns_default(self):
        self.assertEqual(sanitize_attachment_filename(".."), "attachment.bin")

    def test_empty_returns_default(self):
        self.assertEqual(sanitize_attachment_filename(""), "attachment.bin")
        self.assertEqual(sanitize_attachment_filename(None), "attachment.bin")

    def test_caps_length(self):
        long_name = "a" * 200 + ".txt"
        result = sanitize_attachment_filename(long_name)
        self.assertLessEqual(len(result), 120)

    def test_backslash_path(self):
        self.assertEqual(sanitize_attachment_filename("C:\\Users\\file.doc"), "file.doc")


class TestNormalizeContentType(unittest.TestCase):
    def test_valid_type_passes(self):
        self.assertEqual(normalize_content_type("image/png", "x.png"), "image/png")

    def test_strips_params(self):
        self.assertEqual(normalize_content_type("text/html; charset=utf-8", "x.html"), "text/html")

    def test_invalid_falls_back_to_mimetypes(self):
        result = normalize_content_type("garbage", "photo.jpg")
        self.assertEqual(result, "image/jpeg")

    def test_none_falls_back(self):
        result = normalize_content_type(None, "data.json")
        self.assertEqual(result, "application/json")

    def test_unknown_extension_falls_back_to_octet_stream(self):
        result = normalize_content_type("", "file.xyzzy123")
        self.assertEqual(result, "application/octet-stream")


class TestDecodeAttachmentData(unittest.TestCase):
    def test_valid_base64(self):
        encoded = base64.b64encode(b"hello world").decode()
        self.assertEqual(decode_attachment_data(encoded), b"hello world")

    def test_data_uri_prefix_stripped(self):
        encoded = base64.b64encode(b"test").decode()
        data_uri = f"data:image/png;base64,{encoded}"
        self.assertEqual(decode_attachment_data(data_uri), b"test")

    def test_rejects_non_string(self):
        with self.assertRaises(AttachmentError):
            decode_attachment_data(None)
        with self.assertRaises(AttachmentError):
            decode_attachment_data(123)

    def test_rejects_empty_string(self):
        with self.assertRaises(AttachmentError):
            decode_attachment_data("")
        with self.assertRaises(AttachmentError):
            decode_attachment_data("   ")

    def test_rejects_invalid_base64(self):
        with self.assertRaises(AttachmentError):
            decode_attachment_data("not!valid!base64!!!")


class TestNormalizeAttachmentId(unittest.TestCase):
    def test_valid_id(self):
        self.assertEqual(normalize_attachment_id("abcdef12"), "abcdef12")

    def test_64_char_id(self):
        long_id = "a" * 64
        self.assertEqual(normalize_attachment_id(long_id), long_id)

    def test_too_short(self):
        with self.assertRaises(AttachmentError):
            normalize_attachment_id("short")

    def test_too_long(self):
        with self.assertRaises(AttachmentError):
            normalize_attachment_id("a" * 65)

    def test_invalid_chars(self):
        with self.assertRaises(AttachmentError):
            normalize_attachment_id("abcd/../../..")

    def test_none(self):
        with self.assertRaises(AttachmentError):
            normalize_attachment_id(None)


class TestNormalizeAttachmentReferences(unittest.TestCase):
    def test_none_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(normalize_attachment_references(Path(tmp), None), [])

    def test_empty_string_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(normalize_attachment_references(Path(tmp), ""), [])

    def test_non_list_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AttachmentError):
                normalize_attachment_references(Path(tmp), "not a list")

    def test_max_count_enforcement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Create more than MAX attachments
            refs = []
            for i in range(MAX_ATTACHMENTS_PER_EVENT + 1):
                aid = f"attachment_{i:08d}"
                d = root / "attachments" / aid
                d.mkdir(parents=True)
                meta = {"id": aid, "filename": "f.txt", "content_type": "text/plain", "size": 1, "is_image": False}
                (d / "metadata.json").write_text(json.dumps(meta))
                refs.append({"id": aid})
            with self.assertRaises(AttachmentError):
                normalize_attachment_references(root, refs)

    def test_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            aid = "dedupe_test_id_01"
            d = root / "attachments" / aid
            d.mkdir(parents=True)
            meta = {"id": aid, "filename": "f.txt", "content_type": "text/plain", "size": 1, "is_image": False}
            (d / "metadata.json").write_text(json.dumps(meta))
            (d / "f.txt").write_text("x")
            refs = [{"id": aid}, {"id": aid}]
            result = normalize_attachment_references(root, refs)
            self.assertEqual(len(result), 1)


class TestStoreAndReadRoundTrip(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b"round trip content"
            encoded = base64.b64encode(content).decode()
            payload = {"filename": "test.txt", "content_type": "text/plain", "data_base64": encoded}
            meta = store_uploaded_attachment(root, payload)
            self.assertEqual(meta["filename"], "test.txt")
            self.assertEqual(meta["content_type"], "text/plain")
            self.assertEqual(meta["size"], len(content))
            # Read back
            pub_meta, file_path = read_attachment_file(root, meta["id"])
            self.assertEqual(file_path.read_bytes(), content)
            self.assertEqual(pub_meta["filename"], "test.txt")

    def test_size_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            big = b"x" * (MAX_ATTACHMENT_BYTES + 1)
            encoded = base64.b64encode(big).decode()
            payload = {"filename": "big.bin", "content_type": "application/octet-stream", "data_base64": encoded}
            with self.assertRaises(AttachmentError):
                store_uploaded_attachment(root, payload)


class TestPathTraversalContainment(unittest.TestCase):
    def test_tampered_storage_filename_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b"secret"
            encoded = base64.b64encode(content).decode()
            payload = {"filename": "safe.txt", "content_type": "text/plain", "data_base64": encoded}
            meta = store_uploaded_attachment(root, payload)
            # Tamper with metadata to attempt path traversal
            aid = meta["id"]
            meta_path = root / "attachments" / aid / "metadata.json"
            stored = json.loads(meta_path.read_text())
            stored["storage_filename"] = "../../escape.txt"
            meta_path.write_text(json.dumps(stored))
            with self.assertRaises(AttachmentError):
                read_attachment_file(root, aid)


class TestIsImageClassification(unittest.TestCase):
    def test_svg_upload_is_not_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            encoded = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>").decode()
            meta = store_uploaded_attachment(
                Path(tmp), {"filename": "x.svg", "content_type": "image/svg+xml", "data_base64": encoded}
            )
            self.assertFalse(meta["is_image"])

    def test_png_upload_is_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            encoded = base64.b64encode(b"png-bytes").decode()
            meta = store_uploaded_attachment(
                Path(tmp), {"filename": "x.png", "content_type": "image/png", "data_base64": encoded}
            )
            self.assertTrue(meta["is_image"])

    def test_public_metadata_reclassifies_svg_even_if_stored_true(self):
        meta = public_attachment_metadata(
            {"id": "abcdef12", "filename": "x.svg", "content_type": "image/svg+xml", "size": 10, "is_image": True}
        )
        self.assertFalse(meta["is_image"])


if __name__ == "__main__":
    unittest.main()

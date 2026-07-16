"""claude Code TUI answer extraction (real PTY capture + edge cases).

REAL_CAPTURE_B64 is an actual claude Code 2.1.177 PTY byte stream answering
"원피스에서 누가 제일 세?" — the assistant reply rendered as `⏺루피.` amid the
echoed prompt, the gerund spinner, the conversation title, and the token footer.
The extractor must pull out exactly "루피." (anti-regression against real chrome).
"""
import base64
import unittest

from agentsassemble.providers.claude_resident import (
    _strip_envelope_leak,
    claude_answer_ready,
    extract_claude_terminal_message,
    render_terminal_screen,
)


class ScreenRenderTests(unittest.TestCase):
    """The VT screen emulator that recovers spaces from cursor-positioned TUI
    output (the fix for Claude's "현재시점" / "현재  시점" mangling)."""

    def test_real_space_between_cjk_preserved_single(self):
        self.assertEqual(render_terminal_screen("현재 시점".encode()), "현재 시점")

    def test_cursor_forward_gap_renders_as_one_space(self):
        # CJK is double-width; a CUF over the (single) space column must not
        # become several spaces — the width-aware grid keeps it to one.
        seq = "현재".encode() + b"\x1b[1C" + "시점".encode()
        self.assertEqual(render_terminal_screen(seq), "현재 시점")

    def test_cursor_reposition_overwrites(self):
        seq = "가나".encode() + b"\x1b[1;1H" + "다라마".encode()
        self.assertEqual(render_terminal_screen(seq), "다라마")

    def test_plain_ascii_untouched(self):
        self.assertEqual(render_terminal_screen(b"hello world\r\n"), "hello world")

REAL_CAPTURE_B64 = (
    "GzcbW3IbOBtbPzI1aBtbPzI1bBtbPzIwMDRoG1s/MTAwNGgbWz8yMDMxaBtbPjBxG1tjG1s/MTA0OWgbWzJKG1tIG1s/MTAwMGgb"
    "Wz8xMDAyaBtbPzEwMDNoG1s/MTAwNmgbXTA74pyzIENsYXVkZSBDb2RlBxtbSA0bWzFCG1szODs1OzE3NG3ila3ilIDilIDilIAb"
    "WzZHQ2xhdWRlIENvZGUbWzE4RxtbMzg7NTsyNDZtdjIuMS4xNzcbWzI3RxtbMzg7NTsxNzRt4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pWuDRtbMULilIIb"
    "WzU0RxtbMm3ilIIbWzU2RxtbMjJtG1sxbVRpcHMgZm9yIGdldHRpbmcgG1s4MEcbWzIybeKUgg0bWzFC4pSCG1sxOEcbWzM5bRtb"
    "MW1XZWxjb21lIGJhY2sg662J7YOx7J20IRtbNTRHG1syMm0bWzJtG1szODs1OzE3NG3ilIIbWzU2RxtbMjJtG1sxbXN0YXJ0ZWQb"
    "WzgwRxtbMjJt4pSCDRtbMULilIIbWzU0RxtbMm3ilIIbWzU2RxtbMzltG1syMm1SdW4bWzYwRy9pbml0G1s2Nkd0bxtbNjlHY3Jl"
    "YXRlG1s3NkdhG1s3OEfigKYbWzgwRxtbMzg7NTsxNzRt4pSCDRtbMULilIIbWzI0RyDilpAbWzQ4OzU7MTZt4pab4paI4paI4paI"
    "4pacG1s0OW3ilowbWzU0RxtbMm3ilIIbWzU2RxtbMjJt4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAG1s4MEfilIING1sxQuKUghtbMjRH4pad4pacG1s0ODs1OzE2beKWiOKWiOKWiOKW"
    "iOKWiBtbNDlt4pab4paYG1s1NEcbWzJt4pSCG1s1NkcbWzIybRtbMW1XaGF0J3MgbmV3G1s4MEcbWzIybeKUgg0bWzFC4pSCG1sy"
    "NEcgIOKWmOKWmCDilp3ilp0gIBtbNTRHG1sybeKUghtbNTZHG1szOW0bWzIybUFkZGVkG1s2MkdgVG9vbChwYXJhbTp2YWx14oCm"
    "G1s4MEcbWzM4OzU7MTc0beKUgg0bWzFC4pSCG1s3RxtbMzg7NTsyNDZtT3B1cyA0LjggwrcgQ2xhdWRlIFBybyDCtyBrd2Q0MjFA"
    "Z21haWwuY29tJ3MgG1s1NEcbWzJtG1szODs1OzE3NG3ilIIbWzU2RxtbMzltG1syMm1Ta2lsbHMbWzYzR2luG1s2NkduZXN0ZWQb"
    "WzczR2AuY2xh4oCmG1s4MEcbWzM4OzU7MTc0beKUgg0bWzFC4pSCG1s3RxtbMzg7NTsyNDZtT3JnYW5pemF0aW9uG1s1NEcbWzJt"
    "G1szODs1OzE3NG3ilIIbWzU2RxtbMzltG1syMm1OZXN0ZWQbWzYzR2AuY2xhdWRlL2AbWzc0R2RpcmXigKYbWzgwRxtbMzg7NTsx"
    "NzRt4pSCDRtbMULilIIbWzE2RxtbMzg7NTsyNDZtfi9Qcm9qZWN0cy9BZ2VudHNBc3NlbWJsZRtbNTRHG1sybRtbMzg7NTsxNzRt"
    "4pSCG1s1NkcbWzIybRtbMzg7NTsyNDZtG1szbS9yZWxlYXNlLW5vdGVzIGZvciBtb3JlG1s4MEcbWzIzbRtbMzg7NTsxNzRt4pSC"
    "DRtbMULilbDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDila8NG1s2M0MbWzhCG1szODs1OzI0Nm3il48gaGlnaCDCtyAvZWZmb3J0DRtbMUIbWzM4OzU7MjQ0"
    "beKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgA0bWzFCG1szOW3ina/CoBtbMm1UcnkgImhvdyBkb2VzIEFwcC50c3ggd29yaz8iDRtbMUIbWzIybRtb"
    "Mzg7NTsyNDRt4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSADRtbMkMbWzFCG1szODs1OzI0Nm0/IGZvciBzaG9ydGN1dHMgwrcg4oaQIGZvciBhZ2Vu"
    "dHMbWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoGyhCDxtbPzEwMDBoG1s/MTAwMmgbWz8xMDAzaBtbPzEwMDZoG1s/MjVsG1tIDRtb"
    "MkMbWzIxQuybkO2UvOyKpOyXkOyEnCDriITqsIAg7KCc7J28IOyEuD8g65SxIO2VnBtbMzRH66y47J6l7Jy866Gc66eMLg0bWzJD"
    "G1syQhtbSxtbMjQ7MUgbWzIyOzQ1SBtbPzI1aBtdMDvioIIgQ2xhdWRlIENvZGUHG1s/MjVsG1tIDRtbMTRCG1s0ODs1OzIzN20b"
    "WzM4OzU7MjM5beKdryAbWzM4OzU7MjMxbeybkO2UvOyKpOyXkOyEnCDriITqsIAg7KCc7J28IOyEuD8g65SxIO2VnCDrrLjsnqXs"
    "nLzroZzrp4wuG1szOW0gICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICANG1s0QhtbNDltG1szODs1OzE3NG3inLMb"
    "WzNHVG9wc3ktdHVydnlpbmfigKYgDRtbM0IbWzM4OzU7MjQ2beKdr8KgG1szOW0bW0sNG1syQxtbMkIbWzM4OzU7MjQ2bWVzYyB0"
    "byBpbnRlcnJ1cHQbWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG1s/MjVsG1tIDRtbMThCG1szODs1OzE3NG3inKIbWzM5bRtbMjQ7"
    "MUgbWzIyOzNIG1s/MjVoG1s/MjVsG1tIDRtbMkMbWzE4QhtbMzg7NTsyMTZtVG8bWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG1s/"
    "MjVsG1tIDRtbMThCG1szODs1OzE3NG3CtxtbNUcbWzM4OzU7MjE2bXAbWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG1s/MjVsG1tI"
    "DRtbMkMbWzE4QhtbMzg7NTsxNzRtVBtbNkcbWzM4OzU7MjE2bXMbWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG1s/MjVsG1tIDRtb"
    "M0MbWzE4QhtbMzg7NTsxNzRtb3AbWzdHG1szODs1OzIxNm15LRtbMzltG1syNDsxSBtbMjI7M0gbWz8yNWgbWz8yNWwbW0gNG1s1"
    "QxtbMThCG1szODs1OzE3NG1zG1s5RxtbMzg7NTsyMTZtdBtbMzltG1syNDsxSBtbMjI7M0gbWz8yNWgbWz8yNWwbW0gNG1s2Qxtb"
    "MThCG1szODs1OzE3NG15G1sxMEcbWzM4OzU7MjE2bXUbWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG1s/MjVsG1tIDRtbN0MbWzE4"
    "QhtbMzg7NTsxNzRtLRtbMTFHG1szODs1OzIxNm1yG1szOW0bWzI0OzFIG1syMjszSBtbPzI1aBtbPzI1bBtbSA0bWzE4QhtbMzg7"
    "NTsxNzRt4pyiG1s5R3R1G1sxMkcbWzM4OzU7MjE2bXZ5G1szOW0bWzI0OzFIG1syMjszSBtbPzI1aBtbPzI1bBtbSA0bWzEwQxtb"
    "MThCG1szODs1OzE3NG1yG1sxNEcbWzM4OzU7MjE2bWkbWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG1s/MjVsG1tIDRtbMTFDG1sx"
    "OEIbWzM4OzU7MTc0bXYbWzE1RxtbMzg7NTsyMTZtbhtbMzltG1syNDsxSBtbMjI7M0gbWz8yNWgbWz8yNWwbW0gNG1sxOEIbWzM4"
    "OzU7MTc0beKcsxtbMTNHeWkbWzE2RxtbMzg7NTsyMTZtZ+KAphtbMzltG1syNDsxSBtbMjI7M0gbWz8yNWgbWz8yNWwbW0gNG1sx"
    "NEMbWzE4QhtbMzg7NTsxNzRtbhtbMzltG1syNDsxSBtbMjI7M0gbWz8yNWgbXTA74qCQIENsYXVkZSBDb2RlBxtbPzI1bBtbSA0b"
    "WzE4QhtbMzg7NTsxNzRt4py2G1sxNkdnG1szOW0bWzI0OzFIG1syMjszSBtbPzI1aBtbPzI1bBtbSA0bWzE2QxtbMThCG1szODs1"
    "OzE3NG3igKYbWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG1s/MjVsG1tIDRtbMThCG1szODs1OzE3NG3inLsbWzM5bRtbMjQ7MUgb"
    "WzIyOzNIG1s/MjVoG1s/MjVsG1tIDRtbMThCG1szODs1OzE3NG3inL0bWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG10wO+KgkCDs"
    "m5DtlLzsiqQg7LWc6rCV7J6QIOyInOychCDsp4jrrLgHG1s/MjVsG1tIDRtbMTZCG1szODs1OzIzMW3ij7obWzNHG1szOW3ro6jt"
    "lLwuG1syNDsxSBtbMjI7M0gbWz8yNWgbWz8yNWwbW0gNG1sxOEMbWzE4QhtbMzg7NTsyNDZtKDFzIMK3IOKGkxtbMjdHMSB0b2tl"
    "bnMpG1szOW0bWzI0OzFIG1syMjszSBtbPzI1aBtdMDvinLMg7JuQ7ZS87IqkIOy1nOqwleyekCDsiJzsnIQg7KeI66y4BxtbPzI1"
    "bBtbSA0bWzE4QhtbMzg7NTsyNDZt4py7G1szR0NodXJuZWQgZm9yIDFzG1szOW0bW0sNG1szQuKdr8KgDRtbMkMbWzJCG1szODs1"
    "OzI0Nm0/IGZvciBzaG9ydGN1dHMgwrcg4oaQIGZvciBhZ2VudHMbWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG1s/MjVsG1tIDRtb"
    "MkMbWzE5QhtbMzg7NTsyMjBtWW91J3ZlIHVzZWQgNzclIG9mIHlvdXIgd2Vla2x5IGxpbWl0IMK3IHJlc2V0cyBKdW4gMTkgYXQg"
    "OGFtIChBc2lhL1Nlb3VsKRtbMzg7NTsyNDZtIMK3IHTigKYbWzM5bRtbMjQ7MUgbWzIyOzNIG1s/MjVoG1s/MjVsG1tIDRtbMkMb"
    "WzE5QhtbSxtbMjQ7MUgbWzIyOzNIG1s/MjVo"
)


def _ansi(text: str) -> bytes:
    # a few real-ish escape sequences interleaved, to prove ANSI stripping
    return ("\x1b[2K\x1b[1m" + text + "\x1b[0m").encode("utf-8")


class ClaudeTerminalExtractionTests(unittest.TestCase):
    def test_extracts_answer_from_real_capture(self):
        raw = base64.b64decode(REAL_CAPTURE_B64)
        self.assertTrue(claude_answer_ready(raw))
        self.assertEqual(extract_claude_terminal_message(raw), "루피.")

    def test_single_line_answer_strips_marker_and_chrome(self):
        raw = (
            _ansi("\u276f 원피스에서 누가 제일 세?") + b"\n"
            + _ansi("\u2733 Topsy-turvying\u2026") + b"\n"
            + _ansi("\u23fa 샹크스. 사황이자 패기 최강이라.") + b"\n"
            + _ansi("(2s \u00b7 \u219313 tokens)") + b"\n"
            + _ansi("? for shortcuts \u00b7 \u2190 for agents") + b"\n"
        )
        self.assertTrue(claude_answer_ready(raw))
        self.assertEqual(extract_claude_terminal_message(raw), "샹크스. 사황이자 패기 최강이라.")

    def test_multiline_answer_joins_until_chrome(self):
        raw = (
            _ansi("\u23fa 첫 줄.") + b"\n"
            + _ansi("둘째 줄.") + b"\n"
            + _ansi("(1s \u00b7 \u21935 tokens)") + b"\n"
        )
        self.assertEqual(extract_claude_terminal_message(raw), "첫 줄.\n둘째 줄.")

    def test_no_answer_marker_is_not_ready_and_extracts_empty(self):
        raw = (
            _ansi("\u2733 Churning\u2026") + b"\n"
            + _ansi("\u276f ") + b"\n"
            + _ansi("esc to interrupt") + b"\n"
        )
        self.assertFalse(claude_answer_ready(raw))
        self.assertEqual(extract_claude_terminal_message(raw), "")

    def test_last_marker_wins_for_multiturn_screen(self):
        raw = (
            _ansi("\u23fa 예전 답.") + b"\n"
            + _ansi("(1s \u00b7 tokens)") + b"\n"
            + _ansi("\u276f 새 질문") + b"\n"
            + _ansi("\u23fa 새 답.") + b"\n"
            + _ansi("(1s \u00b7 tokens)") + b"\n"
        )
        self.assertEqual(extract_claude_terminal_message(raw), "새 답.")


class EnvelopeLeakStripTests(unittest.TestCase):
    def test_strips_mashed_envelope_echo(self):
        # The real artifact: a Korean answer with the (space-mashed) envelope echo.
        leaked = "누가 카이도 위를 증명할 근거를 댈 수 있죠?transporthasroomtools,inspectread-since,archivea"
        self.assertEqual(
            _strip_envelope_leak(leaked), "누가 카이도 위를 증명할 근거를 댈 수 있죠?"
        )

    def test_strips_spaced_envelope_echo(self):
        leaked = "real answer here. transport has room tools, inspect read-since"
        self.assertEqual(_strip_envelope_leak(leaked), "real answer here.")

    def test_leaves_clean_answer_untouched(self):
        clean = "루피. 니카 각성으로 카이도를 이겼으니까."
        self.assertEqual(_strip_envelope_leak(clean), clean)


if __name__ == "__main__":
    unittest.main()

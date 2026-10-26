import json
import logging
import re

from src.core.slicer.openscad import clean_scad_code

logger = logging.getLogger(__name__)


class ParsedAIResponse(tuple):
    """A 4-tuple (code, filename, colors, expected_parts_count) with dict compatibility."""

    def __new__(
        cls,
        code: str,
        filename: str,
        colors: list[str],
        expected_parts_count: int,
        raw_dict: dict | None = None,
    ):
        return super().__new__(cls, (code, filename, colors, expected_parts_count))

    def __init__(
        self,
        code: str,
        filename: str,
        colors: list[str],
        expected_parts_count: int,
        raw_dict: dict | None = None,
    ):
        self._dict = raw_dict or {
            "code": code,
            "filename": filename,
            "color_hierarchy": colors,
            "colors": colors,
            "expected_parts_count": expected_parts_count,
        }

    def get(self, key, default=None):
        return self._dict.get(key, default)

    def __getitem__(self, item):
        if isinstance(item, str):
            return self._dict[item]
        return super().__getitem__(item)


def _try_repair_json(text: str) -> dict | None:
    """Attempt to repair malformed JSON from AI by re-escaping the code field."""
    code_start_match = re.search(r'"code"\s*:\s*"', text)
    if not code_start_match:
        return None

    code_value_start = code_start_match.end()

    end_patterns = [
        r'",\s*"[a-zA-Z_]+"\s*:',  # next key boundary
        r'"\s*\}\s*$',  # end of JSON object
    ]

    code_value_end = None
    for pattern in end_patterns:
        end_match = re.search(pattern, text[code_value_start:])
        if end_match:
            code_value_end = code_value_start + end_match.start()
            break

    if code_value_end is None:
        return None

    raw_code = text[code_value_start:code_value_end]

    escaped_code = raw_code
    escaped_code = escaped_code.replace("\\\\", "\x00BACKSLASH\x00")
    escaped_code = escaped_code.replace("\\n", "\x00NEWLINE\x00")
    escaped_code = escaped_code.replace('\\"', "\x00QUOTE\x00")
    escaped_code = escaped_code.replace("\\t", "\x00TAB\x00")

    escaped_code = escaped_code.replace("\\", "\\\\")
    escaped_code = escaped_code.replace('"', '\\"')
    escaped_code = escaped_code.replace("\n", "\\n")
    escaped_code = escaped_code.replace("\r", "\\r")
    escaped_code = escaped_code.replace("\t", "\\t")

    escaped_code = escaped_code.replace("\x00BACKSLASH\x00", "\\\\")
    escaped_code = escaped_code.replace("\x00NEWLINE\x00", "\\n")
    escaped_code = escaped_code.replace("\x00QUOTE\x00", '\\"')
    escaped_code = escaped_code.replace("\x00TAB\x00", "\\t")

    repaired = text[:code_value_start] + escaped_code + text[code_value_end:]

    try:
        return json.loads(repaired)
    except (json.JSONDecodeError, ValueError):
        stripped = repaired.rstrip()
        while stripped.endswith("}}"):
            stripped = stripped[:-1]
            try:
                return json.loads(stripped)
            except Exception:
                pass
        for suffix in ['"}', "}", '"']:
            try:
                return json.loads(repaired.rstrip() + suffix)
            except Exception:
                pass
        return None


def parse_ai_response(response_text: str) -> tuple[str, str, list[str], int]:
    """Parse AI JSON response with robust error recovery.

    Returns (code: str, filename: str, color_hierarchy: list[str], expected_parts_count: int).
    """
    original_response_text = response_text
    start_idx = response_text.find("{")
    end_idx = response_text.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        response_text = response_text[start_idx : end_idx + 1]
    else:
        response_text = re.sub(r"^```[a-zA-Z]*\s*", "", response_text)
        response_text = re.sub(r"\s*```$", "", response_text)

    response_text_fixed = response_text.strip()

    try:
        data = json.loads(response_text_fixed)
        if "thought_process" in data:
            logger.info(f"AI Thought Process: {data['thought_process']}")

        try:
            epc = int(data.get("expected_parts_count", 1))
        except (TypeError, ValueError):
            epc = 1

        return ParsedAIResponse(
            clean_scad_code(data.get("code", "")),
            data.get("filename", "gemini_part"),
            data.get("color_hierarchy", []),
            epc,
            raw_dict=data,
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Initial JSON parse failed ({e}), attempting repair...")

        repaired_data = _try_repair_json(response_text_fixed)
        if repaired_data and "code" in repaired_data:
            if "thought_process" in repaired_data:
                logger.info(f"AI Thought Process: {repaired_data['thought_process']}")
            try:
                epc = int(repaired_data.get("expected_parts_count", 1))
            except (TypeError, ValueError):
                epc = 1

            return ParsedAIResponse(
                clean_scad_code(repaired_data["code"]),
                repaired_data.get("filename", "gemini_part"),
                repaired_data.get("color_hierarchy", []),
                epc,
                raw_dict=repaired_data,
            )

        logger.warning("JSON repair failed, attempting raw regex extraction...")
        code = None
        thought = None
        filename = "gemini_part"

        tp_match = re.search(
            r'"thought_process"\s*:\s*"((?:[^"\\]|\\.)*)"', response_text, re.DOTALL
        )
        if tp_match:
            thought = tp_match.group(1)

        fn_match = re.search(r'"filename"\s*:\s*"((?:[^"\\]|\\.)*)"', response_text, re.DOTALL)
        if fn_match:
            filename = fn_match.group(1)

        code_match = re.search(
            r'"code"\s*:\s*"(.*?)"(?:\s*,\s*"[a-zA-Z_]+"\s*:|\s*\}\s*$)', response_text, re.DOTALL
        )
        if code_match:
            code = code_match.group(1)
        else:
            code_match2 = re.search(r'"code"\s*:\s*"(.*)"\s*\}', response_text, re.DOTALL)
            if code_match2:
                code = code_match2.group(1)
            else:
                md_match = re.search(
                    r"```(?:openscad|scad)?(.*?)```",
                    original_response_text,
                    re.DOTALL | re.IGNORECASE,
                )
                if md_match:
                    code = md_match.group(1).strip()
                elif not response_text.strip().startswith("{"):
                    code = response_text

        if code:
            _unescape_map = {
                "\\": "\\",
                "n": "\n",
                "r": "\r",
                "t": "\t",
                '"': '"',
            }
            code = re.sub(r'\\(\\|n|r|t|")', lambda m: _unescape_map[m.group(1)], code)
            if thought:
                logger.info(f"AI Thought Process: {thought}")

            colors = []
            color_match = re.search(r'"color_hierarchy"\s*:\s*\[(.*?)\]', response_text, re.DOTALL)
            if color_match:
                colors = re.findall(r'"([^"]*)"', color_match.group(1))

            epc = 1
            epc_match = re.search(r'"expected_parts_count"\s*:\s*(\d+)', response_text)
            if epc_match:
                epc = int(epc_match.group(1))

            return ParsedAIResponse(clean_scad_code(code.strip()), filename, colors, epc)

        return ParsedAIResponse(
            "// Error: AI returned malformed JSON that could not be parsed.\n// Raw Output:\n/*\n"
            + response_text
            + "\n*/\n",
            "gemini_part",
            [],
            1,
        )


_parse_response = parse_ai_response

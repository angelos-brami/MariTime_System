import base64
import binascii
import html
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

INSTRUCTION_PATTERNS = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget)\b.{0,40}\b(instruction|prompt|system|rules?)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "role_override": re.compile(
        r"\b(system message|developer message|you are now|act as)\b", re.IGNORECASE
    ),
    "tool_request": re.compile(
        r"\b(call|invoke|use|run|execute)\b.{0,30}\b(tool|shell|terminal|command|function)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "secret_request": re.compile(
        r"\b(reveal|print|return|exfiltrate)\b.{0,30}\b(secret|token|password|api key)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "greek_instruction_override": re.compile(
        r"\b(αγνόησε|παράβλεψε|ξέχασε)\b.{0,50}\b(οδηγίες|κανόνες|προτροπή)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "turkish_instruction_override": re.compile(
        r"\b(talimatları|kuralları|sistem mesajını)\b.{0,50}\b(görmezden gel|unut|yoksay)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "arabic_instruction_override": re.compile(
        r"(تجاهل|انس|تجاوز).{0,50}(التعليمات|القواعد|رسالة النظام)",
        re.IGNORECASE | re.DOTALL,
    ),
    "multilingual_role_override": re.compile(
        r"(είσαι τώρα|şimdi sen|أنت الآن).{0,40}(σύστημα|sistem|نظام|مساعد)",
        re.IGNORECASE | re.DOTALL,
    ),
}


@dataclass(frozen=True)
class SecurityScanResult:
    injection_suspected: bool
    matched_rules: tuple[str, ...]
    scanner_version: str = "instruction-scan-v2"

    def as_dict(self) -> dict[str, bool | str | list[str]]:
        return {
            "injection_suspected": self.injection_suspected,
            "quarantined": self.injection_suspected,
            "matched_rules": list(self.matched_rules),
            "scanner_version": self.scanner_version,
        }


def scan_untrusted_text(text: str) -> SecurityScanResult:
    normalized = unicodedata.normalize("NFKC", html.unescape(unquote(text)))
    normalized = normalized.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    matches = [name for name, pattern in INSTRUCTION_PATTERNS.items() if pattern.search(normalized)]
    encoded_matches = re.findall(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{24,}={0,2}", normalized)
    for candidate in encoded_matches[:20]:
        try:
            decoded = base64.b64decode(candidate, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            continue
        decoded = unicodedata.normalize("NFKC", decoded)
        if any(pattern.search(decoded) for pattern in INSTRUCTION_PATTERNS.values()):
            matches.append("encoded_instruction")
            break
    unique_matches = tuple(dict.fromkeys(matches))
    return SecurityScanResult(
        injection_suspected=bool(unique_matches), matched_rules=unique_matches
    )

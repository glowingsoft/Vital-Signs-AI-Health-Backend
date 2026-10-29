# Checks and cleans the input of POST /ai/assess before any motor is called.
# The spec says to send only symptoms and vitals to the AI, never a name, phone, email or address,
# so personal information in the symptoms text is replaced with a tag such as [PHONE].
# Medical numbers ("120/80", "38.5 C", "3 days", "SpO2 91%") are left as they are.

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Dict, NamedTuple, Optional, Pattern, Tuple

from fastapi import HTTPException

from app import config
from app.shared.contracts import AssessRequest

logger = logging.getLogger(__name__)


def digit_count(text: str) -> int:
    return sum(char.isdigit() for char in text)


def passes_luhn(number: str) -> bool:
    """The checksum every real card number passes; random digit strings almost never do."""
    digits = [int(char) for char in number if char.isdigit()]
    total = 0
    # From the right, every second digit is doubled (minus 9 when it goes over 9).
    for position, digit in enumerate(reversed(digits)):
        if position % 2 == 1:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def is_card(match: str) -> bool:
    return 13 <= digit_count(match) <= 19 and passes_luhn(match)


def is_phone(match: str) -> bool:
    # 10+ digits covers US and Mexico; with a +country code, shorter international numbers too.
    # More than 15 digits is never a phone number (E.164 limit).
    return (8 if match.startswith("+") else 10) <= digit_count(match) <= 15


class PiiPattern(NamedTuple):
    name: str
    regex: Pattern
    tag: str  # the text that replaces the match, e.g. "[PHONE]" for tag "PHONE"
    # Optional extra check on the matched text; the match is kept as it is when this returns False.
    check: Optional[Callable[[str], bool]] = None
    # When the regex has a group named "value", only that group is replaced (e.g. the name after
    # "my name is"); otherwise the whole match is replaced.


# Building blocks shared by several patterns.
CAPITAL = "A-ZÁÉÍÓÚÑÜ"
CAP_WORD = rf"[{CAPITAL}][^\W\d_]*(?:['-][^\W\d_]+)*"  # "María", "O'Brien", "Smith-Jones"
CAP_NAME = rf"{CAP_WORD}(?:\s+{CAP_WORD}){{0,2}}"  # one to three capitalised words
ANY_WORD = r"[^\W\d_]+"
MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|"
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
)
DATE = (
    r"(?:\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}"  # 03/15/1990, 1990-03-15
    rf"|(?i:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"  # March 15, 1990
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:de\s+)?(?i:{MONTHS})\.?,?\s+(?:de\s+|del\s+)?\d{{4}}"  # 15 de marzo de 1990
    r"|\d{4})"  # born in 1990
)
STREET_WORDS = r"street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|court|ct"
SPANISH_STREET_WORDS = r"calle|avenida|av|colonia|col|privada|calzada"

# Every pattern, in the order they run. Earlier patterns win: emails and URLs go first so their
# digits are not taken as phone numbers, cards before phones because both are long digit runs.
PII_PATTERNS = [
    PiiPattern("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "EMAIL"),
    PiiPattern("url", re.compile(r"(?i:\bhttps?://|\bwww\.)[^\s<>\"]*[^\s<>\".,;:!?)\]]"), "URL"),
    PiiPattern(
        "date_of_birth",
        re.compile(
            r"(?i:\b(?:born(?:\s+(?:on|in))?|d\.?o\.?b\.?|date\s+of\s+birth|fecha\s+de\s+nacimiento|nac[ií]\s+(?:el|en))"
            rf")\s*[:\-]?\s*(?P<value>{DATE})(?!\d)"
        ),
        "DOB",
    ),
    PiiPattern("us_ssn", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), "ID"),
    # CURP: 4 letters, birth date, sex (H/M), state and consonants, 2 check characters = 18 characters.
    PiiPattern("mx_curp", re.compile(r"(?i)\b[A-Z]{4}\d{6}[HMX][A-Z]{5}[A-Z0-9]\d\b"), "ID"),
    # RFC: 3 letters (companies) or 4 (people), date, 3 check characters.
    PiiPattern("mx_rfc", re.compile(r"(?i)\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b"), "ID"),
    # A number starting with "+" is a phone number, so it is never taken as a card.
    PiiPattern("card", re.compile(r"(?<![\d+])(?<!\d\.)\d(?:[ -]?\d){12,18}(?!\d|\.\d)"), "CARD", is_card),
    PiiPattern(
        "phone",
        # Optional +country code (with the "1" of older Mexican mobile numbers), optional (area code), then 2-4 digit groups separated by one space,
        # dot or dash. Groups of at least 2 digits keep "4567 3 days" from swallowing the "3".
        re.compile(
            r"(?<![\w+/.,])(?:\+\d{1,3}(?:[\s.-]1)?[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}(?:[\s.-]?\d{2,4}){1,5}(?![\w/%]|[.,]\d)"
        ),
        "PHONE",
        is_phone,
    ),
    # "123 Main Street": a number, one to four capitalised words, then a street word.
    # Capitalised words only, so "3 days on the road" is left alone.
    PiiPattern(
        "address",
        re.compile(rf"\b\d{{1,6}}\s+(?:{CAP_WORD}\s+){{1,4}}(?i:{STREET_WORDS})\b"),
        "ADDRESS",
    ),
    # "Calle Reforma 123", "Avenida Juárez #45", "Colonia Roma": the street word comes first in Spanish.
    PiiPattern(
        "address_es",
        re.compile(
            rf"(?i:\b(?:{SPANISH_STREET_WORDS}))\.?\s+{CAP_WORD}(?:\s+{CAP_WORD}){{0,3}}"
            r"(?:\s*(?:#|(?i:no\.?|n[uú]m\.?))?\s*\d{1,6})?"
        ),
        "ADDRESS",
    ),
    # Only phrases that clearly introduce a name. A rule that guesses from a capitalised word can remove
    # a symptom (e.g. 'This is Severe pain'), which is worse than missing a name.
    # The phrase is kept and only the name is replaced: up to three capitalised words, or one word in any case.
    PiiPattern(
        "name",
        re.compile(
            r"(?i:\b(?:(?:my\s+name\s+is|my\s+name's|me\s+llamo|mi\s+nombre\s+es)\s+|(?:name|nombre)\s*:\s*))"
            rf"(?P<value>{CAP_NAME}|{ANY_WORD})"
        ),
        "NAME",
    ),
]

# Phrases that try to change how the model behaves. Removed (not blocked) and logged.
INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+instructions\b"),
    re.compile(r"(?i)\bignore\s+all\s+(?:the\s+)?instructions\b"),
    re.compile(r"(?i)\bsystem\s+prompt\b"),
    re.compile(r"(?i)\byou\s+are\s+now\b"),
    re.compile(r"(?i)\bolvida\s+(?:todas\s+)?las\s+instrucciones\b"),
]

# Invisible characters that could hide text from the patterns above.
ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}


@dataclass
class GuardrailReport:
    removed: Dict[str, int] = field(default_factory=dict)  # tag -> how many, e.g. {"PHONE": 1}
    injection_removed: bool = False

    @property
    def anything_removed(self) -> bool:
        return bool(self.removed) or self.injection_removed


def remove_control_characters(text: str) -> str:
    """Control and zero-width characters go; normal newlines stay and tabs become spaces."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return "".join(
        char for char in text
        if char == "\n" or (unicodedata.category(char) != "Cc" and char not in ZERO_WIDTH)
    )


def tidy_spaces(text: str) -> str:
    """Remove the double spaces and stray leading punctuation that a removed phrase leaves behind."""
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    return text.strip(" ,;:\n")


def remove_injection(text: str) -> Tuple[str, bool]:
    found = False
    for pattern in INJECTION_PATTERNS:
        text, count = pattern.subn(" ", text)
        found = found or count > 0
    return (tidy_spaces(text) if found else text), found


def remove_pii(text: str, removed: Dict[str, int]) -> str:
    """Replace every PII_PATTERNS match with its tag. Adds the counts to removed."""
    for pattern in PII_PATTERNS:

        def replace(match: re.Match) -> str:
            if pattern.check and not pattern.check(match.group(0)):
                return match.group(0)
            removed[pattern.tag] = removed.get(pattern.tag, 0) + 1
            if "value" in pattern.regex.groupindex:
                start, end = match.span("value")
                offset = match.start()
                whole = match.group(0)
                return whole[: start - offset] + f"[{pattern.tag}]" + whole[end - offset:]
            return f"[{pattern.tag}]"

        text = pattern.regex.sub(replace, text)
    return text


def clean_symptoms(symptoms: str, report: GuardrailReport) -> Optional[str]:
    """The cleaned text, or None when nothing is left. 422 when it is too long."""
    text = remove_control_characters(symptoms).strip()
    if len(text) > config.MAX_SYMPTOMS_CHARS:
        raise HTTPException(status_code=422, detail="symptoms are too long")
    text, report.injection_removed = remove_injection(text)
    text = remove_pii(text, report.removed)
    return text or None


def clean_request(request: AssessRequest) -> Tuple[AssessRequest, GuardrailReport]:
    """A NEW request with cleaned symptoms (the original is never changed), and what was removed."""
    report = GuardrailReport()
    if request.symptoms is None:
        return request.model_copy(), report
    symptoms = clean_symptoms(request.symptoms, report)
    if report.injection_removed:
        logger.warning("Guardrails: prompt-injection text removed from symptoms")  # never the text itself
    return request.model_copy(update={"symptoms": symptoms}), report

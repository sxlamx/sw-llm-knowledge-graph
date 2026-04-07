"""Regex pattern library for high-precision entity detection.

These patterns provide zero-cost, high-precision detection for:
- Legal citations (cases, statutes, regulations)
- PII (emails, phones, SSNs, credit cards)
- Financial identifiers (tickers, CUSIPs, ISINs)
- Technical identifiers (IPs, URLs, file hashes)

All patterns are compiled at module load time for performance.
"""

import re
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class PatternCategory(str, Enum):
    """Categories for regex patterns."""
    LEGAL = "legal"
    PII = "pii"
    FINANCIAL = "financial"
    TECHNICAL = "technical"
    GENERAL = "general"


@dataclass
class RegexPattern:
    """A compiled regex pattern with metadata."""
    name: str
    pattern: str
    category: PatternCategory
    label: str  # NER label to assign
    description: str
    flags: int = re.IGNORECASE
    compiled: Optional[re.Pattern] = None
    
    def __post_init__(self):
        self.compiled = re.compile(self.pattern, self.flags)
    
    def find_all(self, text: str) -> list[dict]:
        """Find all matches in text with character offsets."""
        matches = []
        for match in self.compiled.finditer(text):
            matches.append({
                "label": self.label,
                "text": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "confidence": 1.0,  # Regex matches are high-confidence
                "source": "regex",
            })
        return matches


# ============================================================================
# LEGAL CITATION PATTERNS
# ============================================================================

LEGAL_PATTERNS = [
    # US Case Citations
    RegexPattern(
        name="us_case_supreme_court",
        pattern=r"\d+\s+U\.?\s*S\.?\s*(?:2d|3d|4th|5th)?\s+\d+",
        category=PatternCategory.LEGAL,
        label="CASE_CITATION",
        description="US Supreme Court citations (e.g., 505 U.S. 83)"
    ),
    RegexPattern(
        name="us_case_federal_reporter",
        pattern=r"\d+\s+F\.?\s*(?:2d|3d|4th)?\s+\d+",
        category=PatternCategory.LEGAL,
        label="CASE_CITATION",
        description="Federal Reporter citations (e.g., 954 F.2d 113)"
    ),
    RegexPattern(
        name="us_case_state_reporter",
        pattern=r"\d+\s+[A-Z][a-z]?(?:\.\s*2d|\.\s*3d)?\s+\d+",
        category=PatternCategory.LEGAL,
        label="CASE_CITATION",
        description="State reporter citations (e.g., 123 Cal.App.4th 456)"
    ),
    
    # UK/Commonwealth Case Citations
    RegexPattern(
        name="uk_case_citation",
        pattern=r"\[\d{4}\]\s+(?:UKHL|UKSC|EWCA\s+Civ|EWCA\s+Crim|EWHC\s+[A-Z]+)\s+\d+",
        category=PatternCategory.LEGAL,
        label="CASE_CITATION",
        description="UK case citations (e.g., [2021] UKSC 12)"
    ),
    RegexPattern(
        name="singapore_case_citation",
        pattern=r"\[\d{4}\]\s+SG(?:CA|HC|DC|MC)\s+\d+",
        category=PatternCategory.LEGAL,
        label="CASE_CITATION",
        description="Singapore case citations (e.g., [2021] SGCA 1)"
    ),
    RegexPattern(
        name="australia_case_citation",
        pattern=r"\[\d{4}\]\s+HCA\s+\d+",
        category=PatternCategory.LEGAL,
        label="CASE_CITATION",
        description="Australian High Court citations (e.g., [2020] HCA 1)"
    ),
    
    # Statute Citations
    RegexPattern(
        name="us_code_citation",
        pattern=r"\d+\s+U\.?S\.?C\.?\s+§?\s*\d+",
        category=PatternCategory.LEGAL,
        label="STATUTE_CITATION",
        description="US Code citations (e.g., 42 U.S.C. § 1983)"
    ),
    RegexPattern(
        name="public_law",
        pattern=r"Pub\.?\s*L\.?\s*No\.?\s*\d+-\d+",
        category=PatternCategory.LEGAL,
        label="STATUTE_CITATION",
        description="US Public Law citations (e.g., Pub. L. No. 117-263)"
    ),
    RegexPattern(
        name="statute_at_large",
        pattern=r"\d+\s+Stat\.\s*\d+",
        category=PatternCategory.LEGAL,
        label="STATUTE_CITATION",
        description="Statutes at Large citations (e.g., 136 Stat. 1234)"
    ),
    
    # Regulatory Citations
    RegexPattern(
        name="cfr_citation",
        pattern=r"\d+\s+C\.?F\.?R\.?\s+§?\s*\d+",
        category=PatternCategory.LEGAL,
        label="REGULATION_CITATION",
        description="Code of Federal Regulations (e.g., 17 C.F.R. § 240.10b-5)"
    ),
    RegexPattern(
        name="federal_register",
        pattern=r"\d+\s+Fed\.?\s*Reg\.?\s*\d+",
        category=PatternCategory.LEGAL,
        label="REGULATION_CITATION",
        description="Federal Register citations (e.g., 87 Fed. Reg. 12345)"
    ),
    
    # Legal Document Identifiers
    RegexPattern(
        name="docket_number",
        pattern=r"(?:Case|Docket|Civil)\s*(?:No\.?|Nos?\.?)\s*[:\s]*\d{1,2}[:-]\w{2,5}(?:-\w{2,4})?",
        category=PatternCategory.LEGAL,
        label="DOCKET_NUMBER",
        description="Court docket numbers (e.g., Case No. 21-cv-01234)"
    ),
    RegexPattern(
        name="slip_opinion",
        pattern=r"No\.\s*\d+(?:-\w+)?\s*\(?\w{2,4}\s*\d{4}\)?",
        category=PatternCategory.LEGAL,
        label="SLIP_OPINION",
        description="Slip opinion numbers (e.g., No. 21-123 (2023))"
    ),
]

# ============================================================================
# PII PATTERNS
# ============================================================================

PII_PATTERNS = [
    # Email Addresses
    RegexPattern(
        name="email_address",
        pattern=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        category=PatternCategory.PII,
        label="EMAIL_ADDRESS",
        description="Email addresses"
    ),
    
    # Phone Numbers (various formats)
    RegexPattern(
        name="phone_us",
        pattern=r"(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
        category=PatternCategory.PII,
        label="PHONE_NUMBER",
        description="US phone numbers"
    ),
    RegexPattern(
        name="phone_intl",
        pattern=r"\+\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}",
        category=PatternCategory.PII,
        label="PHONE_NUMBER",
        description="International phone numbers"
    ),
    
    # Social Security Numbers
    RegexPattern(
        name="ssn_us",
        pattern=r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",
        category=PatternCategory.PII,
        label="SSN",
        description="US Social Security Numbers"
    ),
    
    # Credit Card Numbers (major issuers)
    RegexPattern(
        name="credit_card_visa",
        pattern=r"\b4[0-9]{3}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
        category=PatternCategory.PII,
        label="CREDIT_CARD_NUMBER",
        description="Visa credit card numbers"
    ),
    RegexPattern(
        name="credit_card_mastercard",
        pattern=r"\b5[1-5][0-9]{2}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
        category=PatternCategory.PII,
        label="CREDIT_CARD_NUMBER",
        description="Mastercard credit card numbers"
    ),
    RegexPattern(
        name="credit_card_amex",
        pattern=r"\b3[47][0-9]{2}[-\s]?\d{6}[-\s]?\d{5}\b",
        category=PatternCategory.PII,
        label="CREDIT_CARD_NUMBER",
        description="American Express credit card numbers"
    ),
    
    # IP Addresses
    RegexPattern(
        name="ipv4_address",
        pattern=r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
        category=PatternCategory.PII,
        label="IP_ADDRESS",
        description="IPv4 addresses"
    ),
    RegexPattern(
        name="ipv6_address",
        pattern=r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b",
        category=PatternCategory.PII,
        label="IP_ADDRESS",
        description="IPv6 addresses"
    ),
    
    # Dates of Birth (potential PII when combined with other data)
    RegexPattern(
        name="date_of_birth",
        pattern=r"\b(?:DOB|Date of Birth)[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b",
        category=PatternCategory.PII,
        label="DATE_OF_BIRTH",
        description="Explicit date of birth fields"
    ),
    
    # Driver's License (US patterns vary by state)
    RegexPattern(
        name="drivers_license",
        pattern=r"(?:DL|Driver'?s?\s*License|Lic\.?)\s*[:#]?\s*[A-Z0-9]{7,15}",
        category=PatternCategory.PII,
        label="DRIVERS_LICENSE",
        description="Driver's license numbers"
    ),
    
    # Passport Numbers (generic pattern)
    RegexPattern(
        name="passport_number",
        pattern=r"(?:Passport|P/No\.?)\s*[:#]?\s*[A-Z0-9]{6,12}",
        category=PatternCategory.PII,
        label="PASSPORT_NUMBER",
        description="Passport numbers"
    ),
    
    # Medical Record Numbers
    RegexPattern(
        name="medical_record",
        pattern=r"(?:MRN|Medical\s*Record|Patient\s*ID)\s*[:#]?\s*[A-Z0-9]{6,15}",
        category=PatternCategory.PII,
        label="MEDICAL_RECORD_NUMBER",
        description="Medical record numbers"
    ),
]

# ============================================================================
# FINANCIAL IDENTIFIERS
# ============================================================================

FINANCIAL_PATTERNS = [
    # Stock Tickers
    RegexPattern(
        name="stock_ticker_nyse",
        pattern=r"\b[NY]SE:\s*[A-Z]{1,4}\b",
        category=PatternCategory.FINANCIAL,
        label="STOCK_TICKER",
        description="NYSE stock tickers"
    ),
    RegexPattern(
        name="stock_ticker_nasdaq",
        pattern=r"\bNASDAQ:\s*[A-Z]{3,5}\b",
        category=PatternCategory.FINANCIAL,
        label="STOCK_TICKER",
        description="NASDAQ stock tickers"
    ),
    RegexPattern(
        name="stock_ticker_plain",
        pattern=r"\b[A-Z]{1,5}\s+(?:Inc\.?|Corp\.?|Ltd\.?|LLC)\b",
        category=PatternCategory.FINANCIAL,
        label="COMPANY_NAME",
        description="Company names with ticker-like patterns"
    ),
    
    # CUSIP (Committee on Uniform Security Identification Procedures)
    RegexPattern(
        name="cusip",
        pattern=r"\b[0-9]{3}[A-Z0-9]{6}\b",
        category=PatternCategory.FINANCIAL,
        label="CUSIP",
        description="CUSIP identifiers (9 chars)"
    ),
    
    # ISIN (International Securities Identification Number)
    RegexPattern(
        name="isin",
        pattern=r"\b[A-Z]{2}[A-Z0-9]{9}\d\b",
        category=PatternCategory.FINANCIAL,
        label="ISIN",
        description="ISIN identifiers (12 chars)"
    ),
    
    # LEI (Legal Entity Identifier)
    RegexPattern(
        name="lei",
        pattern=r"\b[A-Z0-9]{20}\b",
        category=PatternCategory.FINANCIAL,
        label="LEI",
        description="Legal Entity Identifiers (20 chars)"
    ),
    
    # Currency Amounts
    RegexPattern(
        name="currency_usd",
        pattern=r"\$[\d,]+(?:\.\d{2})?(?:\s*(?:million|billion|trillion))?\b",
        category=PatternCategory.FINANCIAL,
        label="MONEY",
        description="USD currency amounts"
    ),
    RegexPattern(
        name="currency_eur",
        pattern=r"€[\d,]+(?:\.\d{2})?(?:\s*(?:million|billion|trillion))?\b",
        category=PatternCategory.FINANCIAL,
        label="MONEY",
        description="EUR currency amounts"
    ),
    RegexPattern(
        name="currency_gbp",
        pattern=r"£[\d,]+(?:\.\d{2})?(?:\s*(?:million|billion|trillion))?\b",
        category=PatternCategory.FINANCIAL,
        label="MONEY",
        description="GBP currency amounts"
    ),
]

# ============================================================================
# TECHNICAL IDENTIFIERS
# ============================================================================

TECHNICAL_PATTERNS = [
    # URLs
    RegexPattern(
        name="url_http",
        pattern=r"https?://[^\s<>\"]+|www\.[^\s<>\"]+",
        category=PatternCategory.TECHNICAL,
        label="URL",
        description="HTTP/HTTPS URLs"
    ),
    
    # File Hashes
    RegexPattern(
        name="md5_hash",
        pattern=r"\b[a-fA-F0-9]{32}\b",
        category=PatternCategory.TECHNICAL,
        label="FILE_HASH",
        description="MD5 file hashes"
    ),
    RegexPattern(
        name="sha1_hash",
        pattern=r"\b[a-fA-F0-9]{40}\b",
        category=PatternCategory.TECHNICAL,
        label="FILE_HASH",
        description="SHA-1 file hashes"
    ),
    RegexPattern(
        name="sha256_hash",
        pattern=r"\b[a-fA-F0-9]{64}\b",
        category=PatternCategory.TECHNICAL,
        label="FILE_HASH",
        description="SHA-256 file hashes"
    ),
    
    # UUIDs
    RegexPattern(
        name="uuid",
        pattern=r"\b[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}\b",
        category=PatternCategory.TECHNICAL,
        label="UUID",
        description="UUID/GUID identifiers"
    ),
    
    # Software Versions
    RegexPattern(
        name="semver",
        pattern=r"\bv?\d+\.\d+\.\d+(?:-[a-zA-Z0-9]+)?(?:\+[a-zA-Z0-9]+)?\b",
        category=PatternCategory.TECHNICAL,
        label="SOFTWARE_VERSION",
        description="Semantic version numbers"
    ),
]

# ============================================================================
# GENERAL PATTERNS
# ============================================================================

GENERAL_PATTERNS = [
    # Street Addresses (US patterns)
    RegexPattern(
        name="street_address",
        pattern=r"\d+\s+[A-Z][a-zA-Z]+\s+(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Lane|Ln\.?|Court|Ct\.?|Place|Pl\.?)[,\s]",
        category=PatternCategory.GENERAL,
        label="STREET_ADDRESS",
        description="US street addresses"
    ),
    
    # ZIP Codes
    RegexPattern(
        name="zip_code",
        pattern=r"\b\d{5}(?:-\d{4})?\b",
        category=PatternCategory.GENERAL,
        label="POSTAL_CODE",
        description="US ZIP codes"
    ),
]

# ============================================================================
# COMPILE ALL PATTERNS
# ============================================================================

ALL_PATTERNS = LEGAL_PATTERNS + PII_PATTERNS + FINANCIAL_PATTERNS + TECHNICAL_PATTERNS + GENERAL_PATTERNS

# Index patterns by category for efficient filtering
PATTERNS_BY_CATEGORY: dict[PatternCategory, list[RegexPattern]] = {}
for pattern in ALL_PATTERNS:
    if pattern.category not in PATTERNS_BY_CATEGORY:
        PATTERNS_BY_CATEGORY[pattern.category] = []
    PATTERNS_BY_CATEGORY[pattern.category].append(pattern)

# Index patterns by label
PATTERNS_BY_LABEL: dict[str, list[RegexPattern]] = {}
for pattern in ALL_PATTERNS:
    if pattern.label not in PATTERNS_BY_LABEL:
        PATTERNS_BY_LABEL[pattern.label] = []
    PATTERNS_BY_LABEL[pattern.label].append(pattern)


def find_all_patterns(text: str, categories: list[PatternCategory] | None = None) -> list[dict]:
    """Find all regex pattern matches in text.
    
    Args:
        text: Input text to search
        categories: Optional list of categories to search. If None, searches all.
    
    Returns:
        List of match dictionaries with label, text, start, end, confidence, source
    """
    results = []
    
    if categories is None:
        categories = list(PatternCategory)
    
    for category in categories:
        for pattern in PATTERNS_BY_CATEGORY.get(category, []):
            matches = pattern.find_all(text)
            results.extend(matches)
    
    # Sort by start position
    results.sort(key=lambda x: x["start"])
    
    return results


def find_pii(text: str) -> list[dict]:
    """Find all PII patterns in text."""
    return find_all_patterns(text, categories=[PatternCategory.PII])


def find_legal_citations(text: str) -> list[dict]:
    """Find all legal citation patterns in text."""
    return find_all_patterns(text, categories=[PatternCategory.LEGAL])


def redact_pii(text: str, replacements: dict[str, str] | None = None) -> tuple[str, list[dict]]:
    """Redact PII from text with optional custom replacements.
    
    Args:
        text: Input text
        replacements: Optional dict mapping labels to replacement strings.
                     Defaults to [REDACTED-{label}] format.
    
    Returns:
        Tuple of (redacted_text, list_of_redacted_spans)
    """
    if replacements is None:
        replacements = {}
    
    pii_matches = find_pii(text)
    
    if not pii_matches:
        return text, []
    
    # Process matches in reverse order to preserve offsets
    redacted_text = text
    redacted_spans = []
    
    for match in reversed(pii_matches):
        label = match["label"]
        start = match["start"]
        end = match["end"]
        original = match["text"]
        
        replacement = replacements.get(label, f"[REDACTED-{label}]")
        
        redacted_text = redacted_text[:start] + replacement + redacted_text[end:]
        
        redacted_spans.append({
            "label": label,
            "original": original,
            "replacement": replacement,
            "start": start,
            "end": end,
        })
    
    redacted_spans.reverse()
    
    return redacted_text, redacted_spans

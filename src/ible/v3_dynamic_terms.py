from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ible.integrity import canonical_sha256, load_json, write_json
from ible.v3_collectors import Period

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "in",
    "into", "is", "of", "on", "or", "the", "to", "with", "all", "new",
    "based", "using", "technology", "technologies", "system", "systems",
    "industry", "industries", "research", "development", "market", "global",
    "among", "billion", "fund", "funds", "report", "this", "why", "also", "can",
    "could", "would", "should", "their", "there", "these", "those", "than",
    "more", "most", "less", "well", "using", "based", "new",
    "both", "challenge", "framework", "however", "learning", "model", "performance",
    "potential", "structural", "structure", "study", "such", "between", "leading",
    "leverage", "limitation", "propose", "scale", "within", "large", "network",
    "property", "response", "approach", "array", "image", "molecule", "novel",
    "allow", "benchmark", "downstream", "exhibit", "fundamental", "level", "meaningful",
    "pose", "predicting", "prediction", "reliance", "representation", "semantic", "task",
    "technique", "training", "tuning", "upon", "pre", "we", "proposed", "show", "shows",
    "result", "results", "method", "methods", "data", "paper", "article", "first", "two",
    "multimodal", "neural", "transformer", "embedding", "algorithm", "architecture",
    "optimization", "inference", "dataset", "latent", "vision", "language", "text",
    "across", "single", "accelerated", "accelerate", "discovery", "general", "generic",
    "analysis", "experimental", "experiment", "evaluation", "efficient", "efficiency",
    "effective", "effect", "impact", "application", "applications", "future", "current",
    "high", "higher", "low", "lower", "real", "time", "multi", "multiple", "toward",
    "towards", "via", "different", "various", "improved", "improving", "novelty",
    "generation", "generative", "foundation", "foundationmodel", "benchmarking",
}

_DOMAIN_ANCHORS = {
    "ai", "robot", "robotic", "automation", "semiconductor", "chip", "memory", "hbm",
    "photonics", "optical", "quantum", "sensor", "lidar", "battery", "energy", "grid",
    "nuclear", "reactor", "fusion", "geothermal", "hydrogen", "carbon", "solar", "storage",
    "recycling", "material", "manufacturing", "additive", "biotech", "biology", "synthetic",
    "gene", "cell", "therapy", "oncology", "drug", "pharma", "protein", "diagnostic",
    "cybersecurity", "security", "identity", "satellite", "space", "drone", "defense",
    "logistics", "warehouse", "agriculture", "food", "water", "fintech", "payment",
    "infrastructure", "interconnect", "packaging", "compute", "computing", "edge",
}
_GENERIC_PHRASE_TOKENS = {
    "across", "single", "accelerated", "general", "generic", "novel", "new", "advanced",
    "efficient", "effective", "future", "current", "large", "small", "high", "low",
}
_TOKEN_ALIASES = {
    "chips": "semiconductor", "chip": "semiconductor", "semiconductors": "semiconductor",
    "drugs": "drug", "pharmaceutical": "drug", "pharmaceuticals": "drug",
    "robotics": "robot", "robots": "robot", "batteries": "battery",
    "photonic": "photonics", "computational": "computing", "compute": "computing",
}

_SHORT_TECH_TERMS = {"ai", "hbm", "llm", "gpu", "cpu", "eda", "ev", "3d"}


def _tokens(value: Any) -> list[str]:
    text = str(value or "").lower().replace("-", " ").replace("_", " ")
    result = []
    for token in _TOKEN_RE.findall(text):
        if token.endswith("ies") and len(token) > 5:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
            token = token[:-1]
        if (len(token) >= 4 or token in _SHORT_TECH_TERMS) and token not in _STOPWORDS:
            result.append(token)
    return result


def _canonical_token(token: str) -> str:
    return _TOKEN_ALIASES.get(str(token), str(token))


def _ngrams(tokens: list[str], minimum: int = 2, maximum: int = 4) -> Counter[str]:
    """Industry discovery uses phrases, not isolated generic words."""
    result: Counter[str] = Counter()
    upper = min(maximum, len(tokens))
    for size in range(max(1, minimum), upper + 1):
        for index in range(len(tokens) - size + 1):
            phrase_tokens = tokens[index:index + size]
            if all(token in _GENERIC_PHRASE_TOKENS for token in phrase_tokens):
                continue
            phrase = " ".join(phrase_tokens)
            if len(phrase) >= 6 or (len(phrase_tokens) == 1 and phrase_tokens[0] in _SHORT_TECH_TERMS):
                result[phrase] += 1
    return result


def _phrase_quality(term: str) -> float:
    tokens = term.split()
    if len(tokens) == 1:
        token = _canonical_token(tokens[0])
        return 72.0 if token in _SHORT_TECH_TERMS or token in _DOMAIN_ANCHORS else 0.0
    if len(tokens) < 2:
        return 0.0
    anchor_count = sum(_canonical_token(token) in _DOMAIN_ANCHORS for token in tokens)
    generic_count = sum(token in _GENERIC_PHRASE_TOKENS for token in tokens)
    score = 45.0
    score += min(30.0, anchor_count * 20.0)
    score += 10.0 if 2 <= len(tokens) <= 4 else 0.0
    score -= generic_count * 18.0
    if len(set(tokens)) != len(tokens):
        score -= 20.0
    return max(0.0, min(100.0, score))


def _normalized_set(values: set[str] | list[str]) -> set[str]:
    return {_canonical_token(token) for token in values if token}


def _theme_similarity(candidate: str, vocabulary: set[str]) -> float:
    candidate_tokens = _normalized_set(candidate.split())
    theme_tokens = _normalized_set(vocabulary)
    if not candidate_tokens or not theme_tokens:
        return 0.0
    intersection = len(candidate_tokens & theme_tokens)
    union = len(candidate_tokens | theme_tokens)
    jaccard = intersection / union if union else 0.0
    containment = intersection / len(candidate_tokens)
    return max(jaccard, containment * 0.9)


def _dedupe_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse near-duplicate phrases, preferring stronger and more specific phrases."""
    ordered = sorted(
        rows,
        key=lambda row: (
            -float(row.get("confidence") or 0.0),
            -float(row.get("phrase_quality_score") or 0.0),
            -len(str(row.get("term") or "").split()),
            str(row.get("term") or ""),
        ),
    )
    kept: list[dict[str, Any]] = []
    for row in ordered:
        tokens = set(str(row.get("term") or "").split())
        duplicate = False
        for existing in kept:
            other = set(str(existing.get("term") or "").split())
            if not tokens or not other:
                continue
            if len(tokens) == 1 or len(other) == 1:
                continue
            overlap = len(tokens & other) / max(1, min(len(tokens), len(other)))
            same_sources = set(row.get("evidence_sources") or []) == set(existing.get("evidence_sources") or [])
            if overlap >= 0.8 and same_sources:
                duplicate = True
                break
        if not duplicate:
            kept.append(row)
    return kept

def _theme_vocabulary(themes: list[dict[str, Any]]) -> dict[str, set[str]]:
    vocabulary: dict[str, set[str]] = {}
    for row in themes:
        values = [row.get("theme_id"), row.get("theme_name"), row.get("sector"), row.get("openalex_search"), row.get("gdelt_query")]
        values.extend(row.get("usaspending_keywords") or [])
        values.extend(row.get("dynamic_aliases") or [])
        vocabulary[str(row["theme_id"])] = set(_tokens(" ".join(str(value) for value in values if value)))
    return vocabulary


def _similarity(candidate: str, vocabulary: set[str]) -> float:
    return _theme_similarity(candidate, vocabulary)

def discover_candidates(
    documents: list[dict[str, Any]], themes: list[dict[str, Any]], as_of: str, *,
    min_documents: int = 2, min_source_families: int = 2, min_periods: int = 2,
    min_similarity: float = 0.15, max_candidates: int = 100,
    min_phrase_tokens: int = 1, max_phrase_tokens: int = 4,
    min_phrase_quality: float = 55.0, existing_theme_similarity: float = 0.55,
) -> dict[str, Any]:
    """Create review-only industry-phrase candidates from a local timestamped corpus."""
    vocabulary = _theme_vocabulary(themes)
    occurrences: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"documents": set(), "sources": set(), "periods": set()}
    )
    for document in documents:
        text = document.get("text") or document.get("title") or ""
        if not str(text).strip():
            continue
        document_id = str(document.get("document_id") or document.get("id") or canonical_sha256({"text": str(text)})[:16])
        source = str(document.get("source") or "unknown")
        captured = str(document.get("captured_at") or document.get("as_of") or as_of)[:10]
        try:
            period = date.fromisoformat(captured).strftime("%Y-%m")
        except ValueError:
            period = str(as_of)[:7]
        tokens = _tokens(text)
        for term in _ngrams(tokens, minimum=min_phrase_tokens, maximum=max_phrase_tokens):
            quality = _phrase_quality(term)
            if quality < min_phrase_quality:
                continue
            item = occurrences[term]
            item["documents"].add(document_id)
            item["sources"].add(source)
            item["periods"].add(period)

    candidates: list[dict[str, Any]] = []
    for term, evidence in occurrences.items():
        document_count, source_count, period_count = (len(evidence[key]) for key in ("documents", "sources", "periods"))
        if document_count < min_documents or source_count < min_source_families or period_count < min_periods:
            continue

        similarities = sorted(
            ((_theme_similarity(term, words), theme_id) for theme_id, words in vocabulary.items()),
            reverse=True,
        )
        best_similarity, best_theme_id = similarities[0] if similarities else (0.0, None)
        phrase_quality = _phrase_quality(term)

        evidence_confidence = 8.0 * min(document_count, 10) + 11.0 * min(source_count, 4) + 7.0 * min(period_count, 6)
        confidence = min(100.0, 0.65 * evidence_confidence + 0.35 * phrase_quality)

        # Sparse corpora must not look artificially certain.
        if document_count < 4:
            confidence = min(confidence, 72.0)
        elif document_count < 6:
            confidence = min(confidence, 82.0)
        if source_count < 3:
            confidence = min(confidence, 78.0)

        existing_extension = bool(best_theme_id) and best_similarity >= existing_theme_similarity
        candidates.append({
            "term": term,
            "display_name": " ".join(word.upper() if word in {"ai", "hbm", "llm", "gpu", "cpu", "eda", "ev"} else word.capitalize() for word in term.split()),
            "suggested_theme_id": best_theme_id,
            "semantic_similarity_proxy": round(best_similarity, 4),
            "phrase_quality_score": round(phrase_quality, 2),
            "confidence": round(confidence, 4),
            "distinct_document_count": document_count,
            "source_family_count": source_count,
            "period_count": period_count,
            "evidence_sources": sorted(evidence["sources"]),
            "evidence_periods": sorted(evidence["periods"]),
            "evidence_document_ids": sorted(evidence["documents"])[:8],
            "promotion_status": "EXISTING_THEME_EXTENSION" if existing_extension else "NEW_THEME_REVIEW",
        })

    candidates = _dedupe_candidate_rows(candidates)
    candidates.sort(
        key=lambda row: (
            row.get("promotion_status") != "NEW_THEME_REVIEW",
            -float(row.get("confidence") or 0.0),
            -float(row.get("phrase_quality_score") or 0.0),
            str(row.get("term") or ""),
        )
    )
    return {
        "schema_version": 2,
        "as_of": str(as_of),
        "status": "CANDIDATES_FOUND" if candidates else "NO_QUALIFIED_CANDIDATES",
        "candidate_count": min(len(candidates), max_candidates),
        "auto_add_allowed": False,
        "promotion_rule": {
            "min_distinct_document_count": min_documents,
            "min_source_family_count": min_source_families,
            "min_period_count": min_periods,
            "min_similarity": min_similarity,
            "min_phrase_tokens": min_phrase_tokens,
            "max_phrase_tokens": max_phrase_tokens,
            "min_phrase_quality": min_phrase_quality,
            "existing_theme_similarity": existing_theme_similarity,
            "requires_human_review": True,
        },
        "candidates": candidates[:max_candidates],
    }


def build_dynamic_discovery_report(root: Path, themes: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    config_path = root / "config/v3_dynamic_discovery.json"
    config = load_json(config_path) if config_path.is_file() else {}
    inbox = root / str(config.get("local_text_corpus", "data_cache/inbox/v3_text_documents.json"))
    documents: list[dict[str, Any]] = []
    if inbox.is_file():
        payload = load_json(inbox)
        documents = payload if isinstance(payload, list) else list((payload or {}).get("documents") or [])
    if not documents:
        return {"schema_version": 1, "as_of": str(as_of), "status": "WAITING_FOR_LOCAL_TEXT_CORPUS", "candidate_count": 0, "auto_add_allowed": False, "input_path": str(inbox.relative_to(root)), "promotion_rule": config.get("promotion_rule") or {}, "candidates": []}
    report = discover_candidates(documents, themes, as_of, **(config.get("promotion_rule") or {}))
    report["input_path"] = str(inbox.relative_to(root))
    report["input_document_count"] = len(documents)
    return report


def collect_dynamic_documents(
    openalex: Any, gdelt: Any, themes: list[dict[str, Any]], as_of: str, arxiv: Any = None,
    *, max_theme_queries: int = 5, documents_per_source: int = 5, lookback_days: int = 90,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect a small, cached text sample for frontier-term discovery."""
    end = date.fromisoformat(str(as_of)[:10])
    period = Period(end - timedelta(days=max(1, lookback_days) - 1), end)
    selected = sorted(themes, key=lambda row: (int(row.get("data_build_priority", 99)), str(row.get("theme_id"))))[:max(0, int(max_theme_queries))]
    documents: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for row in selected:
        theme_id = str(row.get("theme_id") or "")
        if openalex:
            try:
                documents.extend(openalex.documents(str(row.get("openalex_search") or ""), period, documents_per_source))
            except Exception as exc:
                errors.append({"theme_id": theme_id, "source": "openalex", "error": str(exc)[:300]})
        if arxiv:
            try:
                documents.extend(arxiv.documents(str(row.get("openalex_search") or ""), period, documents_per_source))
            except Exception as exc:
                errors.append({"theme_id": theme_id, "source": "arxiv", "error": str(exc)[:300]})
    if len({str(document.get("source")) for document in documents}) < 2 and gdelt:
        for row in selected:
            theme_id = str(row.get("theme_id") or "")
            try:
                documents.extend(gdelt.documents(str(row.get("gdelt_query") or ""), period, documents_per_source))
            except Exception as exc:
                errors.append({"theme_id": theme_id, "source": "gdelt", "error": str(exc)[:300]})
    deduped = {}
    for document in documents:
        key = (str(document.get("source")), str(document.get("document_id")))
        deduped[key] = document
    return list(deduped.values()), {
        "status": "LIVE_OR_CACHED_TEXT_SAMPLE" if deduped else "TEXT_SAMPLE_UNAVAILABLE",
        "selected_theme_count": len(selected),
        "document_count": len(deduped),
        "errors": errors,
        "lookback_days": lookback_days,
    }


def write_dynamic_discovery_report(root: Path, output_dir: Path, report: dict[str, Any]) -> None:
    report = dict(report)
    report["content_sha256"] = canonical_sha256(report)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v3_dynamic_theme_candidates.json", report)
    write_json(root / "data_cache/latest/v3_dynamic_theme_candidates.json", report)

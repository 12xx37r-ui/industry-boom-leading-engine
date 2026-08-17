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


def _ngrams(tokens: list[str], maximum: int = 3) -> Counter[str]:
    result: Counter[str] = Counter()
    for size in range(1, min(maximum, len(tokens)) + 1):
        for index in range(len(tokens) - size + 1):
            phrase = " ".join(tokens[index:index + size])
            if len(phrase) >= 3:
                result[phrase] += 1
    return result


def _theme_vocabulary(themes: list[dict[str, Any]]) -> dict[str, set[str]]:
    vocabulary: dict[str, set[str]] = {}
    for row in themes:
        values = [row.get("theme_name"), row.get("openalex_search"), row.get("gdelt_query")]
        values.extend(row.get("usaspending_keywords") or [])
        values.extend(row.get("dynamic_aliases") or [])
        vocabulary[str(row["theme_id"])] = set(_tokens(" ".join(str(value) for value in values if value)))
    return vocabulary


def _similarity(candidate: str, vocabulary: set[str]) -> float:
    candidate_tokens = set(candidate.split())
    if not candidate_tokens or not vocabulary:
        return 0.0
    return len(candidate_tokens & vocabulary) / len(candidate_tokens | vocabulary)


def discover_candidates(
    documents: list[dict[str, Any]], themes: list[dict[str, Any]], as_of: str, *,
    min_documents: int = 2, min_source_families: int = 2, min_periods: int = 2,
    min_similarity: float = 0.15, max_candidates: int = 100,
) -> dict[str, Any]:
    """Create review-only new-term candidates from a local, timestamped corpus."""
    vocabulary = _theme_vocabulary(themes)
    known_terms = set().union(*vocabulary.values()) if vocabulary else set()
    occurrences: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"documents": set(), "sources": set(), "periods": set()})
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
        for term in _ngrams(_tokens(text)):
            if term in known_terms:
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
        term_tokens = term.split()
        known_flags = [token in known_terms for token in term_tokens]
        if any(known_flags):
            continue
        matches = sorted(((score, theme_id) for theme_id, words in vocabulary.items() if (score := _similarity(term, words)) >= min_similarity), reverse=True)
        best_similarity, best_theme_id = matches[0] if matches else (0.0, None)
        evidence_confidence = 10.0 * min(document_count, 10) + 10.0 * source_count + 8.0 * period_count
        confidence = min(100.0, evidence_confidence + 30.0 * best_similarity)
        if best_theme_id is None:
            confidence = min(75.0, evidence_confidence)
        candidates.append({
            "term": term, "suggested_theme_id": best_theme_id,
            "semantic_similarity_proxy": round(best_similarity, 4), "confidence": round(confidence, 4),
            "distinct_document_count": document_count, "source_family_count": source_count,
            "period_count": period_count,
            "evidence_sources": sorted(evidence["sources"]),
            "evidence_periods": sorted(evidence["periods"]),
            "evidence_document_ids": sorted(evidence["documents"])[:8],
            "promotion_status": "NEW_THEME_REVIEW" if best_theme_id is None else "REVIEW_REQUIRED",
        })
    candidates.sort(key=lambda row: (-row["confidence"], row["term"]))
    return {
        "schema_version": 1, "as_of": str(as_of),
        "status": "CANDIDATES_FOUND" if candidates else "NO_QUALIFIED_CANDIDATES",
        "candidate_count": min(len(candidates), max_candidates), "auto_add_allowed": False,
        "promotion_rule": {"min_distinct_document_count": min_documents, "min_source_family_count": min_source_families, "min_period_count": min_periods, "min_similarity": min_similarity, "requires_human_review": True},
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

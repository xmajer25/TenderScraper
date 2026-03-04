from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse

# Reuse your existing config if available.
# If this import fails, replace with a plain Path("data/tenders") fallback.
from tenderscraper.config import settings

app = FastAPI(title="TenderScraper API", version="0.1.0")


# ----------------------------
# Storage + Indexing
# ----------------------------

@dataclass(frozen=True)
class TenderRef:
    source: str
    tender_id: str  # your folder key: tender=<id>
    meta_path: Path


class TenderIndex:
    """
    In-memory index to avoid scanning the filesystem on every request.
    """
    def __init__(self) -> None:
        self._by_key: Dict[Tuple[str, str], TenderRef] = {}
        self._sources: Dict[str, List[TenderRef]] = {}

    def clear(self) -> None:
        self._by_key.clear()
        self._sources.clear()

    def build(self, tenders_dir: Path) -> None:
        self.clear()

        # Expected layout:
        # data/tenders/source=<source>/tender=<tender_id>/meta.json
        if not tenders_dir.exists():
            return

        for meta_path in tenders_dir.rglob("meta.json"):
            # meta_path: .../source=XYZ/tender=ABC/meta.json
            try:
                tender_dir = meta_path.parent
                source_dir = tender_dir.parent

                source = source_dir.name.removeprefix("source=")
                tender_id = tender_dir.name.removeprefix("tender=")

                if not source or not tender_id:
                    continue

                ref = TenderRef(source=source, tender_id=tender_id, meta_path=meta_path)
                self._by_key[(source, tender_id)] = ref
                self._sources.setdefault(source, []).append(ref)
            except Exception:
                continue

        # Stable ordering: newest first if we can read _ingested_at, else by path name
        for src, refs in self._sources.items():
            refs.sort(key=lambda r: _safe_ingested_at(r.meta_path) or "", reverse=True)

    def get(self, source: str, tender_id: str) -> Optional[TenderRef]:
        return self._by_key.get((source, tender_id))

    def sources(self) -> List[str]:
        return sorted(self._sources.keys())

    def list_refs(self, source: Optional[str] = None) -> List[TenderRef]:
        if source:
            return list(self._sources.get(source, []))
        # flatten
        out: List[TenderRef] = []
        for refs in self._sources.values():
            out.extend(refs)
        # global sort newest first (best-effort)
        out.sort(key=lambda r: _safe_ingested_at(r.meta_path) or "", reverse=True)
        return out


INDEX = TenderIndex()


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read meta.json: {path}") from e
    
def _tenders_root() -> Path:
    # Ensure dirs exist; avoids weird “works on my machine” failures.
    settings.ensure_dirs()
    return settings.tenders_dir
    
def _iter_meta_paths(*, source: Optional[str] = None) -> List[Path]:
    root = _tenders_root()
    if source:
        pattern = f"source={source}/tender=*/meta.json"
    else:
        pattern = "source=*/tender=*/meta.json"
    return sorted(root.glob(pattern))


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read {path}") from e


def _available_sources() -> List[str]:
    root = _tenders_root()
    out = []
    for p in root.glob("source=*"):
        if p.is_dir():
            out.append(p.name.split("source=", 1)[-1])
    return sorted(set(out))


def _safe_ingested_at(meta_path: Path) -> Optional[str]:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        v = meta.get("_ingested_at")
        return v if isinstance(v, str) else None
    except Exception:
        return None


def _matches_q(meta: Dict[str, Any], q: str) -> bool:
    ql = q.strip().lower()
    if not ql:
        return True
    title = (meta.get("title") or "")
    desc = (meta.get("description") or "")
    return ql in str(title).lower() or ql in str(desc).lower()


def _summary(meta: Dict[str, Any]) -> Dict[str, Any]:
    # Don’t return huge blobs by default in list endpoints.
    return {
        "source": meta.get("source"),
        "source_tender_id": meta.get("source_tender_id"),
        "tender_key": meta.get("tender_key"),  # if present
        "title": meta.get("title"),
        "buyer": meta.get("buyer"),
        "buyer_ico": meta.get("buyer_ico"),
        "submission_deadline_at": meta.get("submission_deadline_at"),
        "bids_opening_at": meta.get("bids_opening_at"),
        "notice_url": meta.get("notice_url"),
        "documents_count": len(meta.get("documents") or []),
        "_ingested_at": meta.get("_ingested_at"),
    }


def _doc_listing(meta: Dict[str, Any], source: str, tender_id: str) -> List[Dict[str, Any]]:
    docs = meta.get("documents") or []
    out: List[Dict[str, Any]] = []
    for i, d in enumerate(docs):
        local_path = d.get("local_path")
        out.append(
            {
                "index": i,
                "filename": d.get("filename"),
                "mime_type": d.get("mime_type"),
                "size_bytes": d.get("size_bytes"),
                "sha256": d.get("sha256"),
                "downloaded_at": d.get("downloaded_at"),
                "url": d.get("url"),
                "local_path": local_path,  # relative path like "raw/..."
                "has_local_file": bool(local_path),
                "download_endpoint": f"/tenders/{source}/{tender_id}/documents/{i}",
            }
        )
    return out


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    INDEX.build(settings.tenders_dir)


# ----------------------------
# Endpoints
# ----------------------------

@app.get("/")
def root() -> dict:
    # Do not return 404. Be helpful.
    return {
        "service": app.title,
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "list_sources": "/sources",
            "list_all_tenders": "/tenders",
            "list_tenders_by_source": "/tenders/{source}",
            "get_tender": "/tenders/{source}/{tender_id}",
        },
    }

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/sources")
def list_sources() -> Dict[str, Any]:
    return {"sources": INDEX.sources()}


@app.get("/tenders")
def list_tenders(
    source: Optional[str] = Query(None, description="Filter by source key, e.g. poptavej"),
    q: Optional[str] = Query(None, description="Substring search in title/description"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    refs = INDEX.list_refs(source=source)

    # filter by q (requires reading meta; acceptable for demo; optimize later if needed)
    if q:
        filtered: List[TenderRef] = []
        for r in refs:
            meta = _read_json(r.meta_path)
            if _matches_q(meta, q):
                filtered.append(r)
        refs = filtered

    total = len(refs)
    page = refs[offset : offset + limit]

    items: List[Dict[str, Any]] = []
    for r in page:
        meta = _read_json(r.meta_path)
        items.append(
            {
                **_summary(meta),
                "id": r.tender_id,  # folder key you can use in path
                "detail_endpoint": f"/tenders/{r.source}/{r.tender_id}",
            }
        )

    return {"total": total, "offset": offset, "limit": limit, "items": items}


@app.get("/tenders/{source}")
def list_tenders_by_source(
    source: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    if source not in _available_sources():
        raise HTTPException(status_code=404, detail=f"Unknown source '{source}'")

    paths = _iter_meta_paths(source=source)
    slice_paths = paths[offset : offset + limit]
    items = [_load_json(p) for p in slice_paths]
    return {"source": source, "total": len(paths), "limit": limit, "offset": offset, "items": items}



@app.get("/tenders/{source}/{tender_id}")
def get_tender(source: str, tender_id: str) -> Dict[str, Any]:
    ref = INDEX.get(source, tender_id)
    if not ref:
        raise HTTPException(status_code=404, detail="Tender not found")
    meta = _read_json(ref.meta_path)
    # include docs listing helper endpoints
    meta["documents_api"] = f"/tenders/{source}/{tender_id}/documents"
    return meta


@app.get("/tenders/{source}/{tender_id}/documents")
def list_documents(source: str, tender_id: str) -> Dict[str, Any]:
    ref = INDEX.get(source, tender_id)
    if not ref:
        raise HTTPException(status_code=404, detail="Tender not found")

    meta = _read_json(ref.meta_path)
    return {"source": source, "tender_id": tender_id, "documents": _doc_listing(meta, source, tender_id)}


@app.get("/tenders/{source}/{tender_id}/documents/{doc_index}")
def get_document(source: str, tender_id: str, doc_index: int) -> Any:
    ref = INDEX.get(source, tender_id)
    if not ref:
        raise HTTPException(status_code=404, detail="Tender not found")

    meta = _read_json(ref.meta_path)
    docs = meta.get("documents") or []
    if doc_index < 0 or doc_index >= len(docs):
        raise HTTPException(status_code=404, detail="Document not found")

    d = docs[doc_index]
    filename = d.get("filename") or f"document_{doc_index}"

    # Prefer local file if present
    local_rel = d.get("local_path")
    if isinstance(local_rel, str) and local_rel:
        # local_rel stored like "raw/xyz.docx"
        local_abs = ref.meta_path.parent / local_rel
        if local_abs.exists():
            return FileResponse(
                path=str(local_abs),
                filename=filename,
                media_type=d.get("mime_type") or "application/octet-stream",
            )

    # Otherwise redirect to remote URL
    url = d.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return RedirectResponse(url=url, status_code=302)

    raise HTTPException(status_code=404, detail="Document file/url not available")


# Optional: useful in demo/dev when ingestion runs while API is up
@app.post("/admin/reload")
def reload_index() -> Dict[str, Any]:
    INDEX.build(settings.tenders_dir)
    return {"status": "reloaded", "sources": INDEX.sources()}

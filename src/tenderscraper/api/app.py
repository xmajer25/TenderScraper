from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

from tenderscraper.config import settings
from tenderscraper.db import create_db_and_tables, ping_database
from tenderscraper.repository import get_tender_meta, get_winner_tender_count, list_distinct_winners
from tenderscraper.repository import list_sources as repo_list_sources
from tenderscraper.repository import list_tenders as repo_list_tenders
from tenderscraper.storage.object_store import generate_download_url

app = FastAPI(title="TenderScraper API", version="0.2.0")


def _summary(meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": meta.get("source"),
        "source_tender_id": meta.get("source_tender_id"),
        "tender_key": meta.get("tender_key"),
        "title": meta.get("title"),
        "buyer": meta.get("buyer"),
        "buyer_ico": meta.get("buyer_ico"),
        "original_url": meta.get("original_url"),
        "winner_name": meta.get("winner_name"),
        "winner_ic": meta.get("winner_ic"),
        "submission_deadline_at": meta.get("submission_deadline_at"),
        "bids_opening_at": meta.get("bids_opening_at"),
        "notice_url": meta.get("notice_url"),
        "documents_count": len(meta.get("documents") or []),
        "_ingested_at": meta.get("_ingested_at"),
    }


def _doc_download_endpoint(source: str, tender_id: str, doc_index: int) -> str:
    return f"/tenders/{source}/{tender_id}/documents/{doc_index}"


def _doc_public_payload(
    document: Dict[str, Any],
    *,
    source: str,
    tender_id: str,
    doc_index: int,
) -> Dict[str, Any]:
    download_url = _doc_download_endpoint(source, tender_id, doc_index)
    source_url = document.get("url")
    return {
        **document,
        "source_url": source_url,
        "url": download_url,
        "download_url": download_url,
        "has_storage_object": bool(document.get("storage_key") or document.get("storage_url")),
    }


def _public_meta(meta: Dict[str, Any], source: str, tender_id: str) -> Dict[str, Any]:
    out = dict(meta)
    docs = meta.get("documents") or []
    out["documents"] = [
        _doc_public_payload(document=dict(document), source=source, tender_id=tender_id, doc_index=i)
        for i, document in enumerate(docs)
    ]
    out["documents_api"] = f"/tenders/{source}/{tender_id}/documents"
    return out


def _doc_listing(meta: Dict[str, Any], source: str, tender_id: str) -> List[Dict[str, Any]]:
    docs = meta.get("documents") or []
    out: List[Dict[str, Any]] = []
    for i, d in enumerate(docs):
        download_url = _doc_download_endpoint(source, tender_id, i)
        out.append(
            {
                "index": i,
                "filename": d.get("filename"),
                "mime_type": d.get("mime_type"),
                "size_bytes": d.get("size_bytes"),
                "sha256": d.get("sha256"),
                "downloaded_at": d.get("downloaded_at"),
                "url": download_url,
                "source_url": d.get("url"),
                "storage_key": d.get("storage_key"),
                "storage_url": d.get("storage_url"),
                "has_storage_object": bool(d.get("storage_key") or d.get("storage_url")),
                "download_endpoint": download_url,
                "download_url": download_url,
            }
        )
    return out


def _get_meta_or_404(source: str, tender_id: str) -> Dict[str, Any]:
    meta = get_tender_meta(source, tender_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Tender not found")
    return meta


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    create_db_and_tables()


@app.get("/")
def root() -> dict:
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
            "distinct_winners": "/distinct_winners",
            "winner_tender_count": "/distinct_winners/{winner}/tender_count",
        },
    }


@app.get("/health")
def health() -> dict:
    try:
        ping_database()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    return {"status": "ok", "database": "ok"}


@app.get("/sources")
def list_sources() -> Dict[str, Any]:
    return {"sources": repo_list_sources()}


@app.get("/distinct_winners")
@app.get("/winners")
def distinct_winners(
    source: Optional[str] = Query("poptavej", description="Filter by source key, defaults to poptavej"),
    q: Optional[str] = Query(None, description="Substring search in winner name or IC"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    total, items = list_distinct_winners(source=source, q=q, offset=offset, limit=limit)
    return {
        "source": source,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }


@app.get("/distinct_winners/{winner}/tender_count")
@app.get("/winners/{winner}/tender_count")
def winner_tender_count(
    winner: str,
    source: Optional[str] = Query("poptavej", description="Filter by source key, defaults to poptavej"),
) -> Dict[str, Any]:
    payload = get_winner_tender_count(winner=winner, source=source)
    if not payload:
        raise HTTPException(status_code=404, detail=f"Winner '{winner}' not found")
    return {"source": source, **payload}


@app.get("/tenders")
def list_tenders(
    source: Optional[str] = Query(None, description="Filter by source key, e.g. poptavej"),
    q: Optional[str] = Query(None, description="Substring search in title/description"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    total, items = repo_list_tenders(source=source, q=q, offset=offset, limit=limit)
    payload = []
    for meta in items:
        payload.append(
            {
                **_summary(meta),
                "id": meta.get("source_tender_id"),
                "detail_endpoint": f"/tenders/{meta.get('source')}/{meta.get('source_tender_id')}",
            }
        )
    return {"total": total, "offset": offset, "limit": limit, "items": payload}


@app.get("/tenders/{source}")
def list_tenders_by_source(
    source: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    total, items = repo_list_tenders(source=source, offset=offset, limit=limit)
    if total == 0:
        raise HTTPException(status_code=404, detail=f"Unknown source '{source}'")
    return {
        "source": source,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            _public_meta(meta, source=source, tender_id=str(meta.get("source_tender_id") or ""))
            for meta in items
        ],
    }


@app.get("/tenders/{source}/{tender_id}")
def get_tender(source: str, tender_id: str) -> Dict[str, Any]:
    meta = _get_meta_or_404(source, tender_id)
    return _public_meta(meta, source=source, tender_id=tender_id)


@app.get("/tenders/{source}/{tender_id}/documents")
def list_documents(source: str, tender_id: str) -> Dict[str, Any]:
    meta = _get_meta_or_404(source, tender_id)
    return {"source": source, "tender_id": tender_id, "documents": _doc_listing(meta, source, tender_id)}


@app.get("/tenders/{source}/{tender_id}/documents/{doc_index}")
def get_document(source: str, tender_id: str, doc_index: int) -> Any:
    meta = _get_meta_or_404(source, tender_id)
    docs = meta.get("documents") or []
    if doc_index < 0 or doc_index >= len(docs):
        raise HTTPException(status_code=404, detail="Document not found")

    document = docs[doc_index]
    filename = document.get("filename") or f"document_{doc_index}"

    storage_key = document.get("storage_key")
    if isinstance(storage_key, str) and storage_key and settings.uses_s3_storage:
        return RedirectResponse(url=generate_download_url(storage_key), status_code=302)

    storage_url = document.get("storage_url")
    if isinstance(storage_url, str) and storage_url.startswith(("http://", "https://")):
        return RedirectResponse(url=storage_url, status_code=302)

    source_url = document.get("url")
    if isinstance(source_url, str) and source_url.startswith(("http://", "https://")):
        return RedirectResponse(url=source_url, status_code=302)

    raise HTTPException(status_code=404, detail="Document file/url not available")


@app.post("/admin/reload")
def reload_index() -> Dict[str, Any]:
    return {"status": "reloaded", "sources": repo_list_sources()}

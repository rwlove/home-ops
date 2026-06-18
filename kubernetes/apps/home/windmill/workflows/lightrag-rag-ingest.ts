// Cron: every 30 min.
//
// Streams modified Paperless documents into LightRAG (graph-RAG) so its
// knowledge graph covers the same corpus as the Qdrant vector pipeline
// (paperless-rag-ingest). Runs ALONGSIDE that flow, not replacing it —
// vector RAG and graph RAG over the same docs, for comparison.
//
// We push Paperless's OCR TEXT (doc.content), not the original PDF: the
// deployed LightRAG image parses PDFs with pypdf only (no Docling/OCR),
// whereas Paperless already OCR'd everything (tesseract). LightRAG chunks,
// embeds (bge-m3 on Spark), and runs LLM entity/relationship extraction
// itself — we only hand it text.
//
// LightRAG ingestion is async + LLM-heavy (extraction per doc on the
// Spark), so we CAP docs per run and let the cron iterate. This paces the
// initial backfill across many runs instead of flooding the pipeline.
//
// Per doc:
//   1. fetch OCR text from Paperless (doc.content)
//   2. on update, delete any existing LightRAG docs for this paperless id
//      (matched by file_path == "paperless:<id>") so re-ingest replaces
//      cleanly instead of orphaning the old content-hash doc
//   3. POST /documents/text { text, file_source: "paperless:<id>" }
//      (X-API-Key) — fire-and-forget; LightRAG processes in its pipeline
//
// Watermark: persisted in Windmill state (max Paperless `modified` seen).
// Identity: file_source "paperless:<id>" surfaces as file_path in
// LightRAG's doc status — the stable key for clean updates AND the
// tombstone sweep (lightrag-rag-tombstone).

import * as wmill from "npm:windmill-client@1.527.0";

const LIGHTRAG = Deno.env.get("LIGHTRAG_URL") ??
    "http://lightrag.ai.svc.cluster.local:9621";
const PAPERLESS = Deno.env.get("PAPERLESS_URL") ??
    "http://paperless.collab.svc.cluster.local:8000";
const PAGE_SIZE = 50;
const MAX_DOCS_PER_RUN = 20; // pace the LLM-heavy extraction pipeline
const MIN_TEXT_LEN = 50;
const MAX_DOC_CHARS = 500_000; // safety: refuse runaway docs
const SOURCE_PREFIX = "paperless:";
const EPOCH = "1970-01-01T00:00:00Z";

type PaperlessDoc = { id: number; title: string; content: string; modified: string };
type PaperlessList = { count: number; next: string | null; results: PaperlessDoc[] };
type DocStatus = { id: string; file_path: string };
type DocsStatusesResponse = { statuses: Record<string, DocStatus[]> };

export async function main() {
    const token = Deno.env.get("PAPERLESS_TOKEN");
    if (!token) throw new Error("PAPERLESS_TOKEN env not set");
    const apiKey = Deno.env.get("LIGHTRAG_API_KEY");
    if (!apiKey) throw new Error("LIGHTRAG_API_KEY env not set");

    const started = new Date().toISOString();
    const watermark = ((await wmill.getState()) as string | null) ?? EPOCH;
    const existing = await lightragSourceMap(apiKey); // "paperless:<id>" -> [docId]
    console.log(`[lightrag-ingest] watermark=${watermark} known_sources=${existing.size}`);

    let submitted = 0, replaced = 0, skipped = 0;
    let maxModified = watermark;
    let page = 1;
    let capped = false;

    outer:
    while (true) {
        const list = await paperlessList(token, watermark, page);
        if (list.results.length === 0) break;
        for (const doc of list.results) {
            if (submitted >= MAX_DOCS_PER_RUN) { capped = true; break outer; }
            const text = (doc.content ?? "").slice(0, MAX_DOC_CHARS).trim();
            const src = `${SOURCE_PREFIX}${doc.id}`;
            if (text.length < MIN_TEXT_LEN) {
                skipped++;
                if (doc.modified > maxModified) maxModified = doc.modified;
                continue;
            }
            const prior = existing.get(src);
            if (prior?.length) { await lightragDelete(apiKey, prior); replaced++; }
            await lightragInsert(apiKey, text, src);
            submitted++;
            if (doc.modified > maxModified) maxModified = doc.modified;
        }
        if (!list.next) break;
        page += 1;
    }

    // Advance the watermark only as far as the docs we actually decided this
    // run — `capped` means more remain; the next run resumes from here.
    await wmill.setState(maxModified);

    return {
        started,
        finished: new Date().toISOString(),
        watermark_in: watermark,
        watermark_out: maxModified,
        submitted,
        replaced,
        skipped,
        capped,
    };
}

async function lightragSourceMap(apiKey: string): Promise<Map<string, string[]>> {
    const r = await fetch(`${LIGHTRAG}/documents`, {
        headers: { "X-API-Key": apiKey },
        signal: AbortSignal.timeout(60_000),
    });
    if (!r.ok) throw new Error(`lightrag GET /documents ${r.status}: ${await r.text()}`);
    const body = (await r.json()) as DocsStatusesResponse;
    const map = new Map<string, string[]>();
    for (const docs of Object.values(body.statuses ?? {})) {
        for (const d of docs) {
            if (!d.file_path?.startsWith(SOURCE_PREFIX)) continue;
            const arr = map.get(d.file_path) ?? [];
            arr.push(d.id);
            map.set(d.file_path, arr);
        }
    }
    return map;
}

async function lightragInsert(apiKey: string, text: string, source: string): Promise<void> {
    const r = await fetch(`${LIGHTRAG}/documents/text`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ text, file_source: source }),
        signal: AbortSignal.timeout(60_000),
    });
    if (!r.ok) throw new Error(`lightrag insert ${source} ${r.status}: ${await r.text()}`);
}

async function lightragDelete(apiKey: string, docIds: string[]): Promise<void> {
    const r = await fetch(`${LIGHTRAG}/documents/delete_document`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ doc_ids: docIds, delete_llm_cache: true }),
        signal: AbortSignal.timeout(120_000),
    });
    // 404 = already gone; tolerate.
    if (!r.ok && r.status !== 404) {
        throw new Error(`lightrag delete ${JSON.stringify(docIds)} ${r.status}: ${await r.text()}`);
    }
}

async function paperlessList(token: string, modified_gt: string, page: number): Promise<PaperlessList> {
    const url = new URL(`${PAPERLESS}/api/documents/`);
    url.searchParams.set("page", String(page));
    url.searchParams.set("page_size", String(PAGE_SIZE));
    url.searchParams.set("ordering", "modified");
    url.searchParams.set("modified__gt", modified_gt);
    const r = await fetch(url, {
        headers: { Authorization: `Token ${token}` },
        signal: AbortSignal.timeout(60_000),
    });
    if (!r.ok) throw new Error(`paperless list ${r.status}: ${await r.text()}`);
    return (await r.json()) as PaperlessList;
}

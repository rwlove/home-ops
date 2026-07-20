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
// Watermark: persisted in Windmill state (max Paperless `modified` seen),
// forward-clamped to a LOOKBACK_DAYS floor so a stale/diverged trigger scope
// can't trigger wasteful refresh churn (see LOOKBACK_DAYS below).
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
// Forward-clamp floor (#4). wmill.getState() is scoped per trigger context:
// the schedule advances its own state to ~now every run, but a manual /
// webhook trigger can read a stale, months-old scope. A stale watermark
// makes EVERY already-ingested doc above it sort into the refresh path
// (delete + reinsert = wasteful 80B re-extraction on the shared GB10, up to
// the per-run cap, on every such invocation). Clamp any watermark older than
// this many days forward to the floor so a stale/diverged read can only ever
// touch a small recent window. Inert for the schedule (its state is ~now).
const LOOKBACK_DAYS = 14;

type PaperlessDoc = { id: number; title: string; content: string; modified: string };
type PaperlessList = { count: number; next: string | null; results: PaperlessDoc[] };
type DocStatus = { id: string; file_path: string };
type DocsStatusesResponse = { statuses: Record<string, DocStatus[]> };
type PipelineStatus = { busy?: boolean; request_pending?: boolean; destructive_busy?: boolean };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function main() {
    const token = Deno.env.get("PAPERLESS_TOKEN");
    if (!token) throw new Error("PAPERLESS_TOKEN env not set");
    const apiKey = Deno.env.get("LIGHTRAG_API_KEY");
    if (!apiKey) throw new Error("LIGHTRAG_API_KEY env not set");

    const started = new Date().toISOString();
    const stored = ((await wmill.getState()) as string | null) ?? EPOCH;
    const floor = new Date(Date.now() - LOOKBACK_DAYS * 86_400_000).toISOString();
    const watermark = stored > floor ? stored : floor;
    if (watermark !== stored) {
        console.warn(
            `[lightrag-ingest] stored watermark ${stored} older than ${LOOKBACK_DAYS}d floor — clamping to ${floor} (stale/diverged trigger state)`,
        );
    }

    // Lazy fetch (#2): pull the first candidate page BEFORE the O(corpus)
    // GET /documents. Idle runs (nothing modified past the watermark) return
    // here without ever building the source map — the common case once the
    // backfill is done. The map is only worth its full-corpus fetch when
    // there is at least one doc to reconcile against it.
    const firstPage = await paperlessList(token, watermark, 1);
    if (firstPage.results.length === 0) {
        console.log(`[lightrag-ingest] watermark=${watermark} no changes — skipping source-map fetch`);
        return {
            started,
            finished: new Date().toISOString(),
            watermark_in: watermark,
            watermark_out: watermark,
            submitted: 0,
            replaced: 0,
            skipped: 0,
            already: 0,
            capped: false,
        };
    }

    const existing = await lightragSourceMap(apiKey); // "paperless:<id>" -> [docId]
    console.log(`[lightrag-ingest] watermark=${watermark} known_sources=${existing.size}`);

    let submitted = 0, replaced = 0, skipped = 0, already = 0;
    let maxModified = watermark;
    let page = 1;
    let capped = false;

    // Reuse the page we already fetched; page in from page 2 onward.
    let list = firstPage;
    outer:
    while (true) {
        for (const doc of list.results) {
            if (submitted >= MAX_DOCS_PER_RUN) { capped = true; break outer; }
            const text = (doc.content ?? "").slice(0, MAX_DOC_CHARS).trim();
            const src = `${SOURCE_PREFIX}${doc.id}`;
            if (text.length < MIN_TEXT_LEN) {
                skipped++;
                if (doc.modified > maxModified) maxModified = doc.modified;
                continue;
            }
            // Refresh path (doc already in LightRAG). LightRAG has no upsert:
            // /documents/text 409s if the source exists, and delete is an
            // ASYNC pipeline task that is refused while the pipeline is busy.
            // So: bail fast if busy (delete would be refused), else delete and
            // confirm the source is actually gone before re-inserting. If we
            // can't confirm removal, the OLD doc is still present — leave it
            // (no silent graph-doc loss) and let a future run retry.
            const prior = existing.get(src);
            if (prior?.length) {
                if (await pipelineBusy(apiKey)) {
                    already++;
                    if (doc.modified > maxModified) maxModified = doc.modified;
                    continue;
                }
                await lightragDelete(apiKey, prior);
                if (!(await waitSourceAbsent(apiKey, src, 30_000))) {
                    already++;
                    if (doc.modified > maxModified) maxModified = doc.modified;
                    continue;
                }
                replaced++;
            }
            const inserted = await lightragInsert(apiKey, text, src);
            if (inserted) submitted++; else already++;
            if (doc.modified > maxModified) maxModified = doc.modified;
        }
        if (!list.next) break;
        page += 1;
        list = await paperlessList(token, watermark, page);
        if (list.results.length === 0) break;
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
        already,
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

// LightRAG's delete is an async pipeline task that is refused while the
// pipeline is busy (busy / request_pending / destructive_busy). Checking
// first lets us skip a refresh that would be refused, instead of issuing a
// delete and then racing a re-insert against it.
async function pipelineBusy(apiKey: string): Promise<boolean> {
    const r = await fetch(`${LIGHTRAG}/documents/pipeline_status`, {
        headers: { "X-API-Key": apiKey },
        signal: AbortSignal.timeout(30_000),
    });
    if (!r.ok) return true; // unknown → treat as busy; skip the refresh this run
    const s = (await r.json()) as PipelineStatus;
    return Boolean(s.busy || s.request_pending || s.destructive_busy);
}

// Poll until `source` no longer appears in LightRAG (delete has landed), or
// the timeout elapses. Returns true if confirmed absent.
async function waitSourceAbsent(apiKey: string, source: string, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const map = await lightragSourceMap(apiKey);
        if (!map.get(source)?.length) return true;
        await sleep(2_000);
    }
    return false;
}

async function lightragInsert(apiKey: string, text: string, source: string): Promise<boolean> {
    const r = await fetch(`${LIGHTRAG}/documents/text`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ text, file_source: source }),
        signal: AbortSignal.timeout(60_000),
    });
    // 409 = LightRAG already has this source (no upsert; it wants a delete
    // first). Backstop for the refresh path racing LightRAG's async delete —
    // treat as already-ingested, NOT fatal. Without this, one such doc aborts
    // the run before the watermark advances (line ~90), and every subsequent
    // run re-scans from EPOCH and re-hits it forever. Returns false so the
    // caller counts it as `already` rather than a fresh submit.
    if (r.status === 409) return false;
    if (!r.ok) throw new Error(`lightrag insert ${source} ${r.status}: ${await r.text()}`);
    return true;
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

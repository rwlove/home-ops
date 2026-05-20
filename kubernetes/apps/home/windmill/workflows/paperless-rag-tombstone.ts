// Cron: nightly at 03:00 America/New_York.
//
// Reconciles the Qdrant `paperless` collection against the
// authoritative Paperless doc set. For every paperless_id present in
// Qdrant but absent from Paperless, deletes all chunks. Idempotent.

const QDRANT = Deno.env.get("QDRANT_URL") ??
    "http://qdrant.databases.svc.cluster.local:6333";
const PAPERLESS = Deno.env.get("PAPERLESS_URL") ??
    "http://paperless.collab.svc.cluster.local:8000";
const COLLECTION = "paperless";
const PAGE_SIZE = 200;
const SCROLL_LIMIT = 10_000;

export async function main() {
    const token = Deno.env.get("PAPERLESS_TOKEN");
    if (!token) throw new Error("PAPERLESS_TOKEN env not set");

    const started = new Date().toISOString();
    const liveIds = await fetchPaperlessIds(token);
    const indexedIds = await scrollIndexedIds();
    const orphans = [...indexedIds].filter((id) => !liveIds.has(id));

    console.log(
        `[tombstone] paperless=${liveIds.size} indexed=${indexedIds.size} orphans=${orphans.length}`,
    );

    let deleted_docs = 0;
    let deleted_points = 0;
    for (const id of orphans) {
        const n = await deleteDocPoints(id);
        deleted_points += n;
        deleted_docs += 1;
        console.log(`[tombstone] purged paperless_id=${id} (${n} chunks)`);
    }

    return {
        started,
        finished: new Date().toISOString(),
        paperless_total: liveIds.size,
        indexed_total: indexedIds.size,
        deleted_docs,
        deleted_points,
    };
}

async function fetchPaperlessIds(token: string): Promise<Set<number>> {
    const ids = new Set<number>();
    let page = 1;
    while (true) {
        const url = new URL(`${PAPERLESS}/api/documents/`);
        url.searchParams.set("page", String(page));
        url.searchParams.set("page_size", String(PAGE_SIZE));
        url.searchParams.set("fields", "id");
        const r = await fetch(url, {
            headers: { Authorization: `Token ${token}` },
            signal: AbortSignal.timeout(60_000),
        });
        if (!r.ok) {
            throw new Error(`paperless list ${r.status}: ${await r.text()}`);
        }
        const body = await r.json() as {
            next: string | null;
            results: { id: number }[];
        };
        for (const row of body.results) ids.add(row.id);
        if (!body.next) break;
        page += 1;
    }
    return ids;
}

async function scrollIndexedIds(): Promise<Set<number>> {
    const ids = new Set<number>();
    let offset: number | undefined = undefined;
    while (true) {
        const body: Record<string, unknown> = {
            limit: SCROLL_LIMIT,
            with_payload: ["paperless_id"],
            with_vector: false,
        };
        if (offset !== undefined) body.offset = offset;
        const r = await fetch(
            `${QDRANT}/collections/${COLLECTION}/points/scroll`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            },
        );
        if (r.status === 404) return ids; // collection doesn't exist yet
        if (!r.ok) {
            throw new Error(`qdrant scroll ${r.status}: ${await r.text()}`);
        }
        const out = await r.json() as {
            result: {
                points: { payload: { paperless_id: number } }[];
                next_page_offset: number | null;
            };
        };
        for (const p of out.result.points) {
            if (p.payload?.paperless_id !== undefined) {
                ids.add(p.payload.paperless_id);
            }
        }
        if (out.result.next_page_offset === null) break;
        offset = out.result.next_page_offset;
    }
    return ids;
}

async function deleteDocPoints(paperless_id: number): Promise<number> {
    const r = await fetch(
        `${QDRANT}/collections/${COLLECTION}/points/delete?wait=true`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                filter: {
                    must: [{
                        key: "paperless_id",
                        match: { value: paperless_id },
                    }],
                },
            }),
        },
    );
    if (!r.ok) {
        if (r.status === 404) return 0;
        throw new Error(`qdrant delete ${r.status}: ${await r.text()}`);
    }
    const body = await r.json() as { result?: { operation_id?: number } };
    // Qdrant doesn't return the deleted-count in this response shape;
    // we report 0 as "unknown but completed". The collection-level
    // diff against indexed_total surfaces real deltas in run logs.
    return body.result ? 0 : 0;
}

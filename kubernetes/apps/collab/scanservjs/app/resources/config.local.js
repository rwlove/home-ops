/* eslint-disable */
/**
 * scanservjs config — home-ops.
 *
 * Adds a "Scan to Paperless" pipeline that assembles the scanned pages into
 * a single PDF and uploads it to paperless-ngx over its REST API. paperless
 * does its own OCR + AI tag/title on ingest, so a plain image-PDF is fine.
 *
 * The API token is mounted as a file (scanservjs-secret -> /secrets/
 * PAPERLESS_TOKEN) and read at scan time via `$(cat ...)`. Two reasons for a
 * file + command-substitution instead of an env var:
 *   1. Flux's postBuild envsubst runs in STRICT mode over this ConfigMap; a
 *      literal shell-style variable reference to an unset build var fails
 *      the whole Kustomization; the cat command-substitution has no such
 *      token, so envsubst passes it through untouched.
 *   2. scanservjs logs each pipeline command at info level — `$(cat ...)`
 *      keeps the token out of the logs (and out of the config).
 * `$(cat ...)` also strips any trailing newline. Until the token exists the
 * upload returns 401 (a visible pipeline error); the app still runs.
 *
 * The Brother MFC-J1170DW is pinned by IP in /etc/sane.d/airscan.conf
 * (mDNS does not cross from the printer VLAN to the cluster pod network).
 */
module.exports = {
  afterConfig(config) {
    config.pipelines.push({
      extension: 'pdf',
      description: 'Scan to Paperless',
      // `@-` is replaced with the list of scanned page files. The final
      // command must emit the file list scanservjs finalises, so `ls`
      // runs last after the upload.
      commands: [
        'convert @- paperless.pdf',
        'curl -sS -f -H "Authorization: Token $(cat /secrets/PAPERLESS_TOKEN)" -F "document=@paperless.pdf" http://paperless.collab.svc.cluster.local:8000/api/documents/post_document/',
        'ls paperless.pdf'
      ]
    });
  }
};

/* eslint-disable */
/**
 * scanservjs config — home-ops.
 *
 * Adds a "Scan to Paperless" pipeline that assembles the scanned pages into
 * a single PDF and uploads it to paperless-ngx over its REST API. paperless
 * does its own OCR + AI tag/title on ingest, so a plain image-PDF is fine.
 *
 * The API token is injected as the PAPERLESS_TOKEN env var
 * (ExternalSecret -> scanservjs-secret). Pipeline commands run through a
 * shell, so ${PAPERLESS_TOKEN} expands at scan time. It is written
 * $${PAPERLESS_TOKEN} in the command so Flux's strict postBuild envsubst
 * emits a literal ${PAPERLESS_TOKEN} into the ConfigMap rather than
 * substituting (and failing on) an unset build var. Until the token
 * exists the upload returns 401 (a visible pipeline error); app still runs.
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
        'curl -sS -f -H "Authorization: Token $${PAPERLESS_TOKEN}" -F "document=@paperless.pdf" http://paperless.collab.svc.cluster.local:8000/api/documents/post_document/',
        'ls paperless.pdf'
      ]
    });
  }
};

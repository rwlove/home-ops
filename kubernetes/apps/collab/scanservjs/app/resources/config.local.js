/* eslint-disable */
/**
 * scanservjs config — home-ops.
 *
 * Adds a "Scan to Paperless" pipeline that assembles the scanned pages into
 * a single PDF and uploads it to paperless-ngx over its REST API. paperless
 * does its own OCR + AI tag/title on ingest, so a plain image-PDF is fine.
 *
 * The upload is done by upload-to-paperless.js (node) because the scanservjs
 * image ships no curl/wget. That script reads the API token from the mounted
 * secret file (scanservjs-secret -> /secrets/PAPERLESS_TOKEN), so the token is
 * never in a shell command / the ConfigMap / scanservjs' command logs — and
 * there is no shell-style variable token for Flux's strict postBuild envsubst
 * to choke on. Until the token exists the upload 401s (a visible pipeline
 * error); the app still runs.
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
        'node /etc/scanservjs/upload-to-paperless.js paperless.pdf',
        'ls paperless.pdf'
      ]
    });
  }
};

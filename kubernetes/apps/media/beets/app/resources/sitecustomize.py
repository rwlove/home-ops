# Force the pyacoustid client to use HTTPS.
#
# pyacoustid's bundled default base URL is http://api.acoustid.org/v2/
# (plain HTTP, port 80). This cluster's egress blocks outbound port 80
# ([Errno 101] Network unreachable), while port 443 works fine — so every
# beets `chroma` fingerprint lookup was silently failing and falling back
# to tag-only MusicBrainz search, which cannot match poorly-tagged files.
#
# sitecustomize is auto-imported by CPython at interpreter startup (this
# file's directory is placed on PYTHONPATH via the HelmRelease), so this
# runs before beets loads the chroma plugin.
#
# Remove when the cluster permits outbound port 80, or when pyacoustid
# defaults to HTTPS upstream.
import acoustid

acoustid.set_base_url("https://api.acoustid.org/v2/")

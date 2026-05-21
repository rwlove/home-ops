// Populate the sidebar
//
// This is a script, and not included directly in the page, to control the total size of the book.
// The TOC contains an entry for each page, so if each page includes a copy of the TOC,
// the total size of the page becomes O(n**2).
class MDBookSidebarScrollbox extends HTMLElement {
    constructor() {
        super();
    }
    connectedCallback() {
        this.innerHTML = '<ol class="chapter"><li class="chapter-item expanded affix "><li class="part-title">Welcome</li><li class="chapter-item expanded "><a href="introduction.html">👋 Introduction</a></li><li class="chapter-item expanded "><a href="workflow_automation.html">Workflow Automation: Agents, Approvals, and Push</a></li><li class="chapter-item expanded "><a href="memory_mcp.html">Memory MCP — Cross-Agent Knowledge Graph</a></li><li class="chapter-item expanded "><a href="mcp_observability.html">MCP Fleet Observability</a></li><li class="chapter-item expanded "><a href="power-outage.html">Power Outage Recovery</a></li><li class="chapter-item expanded "><a href="coral.html">Coral</a></li><li class="chapter-item expanded "><a href="p40.html">nVidia Tesla P40</a></li><li class="chapter-item expanded "><a href="cluster_rebuild.html">Cluster Rebuild Actions</a></li><li class="chapter-item expanded "><a href="cluster_upgrade.html">Cluster Upgrade Runbook</a></li><li class="chapter-item expanded "><a href="promote_worker_to_control_plane.html">Promote Worker to Control Plane</a></li><li class="chapter-item expanded "><a href="per-route-cert-migration.html">Per-Route Cert Migration</a></li><li class="chapter-item expanded "><a href="init_teardown.html">Initialization and Teardown</a></li><li class="chapter-item expanded "><a href="immich_cnpg.html">Immich restore to new CNPG database</a></li><li class="chapter-item expanded "><a href="offsite_recovery.html">Offsite Recovery — Immich and Paperless</a></li><li class="chapter-item expanded "><a href="rook_ceph_dr.html">Rook-Ceph DR Runbook</a></li><li class="chapter-item expanded "><a href="cnpg_restore.html">CNPG Cluster Restore Template</a></li><li class="chapter-item expanded "><a href="longhorn_restore.html">Longhorn Restore Runbook</a></li><li class="chapter-item expanded "><a href="garage_restore.html">Garage Substrate Restore Runbook</a></li><li class="chapter-item expanded "><a href="frigate_dr.html">Frigate + direct-NFS DR Runbook</a></li><li class="chapter-item expanded "><a href="master1_etcd_disk_swap.html">master1 etcd Disk Swap</a></li><li class="chapter-item expanded "><a href="apiserver_audit_logging.html">Apiserver Audit Logging Runbook</a></li><li class="chapter-item expanded "><a href="networkpolicy_rollout_plan.html">NetworkPolicy Rollout Plan</a></li><li class="chapter-item expanded "><a href="egress_restriction_design.html">Egress Restriction Design Proposal</a></li><li class="chapter-item expanded "><a href="mtls_rollout_design.html">Istio mTLS Rollout Design Proposal</a></li><li class="chapter-item expanded "><a href="orchestration_substrate.html">Orchestration Substrate</a></li><li class="chapter-item expanded "><a href="task_queue_substrate_design.html">Task Queue Substrate — design proposal</a></li><li class="chapter-item expanded "><a href="pod_security_audit.html">Pod Security Baseline Audit</a></li><li class="chapter-item expanded "><a href="github_webhook.html">Github Webhook</a></li><li class="chapter-item expanded "><a href="limits.html">Resources: Limits and Requests Philosophy</a></li><li class="chapter-item expanded "><a href="rbac_audit.html">RBAC Audit Findings</a></li><li class="chapter-item expanded "><a href="debugging.html">Debugging</a></li></ol>';
        // Set the current, active page, and reveal it if it's hidden
        let current_page = document.location.href.toString().split("#")[0].split("?")[0];
        if (current_page.endsWith("/")) {
            current_page += "index.html";
        }
        var links = Array.prototype.slice.call(this.querySelectorAll("a"));
        var l = links.length;
        for (var i = 0; i < l; ++i) {
            var link = links[i];
            var href = link.getAttribute("href");
            if (href && !href.startsWith("#") && !/^(?:[a-z+]+:)?\/\//.test(href)) {
                link.href = path_to_root + href;
            }
            // The "index" page is supposed to alias the first chapter in the book.
            if (link.href === current_page || (i === 0 && path_to_root === "" && current_page.endsWith("/index.html"))) {
                link.classList.add("active");
                var parent = link.parentElement;
                if (parent && parent.classList.contains("chapter-item")) {
                    parent.classList.add("expanded");
                }
                while (parent) {
                    if (parent.tagName === "LI" && parent.previousElementSibling) {
                        if (parent.previousElementSibling.classList.contains("chapter-item")) {
                            parent.previousElementSibling.classList.add("expanded");
                        }
                    }
                    parent = parent.parentElement;
                }
            }
        }
        // Track and set sidebar scroll position
        this.addEventListener('click', function(e) {
            if (e.target.tagName === 'A') {
                sessionStorage.setItem('sidebar-scroll', this.scrollTop);
            }
        }, { passive: true });
        var sidebarScrollTop = sessionStorage.getItem('sidebar-scroll');
        sessionStorage.removeItem('sidebar-scroll');
        if (sidebarScrollTop) {
            // preserve sidebar scroll position when navigating via links within sidebar
            this.scrollTop = sidebarScrollTop;
        } else {
            // scroll sidebar to current active section when navigating via "next/previous chapter" buttons
            var activeSection = document.querySelector('#sidebar .active');
            if (activeSection) {
                activeSection.scrollIntoView({ block: 'center' });
            }
        }
        // Toggle buttons
        var sidebarAnchorToggles = document.querySelectorAll('#sidebar a.toggle');
        function toggleSection(ev) {
            ev.currentTarget.parentElement.classList.toggle('expanded');
        }
        Array.from(sidebarAnchorToggles).forEach(function (el) {
            el.addEventListener('click', toggleSection);
        });
    }
}
window.customElements.define("mdbook-sidebar-scrollbox", MDBookSidebarScrollbox);

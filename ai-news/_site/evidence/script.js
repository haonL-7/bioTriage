/**
 * Co-Metabolism Evidence Monitor
 */
(function () {
    'use strict';

    // closest() polyfill for IE/older browsers
    if (!Element.prototype.closest) {
        Element.prototype.closest = function (selector) {
            var el = this;
            while (el && el.nodeType === 1) {
                if (el.matches(selector)) return el;
                el = el.parentNode;
            }
            return null;
        };
    }

    var state = {
        papers: [],
        filtered: [],
        searchQuery: '',
        evidenceFilter: 'all',
        typeFilter: 'all',
        nodeFilter: null,
        sortBy: 'evidence',
    };

    // ---- Data loading ----
    function loadData() {
        fetch('data/news.json')
            .then(function (resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            })
            .then(function (data) {
                state.papers = data.papers || [];
                state.filtered = state.papers.slice();
                renderStats();
                renderNodeFilters();
                applyAllFilters();
                var loading = document.getElementById('feedLoading');
                if (loading) loading.style.display = 'none';
            })
            .catch(function (err) {
                var loading = document.getElementById('feedLoading');
                if (loading) loading.textContent = 'Data unavailable.';
                console.error(err);
            });
    }

    // ---- Stats ----
    function renderStats() {
        var papers = state.papers;
        document.getElementById('statTotal').textContent = papers.length || '--';

        var l4 = 0, l35plus = 0;
        var allNodes = {};
        papers.forEach(function (p) {
            var lv = p.evidenceLevel || '';
            if (lv === 'L4') l4++;
            if (lv === 'L4' || lv === 'L3.5' || lv === 'L3') l35plus++;
            (p.nodes || []).forEach(function (n) { allNodes[n] = true; });
        });
        document.getElementById('statL4').textContent = l4 || '--';
        document.getElementById('statL3').textContent = l35plus || '--';
        document.getElementById('statNodes').textContent = Object.keys(allNodes).length || '--';
    }

    // ---- Node filter chips (dynamic, with parent groups) ----
    function renderNodeFilters() {
        var counts = {};
        state.papers.forEach(function (p) {
            (p.nodes || []).forEach(function (n) {
                counts[n] = (counts[n] || 0) + 1;
            });
        });

        // Parent group → children mapping (must match NODE_GROUPS in crawler.py)
        var parentGroups = {
            'SCFAs': ['Butyrate', 'Propionate', 'Acetate', 'Branched SCFAs'],
            'Vitamin B Family': ['Vitamin B12', 'Folate/B9', 'Riboflavin/B2', 'Biotin/B7', 'B-Vitamins (B1/B3/B5/B6)'],
            'Fat-Soluble Vitamins': ['Vitamin A/Retinoic Acid', 'Vitamin D'],
            'Gut Strains': ['Phascolarctobacterium', 'Lactobacillus', 'Bifidobacterium', 'Bacteroides', 'Clostridium', 'Prevotella', 'Akkermansia', 'Faecalibacterium'],
        };
        var parentNames = Object.keys(parentGroups);

        // Build HTML: parent groups first, then children indented, then orphans
        var html = '';
        var rendered = {}; // track which nodes we've already rendered

        parentNames.forEach(function (parent) {
            if (!counts[parent]) return; // skip empty groups
            html += '<span class="node-chip parent-chip" data-node="' + parent + '" data-group="true">' + parent + ' (' + counts[parent] + ')</span>';
            rendered[parent] = true;
            // Render children
            (parentGroups[parent] || []).forEach(function (child) {
                if (counts[child] && !rendered[child]) {
                    html += '<span class="node-chip child-chip" data-node="' + child + '" data-parent="' + parent + '">' + child + ' (' + counts[child] + ')</span>';
                    rendered[child] = true;
                }
            });
        });

        // Standalone nodes (not in any parent group): metabolites without a parent
        var standaloneOrder = ['Bile Acids', 'Tryptophan Metabolites', 'Polyamines', 'Lactate', 'Succinate', 'GABA/Glutamate'];
        standaloneOrder.forEach(function (n) {
            if (counts[n] && !rendered[n]) {
                html += '<span class="node-chip" data-node="' + n + '">' + n + ' (' + counts[n] + ')</span>';
                rendered[n] = true;
            }
        });
        // Catch any remaining nodes
        Object.keys(counts).sort().forEach(function (n) {
            if (counts[n] && !rendered[n]) {
                html += '<span class="node-chip" data-node="' + n + '">' + n + ' (' + counts[n] + ')</span>';
                rendered[n] = true;
            }
        });

        var container = document.getElementById('nodeFilters');
        if (container) container.innerHTML = html;

        // Click handler: parent chips expand/shrink to show/hide children
        container.querySelectorAll('.node-chip').forEach(function (chip) {
            chip.addEventListener('click', function () {
                var node = chip.getAttribute('data-node');
                var isGroup = chip.getAttribute('data-group') === 'true';

                if (isGroup) {
                    // Toggle child visibility
                    var children = parentGroups[node] || [];
                    var isExpanded = chip.classList.contains('expanded');
                    if (isExpanded) {
                        // Collapse: hide children, set filter to parent only
                        chip.classList.remove('expanded');
                        container.querySelectorAll('.child-chip[data-parent="' + node + '"]').forEach(function (c) {
                            c.style.display = 'none';
                        });
                    } else {
                        // Expand: show children, keep parent filter
                        chip.classList.add('expanded');
                        container.querySelectorAll('.child-chip[data-parent="' + node + '"]').forEach(function (c) {
                            c.style.display = '';
                        });
                    }
                }

                // Toggle active filter
                state.nodeFilter = state.nodeFilter === node ? null : node;
                container.querySelectorAll('.node-chip').forEach(function (c) {
                    c.classList.toggle('active', c.getAttribute('data-node') === state.nodeFilter);
                });
                applyAllFilters();
            });
        });

        // Initially collapse child chips
        container.querySelectorAll('.child-chip').forEach(function (c) {
            c.style.display = 'none';
        });
    }

    // ---- Filtering ----
    function applyAllFilters() {
        var items = state.papers.slice();

        // Search (space-insensitive: "p.succinatutens" matches "P. succinatutens")
        if (state.searchQuery) {
            var q = state.searchQuery.toLowerCase().replace(/\s+/g, '');
            // Split into tokens for multi-word search (each token must match)
            var tokens = state.searchQuery.toLowerCase().split(/\s+/).filter(function (t) { return t.length > 0; });
            items = items.filter(function (p) {
                // Space-insensitive match for exact phrase
                var haystack = [
                    (p.title || '').toLowerCase().replace(/\s+/g, ''),
                    (p.abstract || '').toLowerCase().replace(/\s+/g, ''),
                    (p.journal || '').toLowerCase().replace(/\s+/g, ''),
                    (p.firstAuthor || '').toLowerCase().replace(/\s+/g, ''),
                ].join(' ');
                // Also check nodes (space-normalized)
                var nodeHaystack = (p.nodes || []).map(function (n) { return n.toLowerCase().replace(/\s+/g, ''); }).join(' ');
                if (haystack.indexOf(q) >= 0 || nodeHaystack.indexOf(q) >= 0) return true;
                // Token-based fallback: each word must match somewhere
                if (tokens.length > 1) {
                    return tokens.every(function (tok) {
                        var t = tok.replace(/\s+/g, '');
                        return haystack.indexOf(t) >= 0 || nodeHaystack.indexOf(t) >= 0;
                    });
                }
                return false;
            });
        }

        // Evidence filter
        if (state.evidenceFilter !== 'all') {
            items = items.filter(function (p) { return p.evidenceLevel === state.evidenceFilter; });
        }

        // Type filter
        if (state.typeFilter === 'knowledge_base') {
            items = items.filter(function (p) { return (p.type || p.source) === 'knowledge_base'; });
        } else if (state.typeFilter === 'daily') {
            items = items.filter(function (p) { return (p.type || p.source) !== 'knowledge_base'; });
        }

        // Node filter — parent groups match all children
        if (state.nodeFilter) {
            var parentGroups = {
                'SCFAs': ['Butyrate', 'Propionate', 'Acetate', 'Branched SCFAs'],
                'Vitamin B Family': ['Vitamin B12', 'Folate/B9', 'Riboflavin/B2', 'Biotin/B7', 'B-Vitamins (B1/B3/B5/B6)'],
                'Fat-Soluble Vitamins': ['Vitamin A/Retinoic Acid', 'Vitamin D'],
                'Gut Strains': ['Phascolarctobacterium', 'Lactobacillus', 'Bifidobacterium', 'Bacteroides', 'Clostridium', 'Prevotella', 'Akkermansia', 'Faecalibacterium'],
            };
            var matchNodes = parentGroups[state.nodeFilter] || [state.nodeFilter];
            items = items.filter(function (p) {
                return (p.nodes || []).some(function (n) {
                    return matchNodes.indexOf(n) >= 0;
                });
            });
        }

        // Sort
        var levelOrder = { L4: 9, 'L3.5': 8, L3: 7, L2b: 6, L2a: 5, L1b: 4, L1a: 3, L1: 2, L0: 1 };
        if (state.sortBy === 'date') {
            items.sort(function (a, b) { return (b.pubDate || '').localeCompare(a.pubDate || ''); });
        } else if (state.sortBy === 'score') {
            items.sort(function (a, b) { return (b.totalScore || 0) - (a.totalScore || 0); });
        } else {
            items.sort(function (a, b) {
                var la = levelOrder[a.evidenceLevel] || 0;
                var lb = levelOrder[b.evidenceLevel] || 0;
                if (la !== lb) return lb - la;
                return (b.totalScore || 0) - (a.totalScore || 0);
            });
        }

        state.filtered = items;
        renderFeed(items);
        updateExportCount();

        // If local search returned 0 results and user typed a query, offer live search
        var liveOffer = document.getElementById('liveSearchOffer');
        var liveMsg = document.getElementById('liveSearchMsg');
        if (items.length === 0 && state.searchQuery && state.searchQuery.length >= 3) {
            // Only show offer if not already showing
            if (!liveOffer) offerLiveSearch(state.searchQuery);
        } else {
            // Clean up live search UI when results exist
            if (liveOffer) liveOffer.parentNode.removeChild(liveOffer);
            if (liveMsg) liveMsg.style.display = 'none';
        }
    }

    // ---- Rendering ----
    function renderFeed(items) {
        var feed = document.getElementById('feed');
        // Remove existing cards
        var existing = feed.querySelectorAll('.paper-card');
        for (var i = 0; i < existing.length; i++) {
            existing[i].parentNode.removeChild(existing[i]);
        }

        var empty = document.getElementById('feedEmpty');
        if (empty) empty.style.display = items.length === 0 ? 'block' : 'none';

        if (items.length === 0) return;

        var template = document.getElementById('paperCard');
        if (!template) return;

        items.forEach(function (paper) {
            var card = template.content.cloneNode(true);

            var isKB = (paper.type || paper.source) === 'knowledge_base';
            var article = card.querySelector('article');
            if (article) {
                if (isKB) article.className += ' kb-entry';
                article.setAttribute('data-paper-id', paper.id || '');
            }

            // Journal
            var jnEl = card.querySelector('.paper-journal');
            if (jnEl) {
                jnEl.textContent = paper.journal || paper.source || '';
            }

            // Badges
            var badgesEl = card.querySelector('.paper-badges');
            if (badgesEl) {
                if (paper.researchPriority) {
                    var prio = document.createElement('span');
                    prio.className = 'priority-badge priority-' + paper.researchPriority.toLowerCase();
                    prio.textContent = paper.researchPriority;
                    badgesEl.appendChild(prio);
                }
                if (isKB) {
                    var kbBadge = document.createElement('span');
                    kbBadge.className = 'kb-badge';
                    kbBadge.textContent = 'Curated';
                    badgesEl.appendChild(kbBadge);
                }
            }

            // Date
            var dateEl = card.querySelector('.paper-date');
            if (dateEl) dateEl.textContent = paper.pubDate || '';

            // Evidence level badge
            var badge = card.querySelector('.level-badge');
            if (badge) {
                var level = paper.evidenceLevel || 'L1';
                badge.textContent = level;
                badge.className = 'level-badge level-' + level.toLowerCase();
            }

            // Title — clicking opens evaluation detail modal
            var titleLink = card.querySelector('.paper-title a');
            if (titleLink) {
                titleLink.textContent = paper.title || '';
                titleLink.setAttribute('href', '#');
                titleLink.className = 'paper-title-link';
                titleLink.setAttribute('data-paper-id', paper.id || '');
                titleLink.addEventListener('click', function (e) {
                    e.preventDefault();
                    openEvaluationModal(paper.id);
                });
            }

            // Evidence levels line
            var levelsEl = card.querySelector('.paper-evidence-levels');
            if (levelsEl) {
                var parts = [];
                if (paper.porcineEvidenceLevel) parts.push('Porcine: ' + paper.porcineEvidenceLevel);
                if (paper.murineEvidenceLevel) parts.push('Murine: ' + paper.murineEvidenceLevel);
                if (parts.length > 0) {
                    levelsEl.textContent = parts.join(' | ');
                } else if (paper.modelSystem) {
                    levelsEl.textContent = paper.modelSystem;
                }
            }

            // Abstract
            var absEl = card.querySelector('.paper-abstract');
            if (absEl) absEl.textContent = paper.abstract || '';

            // Multi-backup links (only render valid URLs)
            var linksEl = card.querySelector('.paper-links');
            if (linksEl) {
                var links = paper.links || [];
                if (links.length === 0 && paper.url && paper.url.trim()) {
                    links = [{type: 'primary', label: 'Source', url: paper.url}];
                }
                var validLinks = links.filter(function (l) { return l.url && l.url.trim(); });
                if (validLinks.length > 0) {
                    validLinks.forEach(function (link) {
                        var a = document.createElement('a');
                        a.href = link.url;
                        a.target = '_blank';
                        a.rel = 'noopener noreferrer';
                        a.className = 'paper-link paper-link-' + (link.type || 'primary');
                        a.textContent = link.label || 'Link';
                        linksEl.appendChild(a);
                    });
                    // Broken link report button
                    var reportBtn = document.createElement('button');
                    reportBtn.className = 'report-broken-btn';
                    reportBtn.setAttribute('data-paper-id', paper.id || '');
                    reportBtn.title = 'Report broken link';
                    reportBtn.textContent = 'Report';
                    reportBtn.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        reportBrokenLink(paper.id, paper.title);
                    });
                    linksEl.appendChild(reportBtn);
                } else {
                    linksEl.style.display = 'none';
                }
            }

            // Nodes
            var nodesEl = card.querySelector('.paper-nodes');
            if (nodesEl) {
                (paper.nodes || []).forEach(function (node) {
                    var tag = document.createElement('span');
                    tag.className = 'node-tag';
                    tag.textContent = node;
                    nodesEl.appendChild(tag);
                });
            }

            // Matrix bars — use new unified field names with correct scales
            var matrixItems = card.querySelectorAll('.matrix-item');
            // Resolve values: new field names take precedence, fall back to old names
            var fwd = paper.forwardPathway !== undefined ? paper.forwardPathway : (paper.effectiveness || 0);
            var rev = paper.reversePathway !== undefined ? paper.reversePathway : (paper.safety || 0);
            var coup = paper.couplingDepth !== undefined ? paper.couplingDepth : (paper.coupling || 0);
            var depth = paper.measurementDepth || 0;
            var scales = [4, 4, 4, 2];  // Forward, Reverse, Coupling: /4; Depth: /2
            var dims = [fwd, rev, coup, depth];
            for (var i = 0; i < matrixItems.length; i++) {
                var val = dims[i] || 0;
                var scale = scales[i] || 4;
                var suffix = (i === 3) ? '/2' : '/4';
                var fillEl = matrixItems[i].querySelector('.matrix-fill');
                var valEl = matrixItems[i].querySelector('.matrix-val');
                if (fillEl) fillEl.style.width = Math.min(val / Math.max(scale, 1) * 100, 100) + '%';
                if (valEl) valEl.textContent = val + suffix;
            }

            // Compartment tags (new)
            var comps = paper.compartmentsCovered || [];
            if (comps.length > 0) {
                var nodesContainer = card.querySelector('.paper-nodes');
                if (nodesContainer) {
                    var compIcons = { luminal: 'Lumen', epithelial: 'Epi', microenvironment: 'MicroEnv' };
                    var compDiv = document.createElement('div');
                    compDiv.className = 'paper-compartments';
                    comps.forEach(function (c) {
                        var tag = document.createElement('span');
                        tag.className = 'compartment-tag comp-' + c;
                        tag.textContent = compIcons[c] || c;
                        compDiv.appendChild(tag);
                    });
                    nodesContainer.parentNode.insertBefore(compDiv, nodesContainer.nextSibling);
                }
            }

            // Framework alignment tag (new)
            var fwAlign = paper.frameworkAlignment || '';
            if (fwAlign && fwAlign !== 'Pending AI assessment' && fwAlign !== 'Pending') {
                var sumEl = card.querySelector('.paper-summary');
                if (sumEl) {
                    var fwDiv = document.createElement('div');
                    fwDiv.className = 'paper-framework';
                    fwDiv.textContent = 'Framework: ' + fwAlign;
                    sumEl.parentNode.insertBefore(fwDiv, sumEl.nextSibling);
                }
            }

            // Summary
            var sumEl = card.querySelector('.paper-summary');
            if (sumEl) sumEl.textContent = paper.summary || '';

            // Limitation
            var limEl = card.querySelector('.paper-limitation');
            if (limEl) limEl.textContent = paper.keyLimitation ? 'Limitation: ' + paper.keyLimitation : '';

            feed.appendChild(card);
        });
    }

    // ---- Events ----
    var searchTimer;
    var searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', function () {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(function () {
                state.searchQuery = searchInput.value.trim();
                applyAllFilters();
            }, 250);
        });
    }

    document.querySelectorAll('.filter-group').forEach(function (group) {
        group.querySelectorAll('.filter-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                group.querySelectorAll('.filter-btn').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');

                if (btn.getAttribute('data-level') !== null) {
                    state.evidenceFilter = btn.getAttribute('data-level');
                } else if (btn.getAttribute('data-sort')) {
                    state.sortBy = btn.getAttribute('data-sort');
                } else if (btn.getAttribute('data-type')) {
                    state.typeFilter = btn.getAttribute('data-type');
                }
                applyAllFilters();
            });
        });
    });

    // ---- CSV Export ----
    function updateExportCount() {
        var cnt = document.getElementById('exportCount');
        if (cnt) cnt.textContent = state.filtered.length ? '(' + state.filtered.length + ' papers)' : '';
    }

    function exportCsv() {
        var items = state.filtered;
        if (!items.length) {
            alert('No papers match the current filters.');
            return;
        }
        var headers = [
            'title', 'firstAuthor', 'journal', 'pubDate', 'doi', 'pmid',
            'evidenceLevel', 'forwardPathway', 'reversePathway', 'couplingDepth',
            'measurementDepth', 'totalScore', 'nodes', 'modelSystem',
            'researchPriority', 'url', 'summary'
        ];
        var rows = [headers];
        items.forEach(function (p) {
            rows.push(headers.map(function (h) {
                var v = p[h];
                if (v === null || v === undefined) return '';
                if (Array.isArray(v)) return v.join('; ');
                return String(v);
            }));
        });
        function esc(v) {
            v = String(v).replace(/"/g, '""');
            return '"' + v + '"';
        }
        var csv = rows.map(function (r) { return r.map(esc).join(','); }).join('\r\n');
        var blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'biotriage-evidence-' + new Date().toISOString().slice(0, 10) + '.csv';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    var exportBtn = document.getElementById('exportCsvBtn');
    if (exportBtn) {
        exportBtn.addEventListener('click', exportCsv);
    }

    // ---- Live External Search (rate-limited: 10/day) ----
    function getApiState() {
        var today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
        try {
            var stored = JSON.parse(localStorage.getItem('liveSearchApi') || '{}');
            if (stored.date !== today) {
                stored = { date: today, used: 0 };
            }
            return stored;
        } catch (e) {
            return { date: today, used: 0 };
        }
    }

    function saveApiState(state) {
        try {
            localStorage.setItem('liveSearchApi', JSON.stringify(state));
        } catch (e) { /* ignore */ }
    }

    function canUseApi() {
        var state = getApiState();
        return state.used < 10;
    }

    function incrementApiCount() {
        var state = getApiState();
        state.used += 1;
        saveApiState(state);
        return 10 - state.used; // return remaining
    }

    function liveSearchExternal(query, callback) {
        if (!canUseApi()) {
            showLiveSearchError('Daily API limit reached (10/10). Resets tomorrow. Database search is unlimited.');
            return;
        }
        var remaining = incrementApiCount();
        showLiveSearchStatus('Searching external sources (Semantic Scholar + Europe PMC)... ' + remaining + ' calls remaining today.');

        var results = [];
        var completed = 0;
        var total = 2;

        function checkDone() {
            completed++;
            if (completed >= total) {
                // Deduplicate
                var seen = {};
                var unique = [];
                results.forEach(function (r) {
                    var key = (r.title || '').substring(0, 80).toLowerCase();
                    if (!seen[key]) {
                        seen[key] = true;
                        unique.push(r);
                    }
                });
                callback(unique, remaining);
            }
        }

        // Semantic Scholar (CORS-enabled)
        var ssUrl = 'https://api.semanticscholar.org/graph/v1/paper/search?query=' +
            encodeURIComponent(query + ' gut microbiome') +
            '&limit=10&fields=title,abstract,year,externalIds,journal,authors';
        fetch(ssUrl)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                (data.data || []).forEach(function (item) {
                    var doi = (item.externalIds || {}).DOI || '';
                    var title = item.title || '';
                    var abstract = (item.abstract || '').substring(0, 500);
                    var journal = (item.journal || {}).name || '';
                    results.push({
                        id: 'live-' + Math.random().toString(36).slice(2, 10),
                        title: title,
                        abstract: abstract,
                        journal: journal,
                        pubDate: String(item.year || ''),
                        source: 'Semantic Scholar (Live)',
                        url: doi ? 'https://doi.org/' + doi : '',
                        evidenceLevel: 'L1',
                        nodes: [],
                        summary: 'Live search result. Not yet AI-evaluated.',
                        links: doi ? [{ type: 'doi', label: 'DOI', url: 'https://doi.org/' + doi }] : [],
                        isLiveResult: true,
                    });
                });
                checkDone();
            })
            .catch(function () { checkDone(); });

        // Europe PMC (CORS-enabled)
        var epUrl = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=' +
            encodeURIComponent(query + ' AND (gut OR intestinal OR microbiome)') +
            '&format=json&pageSize=10&sort=RELEVANCE desc';
        fetch(epUrl)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                (data.resultList || {}).result = (data.resultList || {}).result || [];
                (data.resultList.result || []).forEach(function (item) {
                    var doi = item.doi || '';
                    var pmid = item.pmid || '';
                    var title = item.title || '';
                    var abstract = (item.abstractText || '').substring(0, 500);
                    results.push({
                        id: 'live-' + Math.random().toString(36).slice(2, 10),
                        title: title,
                        abstract: abstract,
                        journal: item.journalTitle || '',
                        pubDate: item.firstPublicationDate || '',
                        source: 'Europe PMC (Live)',
                        url: doi ? 'https://doi.org/' + doi : '',
                        evidenceLevel: 'L1',
                        nodes: [],
                        summary: 'Live search result. Not yet AI-evaluated.',
                        links: doi ? [{ type: 'doi', label: 'DOI', url: 'https://doi.org/' + doi }] : [],
                        isLiveResult: true,
                    });
                });
                checkDone();
            })
            .catch(function () { checkDone(); });
    }

    function showLiveSearchStatus(msg) {
        var el = document.getElementById('liveSearchMsg');
        if (!el) {
            el = document.createElement('div');
            el.id = 'liveSearchMsg';
            el.className = 'live-search-msg status';
            var feed = document.getElementById('feed');
            if (feed) feed.parentNode.insertBefore(el, feed.nextSibling);
        }
        el.textContent = msg;
        el.className = 'live-search-msg status';
        el.style.display = 'block';
    }

    function showLiveSearchError(msg) {
        var el = document.getElementById('liveSearchMsg');
        if (!el) {
            el = document.createElement('div');
            el.id = 'liveSearchMsg';
            el.className = 'live-search-msg error';
            var feed = document.getElementById('feed');
            if (feed) feed.parentNode.insertBefore(el, feed.nextSibling);
        }
        el.textContent = msg;
        el.className = 'live-search-msg error';
        el.style.display = 'block';
    }

    function renderLiveResults(results, remaining) {
        // Hide status message
        var msgEl = document.getElementById('liveSearchMsg');
        if (msgEl) msgEl.style.display = 'none';

        var feed = document.getElementById('feed');

        // Add a separator
        var sep = document.createElement('div');
        sep.className = 'live-search-separator';
        sep.innerHTML = '<span>Live Search Results (' + results.length + ' found, ' + remaining + ' API calls remaining today)</span>';
        feed.appendChild(sep);

        var template = document.getElementById('paperCard');
        if (!template) return;

        results.forEach(function (paper) {
            var card = template.content.cloneNode(true);
            var article = card.querySelector('article');
            if (article) {
                article.className += ' live-result';
                article.setAttribute('data-paper-id', paper.id || '');
            }

            // Badges
            var badgesEl = card.querySelector('.paper-badges');
            if (badgesEl) {
                var liveBadge = document.createElement('span');
                liveBadge.className = 'live-badge';
                liveBadge.textContent = 'Live';
                badgesEl.appendChild(liveBadge);
            }

            // Journal
            var jnEl = card.querySelector('.paper-journal');
            if (jnEl) jnEl.textContent = paper.journal || paper.source || '';

            // Date
            var dateEl = card.querySelector('.paper-date');
            if (dateEl) dateEl.textContent = paper.pubDate || '';

            // Evidence level (always L1 for live results)
            var badge = card.querySelector('.level-badge');
            if (badge) {
                badge.textContent = 'Live';
                badge.className = 'level-badge level-l1';
            }

            // Title
            var titleLink = card.querySelector('.paper-title a');
            if (titleLink) {
                titleLink.textContent = paper.title || '';
                if (paper.url) {
                    titleLink.setAttribute('href', paper.url);
                    titleLink.setAttribute('target', '_blank');
                }
            }

            // Abstract
            var absEl = card.querySelector('.paper-abstract');
            if (absEl) absEl.textContent = paper.abstract || '';

            // Links
            var linksEl = card.querySelector('.paper-links');
            if (linksEl && paper.links && paper.links.length > 0) {
                paper.links.forEach(function (link) {
                    var a = document.createElement('a');
                    a.href = link.url;
                    a.target = '_blank';
                    a.rel = 'noopener noreferrer';
                    a.className = 'paper-link paper-link-' + (link.type || 'primary');
                    a.textContent = link.label || 'Link';
                    linksEl.appendChild(a);
                });
            } else if (linksEl) {
                linksEl.style.display = 'none';
            }

            // Summary
            var sumEl = card.querySelector('.paper-summary');
            if (sumEl) sumEl.textContent = paper.summary || '';

            feed.appendChild(card);
        });
    }

    function offerLiveSearch(query) {
        var feed = document.getElementById('feed');
        // Remove any existing offer
        var existing = document.getElementById('liveSearchOffer');
        if (existing) existing.parentNode.removeChild(existing);

        var offer = document.createElement('div');
        offer.id = 'liveSearchOffer';
        offer.className = 'live-search-offer';

        var apiState = getApiState();
        var remaining = 10 - apiState.used;

        if (remaining <= 0) {
            offer.innerHTML = '<p>No results in database.</p><p class="live-search-note">Daily external search limit reached (10/10). Resets tomorrow. <strong>Database search is always unlimited.</strong></p>';
        } else {
            offer.innerHTML = '<p>No results found in the curated database.</p>' +
                '<button id="liveSearchBtn" class="live-search-btn">Search External Sources (' + remaining + ' API calls left today)</button>' +
                '<p class="live-search-note">Searches Semantic Scholar + Europe PMC for the latest research. Rate limited to 10/day.</p>';
        }
        feed.appendChild(offer);

        var btn = document.getElementById('liveSearchBtn');
        if (btn) {
            btn.addEventListener('click', function () {
                btn.disabled = true;
                btn.textContent = 'Searching...';
                liveSearchExternal(query, function (results, rem) {
                    // Remove the offer
                    var o = document.getElementById('liveSearchOffer');
                    if (o) o.parentNode.removeChild(o);
                    // Remove empty message
                    var empty = document.getElementById('feedEmpty');
                    if (empty) empty.style.display = 'none';
                    // Render results
                    if (results.length > 0) {
                        renderLiveResults(results, rem);
                    } else {
                        showLiveSearchError('No results found in external sources either. Try a different query.');
                    }
                });
            });
        }
    }
    function openEvaluationModal(paperId) {
        if (!paperId) return;
        // Find paper: try state.papers first, then embedded __PAPERS__ fallback
        var paper = null;
        var search = state.papers.length > 0 ? state.papers : (window.__PAPERS__ || []);
        for (var i = 0; i < search.length; i++) {
            if (search[i].id === paperId) { paper = search[i]; break; }
        }
        if (!paper) return;

        var modal = document.getElementById('evalModal');
        var content = document.getElementById('modalContent');
        if (!modal || !content) return;

        var lv = paper.evidenceLevel || 'L1';
        var isKB = (paper.type || paper.source) === 'knowledge_base';

        // Build badges
        var badgesHtml = '';
        if (paper.researchPriority) {
            badgesHtml += '<span class="priority-badge priority-' + paper.researchPriority.toLowerCase() + '">' + paper.researchPriority + '</span>';
        }
        if (isKB) {
            badgesHtml += '<span class="kb-badge">Curated</span>';
        }

        // Evidence levels comparison
        var evParts = [];
        if (paper.porcineEvidenceLevel) evParts.push('Porcine: ' + paper.porcineEvidenceLevel);
        if (paper.murineEvidenceLevel) evParts.push('Murine: ' + paper.murineEvidenceLevel);
        var evLine = evParts.length > 0 ? evParts.join(' | ') : (paper.modelSystem || '');

        // 4-D Matrix — unified framework with correct scales
        var fwd = paper.forwardPathway !== undefined ? paper.forwardPathway : (paper.effectiveness || 0);
        var rev = paper.reversePathway !== undefined ? paper.reversePathway : (paper.safety || 0);
        var coup = paper.couplingDepth !== undefined ? paper.couplingDepth : (paper.coupling || 0);
        var depth = paper.measurementDepth || 0;
        var dims = [
            {id: 'forwardPathway', label: 'Forward (Microbe→Host)', val: fwd, scale: 4, suffix: '/4'},
            {id: 'reversePathway', label: 'Reverse (Host→Microbiome)', val: rev, scale: 4, suffix: '/4'},
            {id: 'couplingDepth', label: 'Bidirectional Coupling', val: coup, scale: 4, suffix: '/4'},
            {id: 'measurementDepth', label: 'Measurement Depth', val: depth, scale: 2, suffix: '/2'}
        ];
        var matrixHtml = '';
        dims.forEach(function (d) {
            var pct = Math.min(d.val / Math.max(d.scale, 1) * 100, 100);
            matrixHtml += '<div class="matrix-item" data-dim="' + d.id + '">' +
                '<span class="matrix-label">' + d.label + '</span>' +
                '<span class="matrix-bar"><span class="matrix-fill" style="width:' + pct + '%"></span></span>' +
                '<span class="matrix-val">' + d.val + d.suffix + '</span></div>';
        });

        // Forward/Reverse justifications
        var fwdJust = paper.forwardJustification || '';
        var revJust = paper.reverseJustification || '';

        // Nodes
        var nodesHtml = '';
        (paper.nodes || []).forEach(function (n) {
            if (n.indexOf('SCFAs') >= 0 || n.indexOf('Vitamin') >= 0 || n.indexOf('Gut Strains') >= 0) return; // skip parent groups in modal
            nodesHtml += '<span class="node-tag">' + n + '</span>';
        });

        // External links
        var allLinks = paper.links || [];
        if (allLinks.length === 0 && paper.url && paper.url.trim()) {
            allLinks = [{type: 'primary', label: 'Source', url: paper.url}];
        }
        var extLinksHtml = '';
        var validLinks = allLinks.filter(function (l) { return l.url && l.url.trim(); });
        if (validLinks.length > 0) {
            extLinksHtml = '<div class="modal-links"><h3>Original Paper Links</h3>';
            validLinks.forEach(function (l) {
                extLinksHtml += '<a href="' + l.url + '" target="_blank" rel="noopener noreferrer" class="paper-link paper-link-' + (l.type || 'primary') + '">' + (l.label || 'Link') + '</a> ';
            });
            extLinksHtml += '</div>';
        } else {
            extLinksHtml = '<p class="modal-no-link">No external link available (curated knowledge base entry)</p>';
        }

        content.innerHTML =
            '<div class="modal-header">' +
                '<span class="paper-journal">' + (paper.journal || '') + '</span>' +
                '<div class="paper-badges" style="display:inline;margin-left:8px;">' + badgesHtml + '</div>' +
                '<span class="level-badge level-' + lv.toLowerCase() + '">' + lv + '</span>' +
            '</div>' +
            '<h1 class="modal-title">' + (paper.title || '') + '</h1>' +
            '<p class="modal-evidence">' + evLine + '</p>' +
            '<div class="modal-section"><h3>Evidence Justification</h3><p>' + (paper.evidenceJustification || 'Not provided.') + '</p></div>' +
            (fwdJust ? '<div class="modal-section"><h3>Forward Pathway (Microbe→Host)</h3><p>' + fwdJust + '</p></div>' : '') +
            (revJust ? '<div class="modal-section"><h3>Reverse Pathway (Host→Microbiome)</h3><p>' + revJust + '</p></div>' : '') +
            '<div class="modal-section"><h3>Four-Dimensional Matrix</h3><div class="paper-matrix" style="grid-template-columns: repeat(2,1fr);">' + matrixHtml + '</div></div>' +
            '<div class="modal-section"><h3>Model System</h3><p>' + (paper.modelSystem || 'Not specified.') + '</p></div>' +
            '<div class="modal-section"><h3>Key Limitation</h3><p>' + (paper.keyLimitation || 'Not specified.') + '</p></div>' +
            '<div class="modal-section"><h3>Summary</h3><p>' + (paper.summary || '') + '</p></div>' +
            '<div class="modal-nodes">' + nodesHtml + '</div>' +
            extLinksHtml +
            '<p class="modal-meta">ID: ' + paperId + ' | Source: ' + (paper.source || 'unknown') + ' | Date: ' + (paper.pubDate || '') + '</p>';

        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }

    function closeModal() {
        var modal = document.getElementById('evalModal');
        if (modal) modal.style.display = 'none';
        document.body.style.overflow = '';
    }

    // Modal event listeners
    var modalClose = document.getElementById('modalClose');
    if (modalClose) {
        modalClose.addEventListener('click', closeModal);
    }
    var modalOverlay = document.getElementById('evalModal');
    if (modalOverlay) {
        modalOverlay.addEventListener('click', function (e) {
            if (e.target === modalOverlay) closeModal();
        });
    }
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeModal();
    });

    // Click handlers for pre-rendered cards: click card or title → open modal
    document.getElementById('feed').addEventListener('click', function (e) {
        // Don't intercept clicks on external links or buttons
        if (e.target.closest('a[target="_blank"]') || e.target.closest('button') || e.target.closest('.paper-link')) {
            return;
        }
        var card = e.target.closest('.paper-card');
        if (card) {
            var paperId = card.getAttribute('data-paper-id');
            if (paperId) {
                e.preventDefault();
                openEvaluationModal(paperId);
            }
        }
    });

    // ---- Link health check (CORS-safe: uses img beacon) ----
    var brokenLinks = {}; // id -> {count, lastReported}

    function checkLinkHealth() {
        var links = document.querySelectorAll('.paper-link');
        links.forEach(function (link) {
            // Skip already-checked links
            if (link.dataset.checked === 'true') return;
            link.dataset.checked = 'true';

            var url = link.getAttribute('href');
            if (!url || url === '#') return;

            // Use Image beacon to check link (avoids CORS issues with fetch)
            var img = new Image();
            var timeout = setTimeout(function () {
                // Timeout = likely unreachable
                link.classList.add('link-unreachable');
                link.title = 'Link may be unavailable';
            }, 5000);

            img.onload = function () {
                clearTimeout(timeout);
                link.classList.add('link-ok');
            };
            img.onerror = function () {
                clearTimeout(timeout);
                // Error doesn't always mean broken — could be non-image URL
                // Mark as "uncertain" rather than broken
                link.classList.add('link-uncertain');
            };
            img.src = url;
        });
    }

    function reportBrokenLink(id, title) {
        if (!id) return;
        var now = new Date().toISOString();
        if (!brokenLinks[id]) {
            brokenLinks[id] = { count: 0, firstReported: now, title: title };
        }
        brokenLinks[id].count += 1;
        brokenLinks[id].lastReported = now;

        // Store in localStorage for persistence
        try {
            localStorage.setItem('brokenLinks', JSON.stringify(brokenLinks));
        } catch (e) { /* ignore */ }

        // Visual feedback
        var btn = document.querySelector('.report-broken-btn[data-paper-id=\"' + id + '\"]');
        if (btn) {
            btn.textContent = 'Reported';
            btn.classList.add('reported');
            setTimeout(function () { btn.textContent = 'Report'; btn.classList.remove('reported'); }, 2000);
        }

        console.log('Broken link reported:', id, title ? title.substring(0, 50) : '', 'Count:', brokenLinks[id].count);
    }

    function loadBrokenLinkReports() {
        try {
            var stored = localStorage.getItem('brokenLinks');
            if (stored) brokenLinks = JSON.parse(stored);
        } catch (e) { /* ignore */ }
    }

    // ---- Init ----
    loadBrokenLinkReports();
    loadData();

    // Run link health check after a short delay (let the page render first)
    setTimeout(function () {
        checkLinkHealth();
    }, 2000);
})();

/* ============================================================
   account/app.js — GitHub Pages 静态账户页（原生 JS，无构建步骤）
   - 站点与后端不同源：所有请求指向后端绝对地址，凭据走 Authorization: Bearer
   - 凭据来自地址栏片段 #t=<token>，只存 sessionStorage（当前标签页）
   - 401 一律回到未登录态，不做弹窗报错
   - 每个请求 8 秒超时（AbortController + 定时兜底），任何分支都会结束加载态
   ============================================================ */
(function () {
    "use strict";

    var API_BASE = "https://1477715351-0lu4gk6z6i.ap-chengdu.tencentscf.com";

    var ENDPOINTS = {
        me: API_BASE + "/api/auth/me",
        stars: API_BASE + "/api/me/stars",
        evaluations: API_BASE + "/api/me/evaluations",
        logout: API_BASE + "/api/auth/logout"
    };

    var TOKEN_KEY = "bt_token";
    var HASH_KEY = "t";

    // 云函数冷启动 + 数据库唤醒常需 3~8 秒
    var REQUEST_TIMEOUT_MS = 8000;
    // 加载态兜底：即使请求层的超时未能触发，也把加载态收掉
    var WATCHDOG_EXTRA_MS = 4000;
    var LOADING_HINT_MS = 3000;
    var LOADING_TEXT = "正在读取账户信息…";
    var LOADING_HINT_TEXT = "正在唤醒服务，首次访问可能需要 5–10 秒…";
    var GENERIC_FAIL_TEXT = "账户信息读取失败，请稍后刷新重试。";

    var currentToken = "";
    var loadingHintTimer = null;
    var watchdogTimer = null;

    /* ---------------- 凭据存取（只作用于当前标签页） ---------------- */
    function readToken() {
        try {
            var v = window.sessionStorage.getItem(TOKEN_KEY);
            return v ? String(v) : "";
        } catch (err) {
            return "";
        }
    }

    function saveToken(token) {
        try {
            window.sessionStorage.setItem(TOKEN_KEY, token);
            return true;
        } catch (err) {
            return false;
        }
    }

    function dropToken() {
        currentToken = "";
        try {
            window.sessionStorage.removeItem(TOKEN_KEY);
        } catch (err) { /* 存储不可用时忽略：内存里的凭据已经清掉 */ }
    }

    function decodeSafe(value) {
        try {
            return decodeURIComponent(value);
        } catch (err) {
            return value;
        }
    }

    /* ---------------- 地址栏片段：#t=<token> ---------------- */
    function stripTokenHash() {
        var clean = "/";
        try {
            clean = window.location.pathname + window.location.search;
        } catch (err) {
            return;
        }
        try {
            if (window.history && typeof window.history.replaceState === "function") {
                window.history.replaceState(null, document.title, clean);
            }
        } catch (err2) { /* 浏览器拒绝改写地址栏时忽略：凭据已转移，不影响后续请求 */ }
    }

    // 读到 #t= 即落盘 sessionStorage 并把片段从地址栏抹掉；没有则返回空串（保留原片段）
    function captureTokenFromHash() {
        var hash = "";
        try {
            hash = String(window.location.hash || "");
        } catch (err) {
            return "";
        }
        if (hash.length < 2) return "";

        var raw = hash.charAt(0) === "#" ? hash.slice(1) : hash;
        var parts = raw.split("&");
        var token = "";
        var prefix = HASH_KEY + "=";
        for (var i = 0; i < parts.length; i++) {
            if (parts[i].indexOf(prefix) === 0) {
                token = parts[i].slice(prefix.length);
                break;
            }
        }
        if (!token) return "";

        token = decodeSafe(token);
        currentToken = token;
        saveToken(token);
        stripTokenHash();
        return token;
    }

    /* ---------------- 请求封装 ---------------- */
    // 返回 { ok, kind, status, data }；kind: network | unauth | http | timeout
    // 永不 reject：超时用 AbortController 中断，统一返回 kind="timeout"
    function request(url, options) {
        var opts = options || {};
        var timeoutMs = typeof opts.timeoutMs === "number" ? opts.timeoutMs : REQUEST_TIMEOUT_MS;

        var headers = { "Accept": "application/json" };
        if (currentToken) headers["Authorization"] = "Bearer " + currentToken;
        if (opts.body !== undefined) headers["Content-Type"] = "application/json";

        var init = {
            method: opts.method || "GET",
            credentials: "omit",
            headers: headers
        };
        if (opts.body !== undefined) init.body = opts.body;

        var controller = null;
        if (typeof AbortController === "function") {
            try {
                controller = new AbortController();
                init.signal = controller.signal;
            } catch (err) {
                controller = null;
            }
        }

        return new Promise(function (resolve) {
            var settled = false;
            var timer = null;

            function settle(result) {
                if (settled) return;
                settled = true;
                if (timer) {
                    clearTimeout(timer);
                    timer = null;
                }
                resolve(result);
            }

            if (timeoutMs > 0) {
                timer = setTimeout(function () {
                    if (controller) {
                        try {
                            controller.abort();
                        } catch (err) { /* 忽略：中断能力缺失时只丢弃结果 */ }
                    }
                    settle({ ok: false, kind: "timeout", status: 0 });
                }, timeoutMs);
            }

            var pending;
            try {
                pending = window.fetch(url, init);
            } catch (err) {
                settle({ ok: false, kind: "network", status: 0 });
                return;
            }
            if (!pending || typeof pending.then !== "function") {
                settle({ ok: false, kind: "network", status: 0 });
                return;
            }

            pending.then(function (res) {
                if (res.status === 401) {
                    settle({ ok: false, kind: "unauth", status: 401 });
                    return;
                }
                if (!res.ok) {
                    settle({ ok: false, kind: "http", status: res.status });
                    return;
                }
                if (res.status === 204 || typeof res.json !== "function") {
                    settle({ ok: true, status: res.status, data: null });
                    return;
                }
                res.json().then(function (data) {
                    settle({ ok: true, status: res.status, data: data });
                }, function () {
                    settle({ ok: true, status: res.status, data: null });
                });
            }, function () {
                settle({ ok: false, kind: "network", status: 0 });
            });
        });
    }

    /* ---------------- 小工具 ---------------- */
    function byId(id) {
        return document.getElementById(id);
    }

    function pick(obj, keys) {
        if (!obj || typeof obj !== "object") return null;
        for (var i = 0; i < keys.length; i++) {
            var v = obj[keys[i]];
            if (v !== undefined && v !== null && v !== "") return v;
        }
        return null;
    }

    function num(v) {
        if (v === null || v === undefined || v === "") return null;
        var n = typeof v === "number" ? v : parseFloat(v);
        return isFinite(n) ? n : null;
    }

    function pad2(n) {
        return n < 10 ? "0" + n : String(n);
    }

    function fmtDate(v) {
        if (v === null || v === undefined || v === "") return "";
        var d = null;
        if (typeof v === "number") {
            d = new Date(v > 1e12 ? v : v * 1000);
        } else {
            d = new Date(String(v));
        }
        if (!d || isNaN(d.getTime())) return String(v);
        return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) +
            " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    }

    function kindLabel(kind) {
        var map = { paper: "论文", trial: "试验", strain: "菌株", metabolite: "代谢物", topic: "主题" };
        var key = String(kind || "").toLowerCase();
        return map[key] || String(kind);
    }

    function retryText(res, prefix) {
        if (res.kind === "network") return prefix + "：网络连接失败，请检查网络后重试。";
        if (res.kind === "timeout") return prefix + "：服务响应超时（可能正在冷启动），请稍后重试。";
        if (res.kind === "http") return prefix + "：服务返回 HTTP " + res.status + "，请稍后重试。";
        return prefix + "：请稍后重试。";
    }

    function showNote(el, message) {
        if (!el) return;
        var text = message ? String(message) : "";
        el.textContent = text;
        el.hidden = !text;
    }

    /* ---------------- 加载态 ---------------- */
    function stopLoadingHint() {
        if (loadingHintTimer) {
            clearTimeout(loadingHintTimer);
            loadingHintTimer = null;
        }
    }

    function startLoadingHint() {
        stopLoadingHint();
        loadingHintTimer = setTimeout(function () {
            loadingHintTimer = null;
            var box = byId("loadingBox");
            var text = byId("loadingText");
            if (box && !box.hidden && text) text.textContent = LOADING_HINT_TEXT;
        }, LOADING_HINT_MS);
    }

    function stopWatchdog() {
        if (watchdogTimer) {
            clearTimeout(watchdogTimer);
            watchdogTimer = null;
        }
    }

    function armWatchdog() {
        stopWatchdog();
        watchdogTimer = setTimeout(function () {
            watchdogTimer = null;
            var box = byId("loadingBox");
            if (box && !box.hidden) {
                showGuest("服务响应较慢，已停止等待；请稍后刷新页面重试。");
            }
        }, REQUEST_TIMEOUT_MS + WATCHDOG_EXTRA_MS);
    }

    function hideLoading() {
        stopLoadingHint();
        stopWatchdog();
        var box = byId("loadingBox");
        if (box) box.hidden = true;
        var text = byId("loadingText");
        if (text) text.textContent = LOADING_TEXT;
    }

    /* ---------------- 视图切换 ---------------- */
    function showGuest(note) {
        hideLoading();
        var authed = byId("authedBox");
        if (authed) authed.hidden = true;
        var guest = byId("guestBox");
        if (guest) guest.hidden = false;
        showNote(byId("guestNote"), note);
    }

    function showAuthed() {
        hideLoading();
        var guest = byId("guestBox");
        if (guest) guest.hidden = true;
        var authed = byId("authedBox");
        if (authed) authed.hidden = false;
    }

    // 401：清掉本地凭据，安静回到未登录态
    function handleUnauth() {
        dropToken();
        showGuest("");
    }

    /* ---------------- 渲染：账户与配额 ---------------- */
    function renderUser(user) {
        var orcid = pick(user, ["orcid", "orcid_id", "orcidId", "orcid_uri"]);
        var name = pick(user, ["name", "display_name", "displayName", "full_name"]);
        if (byId("orcidText")) byId("orcidText").textContent = orcid ? String(orcid) : "未提供";
        if (byId("nameText")) byId("nameText").textContent = name ? String(name) : "未提供";
    }

    function renderQuota(quota) {
        var q = quota || {};
        var tier = pick(q, ["tier", "plan", "level"]);
        var limit = num(pick(q, ["limit", "total"]));
        var used = num(pick(q, ["used", "usage"]));
        var remaining = num(pick(q, ["remaining", "left"]));
        if (remaining === null && limit !== null && used !== null) {
            remaining = Math.max(limit - used, 0);
        }

        if (byId("tierText")) byId("tierText").textContent = tier ? String(tier) : "未提供";
        if (byId("quotaUsed")) byId("quotaUsed").textContent = used === null ? "—" : String(used);
        if (byId("quotaLimit")) byId("quotaLimit").textContent = limit === null ? "—" : String(limit);
        if (byId("quotaRemaining")) {
            byId("quotaRemaining").textContent = remaining === null ? "—" : String(remaining);
        }

        var fill = byId("quotaFill");
        if (fill) {
            fill.style.width = (limit && limit > 0 && used !== null)
                ? Math.min(Math.max(used / limit, 0), 1) * 100 + "%"
                : "0";
        }
        var resets = fmtDate(q.resets_at || q.reset_at);
        if (byId("quotaReset")) {
            byId("quotaReset").textContent = resets
                ? "配额重置时间：" + resets
                : "配额重置时间以服务端返回为准。";
        }
    }

    /* ---------------- 渲染：我的收藏 ---------------- */
    function refreshStarCount() {
        var list = byId("starList");
        var count = list ? list.children.length : 0;
        if (byId("starCount")) byId("starCount").textContent = count ? "共 " + count + " 条" : "";
        if (byId("starEmpty")) byId("starEmpty").hidden = count !== 0;
    }

    function starRow(item) {
        var li = document.createElement("li");
        li.className = "item";

        var main = document.createElement("div");
        main.className = "item-main";

        var title = document.createElement("div");
        title.className = "item-title";
        title.textContent = String(pick(item, ["title", "name"]) || "（未命名条目）");
        main.appendChild(title);

        var bits = [];
        var doi = pick(item, ["doi"]);
        if (doi) bits.push("DOI " + doi);
        var created = fmtDate(pick(item, ["created_at", "createdAt", "time"]));
        if (created) bits.push("收藏于 " + created);
        if (bits.length) {
            var meta = document.createElement("p");
            meta.className = "item-meta";
            meta.textContent = bits.join(" · ");
            main.appendChild(meta);
        }

        var note = document.createElement("p");
        note.className = "item-note";
        note.hidden = true;
        main.appendChild(note);

        li.appendChild(main);

        var kind = pick(item, ["kind"]);
        if (kind) {
            var badge = document.createElement("span");
            badge.className = "badge";
            badge.textContent = kindLabel(kind);
            li.appendChild(badge);
        }

        var id = pick(item, ["id", "star_id", "_id"]);
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "item-remove";
        btn.textContent = "移除";
        btn.setAttribute("data-star-id", id === null ? "" : String(id));
        btn.disabled = id === null;
        btn.addEventListener("click", removeStar);
        li.appendChild(btn);

        return li;
    }

    function removeStar(event) {
        var btn = event.currentTarget;
        var id = btn.getAttribute("data-star-id");
        if (!id) return;

        var row = btn.closest ? btn.closest(".item") : null;
        var note = row ? row.querySelector(".item-note") : null;
        var original = btn.textContent;

        btn.disabled = true;
        btn.textContent = "移除中…";
        showNote(note, "");

        request(ENDPOINTS.stars + "/" + encodeURIComponent(id), { method: "DELETE" }).then(function (res) {
            if (res.ok) {
                if (row && row.parentNode) row.parentNode.removeChild(row);
                refreshStarCount();
                return;
            }
            btn.disabled = false;
            btn.textContent = original;
            if (res.kind === "unauth") {
                handleUnauth();
                return;
            }
            showNote(note, retryText(res, "移除失败"));
        });
    }

    function loadStars() {
        var list = byId("starList");
        var errEl = byId("starError");
        if (!list) return Promise.resolve();

        return request(ENDPOINTS.stars).then(function (res) {
            if (!res.ok) {
                list.textContent = "";
                refreshStarCount();
                if (res.kind === "unauth") {
                    handleUnauth();
                    return;
                }
                showNote(errEl, retryText(res, "无法加载收藏列表"));
                return;
            }
            showNote(errEl, "");
            var items = (res.data && Array.isArray(res.data.items)) ? res.data.items : [];
            list.textContent = "";
            for (var i = 0; i < items.length; i++) {
                list.appendChild(starRow(items[i]));
            }
            refreshStarCount();
        });
    }

    /* ---------------- 渲染：评估历史 ---------------- */
    function evalRow(item) {
        var li = document.createElement("li");
        li.className = "item";

        var main = document.createElement("div");
        main.className = "item-main";

        var title = document.createElement("div");
        title.className = "item-title";
        title.textContent = String(pick(item, ["title", "query", "name", "subject", "term", "keyword"]) || "（未命名评估）");
        main.appendChild(title);

        var bits = [];
        var created = fmtDate(pick(item, ["created_at", "createdAt", "evaluated_at", "time"]));
        if (created) bits.push(created);
        var score = pick(item, ["score", "total_score", "value"]);
        if (score !== null) bits.push("评分 " + score);
        if (bits.length) {
            var meta = document.createElement("p");
            meta.className = "item-meta";
            meta.textContent = bits.join(" · ");
            main.appendChild(meta);
        }
        li.appendChild(main);

        var level = pick(item, ["tier", "evidence_level", "level"]);
        if (level !== null) {
            var badge = document.createElement("span");
            badge.className = "badge";
            badge.textContent = String(level);
            li.appendChild(badge);
        }
        return li;
    }

    function loadEvaluations() {
        var list = byId("evalList");
        var errEl = byId("evalError");
        if (!list) return Promise.resolve();

        return request(ENDPOINTS.evaluations).then(function (res) {
            if (!res.ok) {
                list.textContent = "";
                if (byId("evalEmpty")) byId("evalEmpty").hidden = true;
                if (byId("evalCount")) byId("evalCount").textContent = "";
                if (res.kind === "unauth") {
                    handleUnauth();
                    return;
                }
                showNote(errEl, retryText(res, "无法加载评估历史"));
                return;
            }
            showNote(errEl, "");
            var items = (res.data && Array.isArray(res.data.items)) ? res.data.items : [];
            list.textContent = "";
            for (var i = 0; i < items.length; i++) {
                list.appendChild(evalRow(items[i]));
            }
            if (byId("evalCount")) {
                byId("evalCount").textContent = items.length ? "共 " + items.length + " 条" : "";
            }
            if (byId("evalEmpty")) byId("evalEmpty").hidden = items.length !== 0;
        });
    }

    /* ---------------- 登出 ---------------- */
    function bindLogout() {
        var btn = byId("logoutBtn");
        if (!btn || btn.getAttribute("data-bound") === "1") return;
        btn.setAttribute("data-bound", "1");

        btn.addEventListener("click", function () {
            var noteEl = byId("logoutNote");
            btn.disabled = true;
            showNote(noteEl, "");

            request(ENDPOINTS.logout, { method: "POST" }).then(function (res) {
                btn.disabled = false;
                if (res.ok || res.kind === "unauth") {
                    dropToken();
                    showGuest("");
                    return;
                }
                // 服务端没确认撤销：本机先退出，并如实告知
                dropToken();
                showGuest("已在本机退出登录；服务器未确认撤销（" +
                    (res.kind === "network" ? "网络连接失败" :
                        res.kind === "timeout" ? "响应超时" : "HTTP " + res.status) +
                    "），如有顾虑请稍后重新登录再登出。");
            });
        });
    }

    /* ---------------- 启动 ---------------- */
    function startAccountPage() {
        return request(ENDPOINTS.me).then(function (res) {
            if (!res.ok) {
                if (res.kind === "unauth") {
                    handleUnauth();
                } else if (res.kind === "network") {
                    showGuest("暂时无法连接服务器，请检查网络后刷新页面。");
                } else if (res.kind === "timeout") {
                    showGuest("服务响应较慢（云函数冷启动中），已按未登录展示；稍后刷新即可。");
                } else {
                    showGuest("账户服务暂不可用（HTTP " + res.status + "），请稍后刷新重试。");
                }
                return null;
            }

            var session = res.data || {};
            if (!session.authenticated || !session.user) {
                // 服务端不认这份凭据：清掉本地保存的副本
                handleUnauth();
                return null;
            }

            showAuthed();
            renderUser(session.user);
            renderQuota(session.quota);
            bindLogout();

            return Promise.all([loadStars(), loadEvaluations()]);
        });
    }

    function boot() {
        var loading = byId("loadingBox");
        if (loading && !loading.hidden) startLoadingHint();

        var fromHash = "";
        try {
            fromHash = captureTokenFromHash();
        } catch (err) {
            fromHash = "";
        }
        currentToken = fromHash || readToken();

        if (!currentToken) {
            showGuest("");
            return;
        }

        armWatchdog();
        startAccountPage().then(null, function () {
            showGuest(GENERIC_FAIL_TEXT);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();

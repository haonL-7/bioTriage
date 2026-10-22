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
    var accountOrcid = "";
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
        unmountAdmin();
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
        accountOrcid = orcid ? String(orcid) : "";
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

    /* ---------------- 管理员（仅 is_admin 为 true 时挂载 DOM） ---------------- */
    var ADMIN_ENDPOINTS = {
        overview: API_BASE + "/api/admin/overview",
        users: API_BASE + "/api/admin/users"
    };
    var ADMIN_PAGE_SIZE = 50;
    var QUOTA_MIN = 1;
    var QUOTA_MAX = 1000;
    var adminRows = [];
    var adminTotal = 0;

    function makeEl(tag, cls, text) {
        var el = document.createElement(tag);
        if (cls) el.className = cls;
        if (text !== undefined && text !== null) el.textContent = String(text);
        return el;
    }

    function makeBtn(cls, label, onClick) {
        var btn = makeEl("button", cls, label);
        btn.type = "button";
        btn.addEventListener("click", onClick);
        return btn;
    }

    function adminFigure(id, label) {
        var box = makeEl("div", "quota-item");
        var val = makeEl("div", "quota-val", "—");
        val.id = id;
        box.appendChild(val);
        box.appendChild(makeEl("div", "quota-label", label));
        return box;
    }

    function setFigure(id, value) {
        var el = byId(id);
        if (el) el.textContent = (value === null || value === undefined) ? "—" : String(value);
    }

    function mergeInto(base, extra) {
        var target = (base && typeof base === "object") ? base : {};
        if (!extra || typeof extra !== "object") return target;
        for (var key in extra) {
            if (Object.prototype.hasOwnProperty.call(extra, key)) target[key] = extra[key];
        }
        return target;
    }

    // 非管理员 / 凭据失效：整块从 DOM 移除，不留 display:none 的空壳
    function unmountAdmin() {
        var wrap = byId("adminWrap");
        if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap);
        adminRows = [];
        adminTotal = 0;
    }

    function mountAdmin() {
        if (byId("adminWrap")) return byId("adminWrap");
        var host = byId("authedBox");
        if (!host) return null;

        var wrap = makeEl("div", "admin-wrap");
        wrap.id = "adminWrap";

        // 概览
        var overviewCard = makeEl("section", "card");
        overviewCard.appendChild(makeEl("div", "card-header", "管理员 · 概览"));
        var overviewBody = makeEl("div", "card-body");
        var figures = makeEl("div", "quota-figures");
        figures.appendChild(adminFigure("adminTotalUsers", "总用户"));
        figures.appendChild(adminFigure("adminBannedUsers", "封禁用户"));
        figures.appendChild(adminFigure("adminAiToday", "今日 AI 调用"));
        overviewBody.appendChild(figures);
        var overviewMeta = makeEl("p", "quota-meta", "正在读取概览…");
        overviewMeta.id = "adminOverviewMeta";
        overviewBody.appendChild(overviewMeta);
        overviewCard.appendChild(overviewBody);
        wrap.appendChild(overviewCard);

        // 用户管理
        var usersCard = makeEl("section", "card");
        var usersHeader = makeEl("div", "card-header");
        usersHeader.appendChild(makeEl("span", "", "用户管理"));
        var countEl = makeEl("span", "count");
        countEl.id = "adminUserCount";
        usersHeader.appendChild(countEl);
        var reloadBtn = makeBtn("btn btn-ghost btn-sm admin-header-btn", "刷新", function () {
            loadAdminOverview();
            loadAdminUsers(true);
        });
        usersHeader.appendChild(reloadBtn);
        usersCard.appendChild(usersHeader);

        var usersBody = makeEl("div", "card-body");
        var statusEl = makeEl("p", "empty", "正在读取用户列表…");
        statusEl.id = "adminStatus";
        usersBody.appendChild(statusEl);
        var list = makeEl("ul", "item-list admin-user-list");
        list.id = "adminUserList";
        usersBody.appendChild(list);
        var errorEl = makeEl("p", "inline-error");
        errorEl.id = "adminUserError";
        errorEl.hidden = true;
        usersBody.appendChild(errorEl);
        var moreBtn = makeBtn("btn btn-ghost btn-sm admin-more", "载入更多", function () {
            loadAdminUsers(false);
        });
        moreBtn.id = "adminMore";
        moreBtn.hidden = true;
        usersBody.appendChild(moreBtn);
        usersCard.appendChild(usersBody);
        wrap.appendChild(usersCard);

        host.appendChild(wrap);
        return wrap;
    }

    function adminUserUrl(orcid) {
        return ADMIN_ENDPOINTS.users + "/" + encodeURIComponent(orcid);
    }

    function rowNote(row, message, ok) {
        var text = message ? String(message) : "";
        row.note.textContent = text;
        row.note.hidden = !text;
        row.note.className = "admin-note" + (text ? (ok ? " is-ok" : " is-err") : "");
    }

    function setRowBusy(row, busy) {
        row.busy = busy;
        var disabled = busy || !row.orcid;
        for (var i = 0; i < row.buttons.length; i++) {
            row.buttons[i].disabled = disabled;
        }
        if (row.input) row.input.disabled = disabled;
    }

    function refreshRow(row) {
        var data = row.data || {};
        var banned = data.is_banned === true || data.banned === true;
        var used = num(data.used_today);
        var custom = num(data.quota_limit);
        var effective = num(data.effective_limit);
        if (effective === null && custom !== null) effective = custom;

        row.node.className = "item admin-user" + (banned ? " is-banned" : "");
        row.badge.textContent = banned ? "已封禁" : "正常";
        row.badge.className = "badge admin-badge " + (banned ? "admin-badge-danger" : "admin-badge-ok");
        row.metaUsage.textContent = "今日已用 " + (used === null ? "—" : String(used)) +
            " / " + (effective === null ? "—" : String(effective)) +
            " · 配额" + (custom === null ? "默认" : "自定义");
        row.input.value = custom === null ? "" : String(custom);
        row.input.placeholder = effective === null ? "默认" : "默认 " + effective;
        row.banBtn.textContent = banned ? "解封" : "封禁";
        row.banBtn.className = "btn btn-sm " + (banned ? "btn-ghost" : "btn-danger-ghost");
    }

    function adminUserRow(item) {
        var data = (item && typeof item === "object") ? item : {};
        var orcid = String(pick(data, ["orcid_id", "orcid", "orcidId"]) || "");
        var name = pick(data, ["name", "display_name"]);
        var created = fmtDate(pick(data, ["created_at", "createdAt"]));

        var node = makeEl("li", "item admin-user");
        var head = makeEl("div", "admin-user-head");
        head.appendChild(makeEl("div", "item-title", name ? String(name) : "（未填写姓名）"));
        var badge = makeEl("span", "badge admin-badge");
        head.appendChild(badge);
        node.appendChild(head);

        node.appendChild(makeEl("p", "item-meta admin-orcid", "ORCID " + (orcid || "—")));
        node.appendChild(makeEl("p", "item-meta", "注册时间 " + (created || "—")));
        var metaUsage = makeEl("p", "item-meta");
        node.appendChild(metaUsage);

        var quotaRow = makeEl("div", "admin-quota");
        quotaRow.appendChild(makeEl("span", "admin-quota-label", "每日配额"));
        var input = document.createElement("input");
        input.type = "number";
        input.className = "admin-quota-input";
        input.setAttribute("min", String(QUOTA_MIN));
        input.setAttribute("max", String(QUOTA_MAX));
        input.setAttribute("step", "1");
        input.setAttribute("inputmode", "numeric");
        quotaRow.appendChild(input);

        var note = makeEl("p", "admin-note");
        note.hidden = true;

        var row = {
            orcid: orcid,
            data: data,
            node: node,
            badge: badge,
            metaUsage: metaUsage,
            input: input,
            note: note,
            busy: false,
            buttons: []
        };

        var saveBtn = makeBtn("btn btn-ghost btn-sm", "保存", function () { saveQuota(row, false); });
        var resetBtn = makeBtn("btn btn-ghost btn-sm", "恢复默认", function () { saveQuota(row, true); });
        quotaRow.appendChild(saveBtn);
        quotaRow.appendChild(resetBtn);
        node.appendChild(quotaRow);

        var actions = makeEl("div", "admin-actions");
        var banBtn = makeBtn("btn btn-sm", "封禁", function () { toggleBan(row); });
        var revokeBtn = makeBtn("btn btn-ghost btn-sm", "撤销会话", function () { revokeTokens(row); });
        actions.appendChild(banBtn);
        actions.appendChild(revokeBtn);
        node.appendChild(actions);
        node.appendChild(note);

        row.banBtn = banBtn;
        row.buttons = [saveBtn, resetBtn, banBtn, revokeBtn];

        refreshRow(row);
        setRowBusy(row, false);
        return row;
    }

    // 返回 null 表示格式非法；空串也视为非法（“恢复默认”走另一个按钮）
    function readQuotaInput(raw) {
        var text = String(raw === null || raw === undefined ? "" : raw).replace(/^\s+|\s+$/g, "");
        if (!/^[0-9]+$/.test(text)) return null;
        var n = parseInt(text, 10);
        if (!(n >= QUOTA_MIN && n <= QUOTA_MAX)) return null;
        return n;
    }

    function saveQuota(row, useDefault) {
        if (row.busy || !row.orcid) return;

        var payload = {};
        if (useDefault) {
            payload.quota_limit = null;
        } else {
            var n = readQuotaInput(row.input.value);
            if (n === null) {
                rowNote(row, "配额需为 " + QUOTA_MIN + "–" + QUOTA_MAX + " 的整数；想回到全局默认请点“恢复默认”。", false);
                return;
            }
            payload.quota_limit = n;
        }

        rowNote(row, "");
        setRowBusy(row, true);
        request(adminUserUrl(row.orcid), { method: "PATCH", body: JSON.stringify(payload) }).then(function (res) {
            setRowBusy(row, false);
            if (res.ok) {
                var updated = res.data && res.data.user;
                if (updated && typeof updated === "object") {
                    row.data = mergeInto(row.data, updated);
                } else {
                    row.data.quota_limit = payload.quota_limit;
                    if (payload.quota_limit !== null) row.data.effective_limit = payload.quota_limit;
                }
                refreshRow(row);
                rowNote(row, useDefault ? "已恢复全局默认配额。" : "配额已保存。", true);
                return;
            }
            if (res.kind === "unauth") {
                unmountAdmin();
                handleUnauth();
                return;
            }
            if (res.status === 403) {
                unmountAdmin();
                return;
            }
            if (res.status === 400) {
                rowNote(row, "保存失败：配额需为 " + QUOTA_MIN + "–" + QUOTA_MAX + " 的整数。", false);
                return;
            }
            rowNote(row, retryText(res, "保存失败"), false);
        });
    }

    function toggleBan(row) {
        if (row.busy || !row.orcid) return;
        var banned = row.data.is_banned === true || row.data.banned === true;
        var who = row.orcid + (row.data.name ? "（" + row.data.name + "）" : "");
        var ask = banned
            ? "确定要解封 " + who + " 吗？解封后该账号可重新登录。"
            : "确定要封禁 " + who + " 吗？\n封禁会同时撤销该用户的全部登录会话，之后无法登录。";
        if (row.orcid === accountOrcid && !banned) {
            ask += "\n注意：这是你自己的账号，封禁后你会立即失去访问权限。";
        }
        if (!window.confirm(ask)) return;

        rowNote(row, "");
        setRowBusy(row, true);
        request(adminUserUrl(row.orcid), {
            method: "PATCH",
            body: JSON.stringify({ banned: !banned })
        }).then(function (res) {
            setRowBusy(row, false);
            if (res.ok) {
                var updated = res.data && res.data.user;
                if (updated && typeof updated === "object") row.data = mergeInto(row.data, updated);
                if (row.data.is_banned !== true && row.data.banned !== true) row.data.is_banned = !banned;
                refreshRow(row);
                rowNote(row, banned ? "已解封。" : "已封禁，并撤销该用户全部会话。", true);
                return;
            }
            if (res.kind === "unauth") {
                unmountAdmin();
                handleUnauth();
                return;
            }
            if (res.status === 403) {
                unmountAdmin();
                return;
            }
            rowNote(row, retryText(res, banned ? "解封失败" : "封禁失败"), false);
        });
    }

    function revokeTokens(row) {
        if (row.busy || !row.orcid) return;
        var who = row.orcid + (row.data.name ? "（" + row.data.name + "）" : "");
        if (!window.confirm("确定要撤销 " + who + " 的全部登录会话吗？对方需要重新登录。")) return;

        rowNote(row, "");
        setRowBusy(row, true);
        request(adminUserUrl(row.orcid) + "/revoke-tokens", { method: "POST" }).then(function (res) {
            setRowBusy(row, false);
            if (res.ok) {
                var revoked = num(res.data ? res.data.revoked : null);
                rowNote(row, revoked === null ? "已撤销该用户的全部会话。" : "已撤销 " + revoked + " 个会话。", true);
                return;
            }
            if (res.kind === "unauth") {
                unmountAdmin();
                handleUnauth();
                return;
            }
            if (res.status === 403) {
                unmountAdmin();
                return;
            }
            rowNote(row, retryText(res, "撤销失败"), false);
        });
    }

    function updateAdminCount() {
        var countEl = byId("adminUserCount");
        if (countEl) {
            countEl.textContent = adminRows.length
                ? "已显示 " + adminRows.length + " / " + adminTotal
                : "";
        }
        var moreBtn = byId("adminMore");
        if (moreBtn) moreBtn.hidden = adminRows.length === 0 || adminRows.length >= adminTotal;
    }

    function loadAdminOverview() {
        var meta = byId("adminOverviewMeta");
        if (!meta) return Promise.resolve();
        meta.textContent = "正在读取概览…";

        return request(ADMIN_ENDPOINTS.overview).then(function (res) {
            if (!res.ok) {
                if (res.kind === "unauth") {
                    unmountAdmin();
                    handleUnauth();
                    return;
                }
                if (res.status === 403) {
                    unmountAdmin();
                    return;
                }
                meta.textContent = retryText(res, "概览读取失败");
                return;
            }
            var data = res.data || {};
            setFigure("adminTotalUsers", num(data.total_users));
            setFigure("adminBannedUsers", num(data.banned_users));
            setFigure("adminAiToday", num(data.ai_calls_today));
            var admins = Array.isArray(data.admins) ? data.admins : [];
            var bits = ["管理员：" + (admins.length ? admins.join("、") : "（无）")];
            var nowText = fmtDate(data.now_utc);
            if (nowText) bits.push("统计时间：" + nowText);
            meta.textContent = bits.join(" · ");
        });
    }

    function loadAdminUsers(reset) {
        var list = byId("adminUserList");
        if (!list) return Promise.resolve();

        var statusEl = byId("adminStatus");
        var moreBtn = byId("adminMore");
        var offset = reset ? 0 : adminRows.length;

        showNote(byId("adminUserError"), "");
        if (statusEl) {
            statusEl.hidden = false;
            statusEl.textContent = reset ? "正在读取用户列表…" : "正在载入更多…";
        }
        if (moreBtn) moreBtn.disabled = true;

        return request(ADMIN_ENDPOINTS.users + "?limit=" + ADMIN_PAGE_SIZE + "&offset=" + offset)
            .then(function (res) {
                if (moreBtn) moreBtn.disabled = false;
                if (!res.ok) {
                    if (statusEl) statusEl.hidden = true;
                    if (res.kind === "unauth") {
                        unmountAdmin();
                        handleUnauth();
                        return;
                    }
                    if (res.status === 403) {
                        unmountAdmin();
                        return;
                    }
                    showNote(byId("adminUserError"), retryText(res, "无法加载用户列表"));
                    return;
                }

                var data = res.data || {};
                var items = Array.isArray(data.items) ? data.items : [];
                var total = num(data.total);
                if (reset) {
                    list.textContent = "";
                    adminRows = [];
                }
                for (var i = 0; i < items.length; i++) {
                    var row = adminUserRow(items[i]);
                    list.appendChild(row.node);
                    adminRows.push(row);
                }
                if (total !== null) adminTotal = total;
                if (statusEl) {
                    statusEl.textContent = adminRows.length ? "" : "暂无用户。";
                    statusEl.hidden = adminRows.length > 0;
                }
                updateAdminCount();
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

            // 管理员区块：只有服务端明确 is_admin=true 才创建节点；其余情况连空壳都不插入
            var isAdmin = session.is_admin === true ||
                (session.user && session.user.is_admin === true);
            var adminLoads = [];
            if (isAdmin) {
                if (mountAdmin()) {
                    adminLoads.push(loadAdminOverview());
                    adminLoads.push(loadAdminUsers(true));
                }
            } else {
                unmountAdmin();
            }

            return Promise.all([loadStars(), loadEvaluations()].concat(adminLoads));
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

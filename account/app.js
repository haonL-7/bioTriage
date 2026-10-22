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

    /* ---------------- toast + 读屏播报 ---------------- */
    // 视觉反馈走右下角 toast；播报走独立的 aria-live 区域，
    // toast 2.5 秒后自动消失，Esc 或点 × 立即关闭。
    var TOAST_MS = 2500;
    var toastSeq = 0;

    function announce(message) {
        var live = byId("liveRegion");
        if (!live) return;
        var text = message ? String(message) : "";
        // 同一句话连续播报时先清空，否则部分读屏器不重读
        live.textContent = "";
        live.textContent = text;
    }

    function removeToast(node) {
        if (node && node.parentNode) node.parentNode.removeChild(node);
    }

    function dismissToasts() {
        var host = byId("toastHost");
        if (!host) return;
        while (host.firstChild) host.removeChild(host.firstChild);
    }

    function showToast(message, kind) {
        var text = message ? String(message) : "";
        if (!text) return null;
        announce(text);

        var host = byId("toastHost");
        if (!host) return null;

        var toast = document.createElement("div");
        toast.className = "toast" + (kind === "err" ? " is-err" : "");
        toast.setAttribute("data-toast-id", String(++toastSeq));

        var msg = document.createElement("span");
        msg.className = "toast-msg";
        msg.textContent = text;
        toast.appendChild(msg);

        var close = document.createElement("button");
        close.type = "button";
        close.className = "toast-close";
        close.setAttribute("aria-label", "关闭提示");
        close.textContent = "×";
        toast.appendChild(close);

        var timer = null;

        function stop() {
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
        }

        function hide() {
            stop();
            removeToast(toast);
        }

        function arm() {
            stop();
            timer = setTimeout(function () {
                timer = null;
                // 焦点还在 toast 里就先别抽走，否则键盘用户会丢焦点
                if (toast.contains(document.activeElement)) {
                    arm();
                    return;
                }
                removeToast(toast);
            }, TOAST_MS);
        }

        close.addEventListener("click", hide);
        toast.addEventListener("focusout", function () { arm(); });

        host.appendChild(toast);
        arm();
        return toast;
    }

    function bindToastEsc() {
        document.addEventListener("keydown", function (event) {
            var key = event.key;
            if (key !== "Escape" && key !== "Esc") return;
            var host = byId("toastHost");
            if (!host || !host.firstChild) return;
            dismissToasts();
        });
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

    function tierLabel(tier) {
        var map = { user: "实名用户", anon: "匿名访客", admin: "管理员" };
        var key = String(tier || "").toLowerCase();
        return map[key] || String(tier);
    }

    function quotaFigure(value) {
        var el = document.createElement("strong");
        el.textContent = value === null ? "—" : String(value);
        return el;
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

        if (byId("tierText")) byId("tierText").textContent = tier ? tierLabel(tier) : "未提供";

        // 一句话说清「今日已用 / 上限 · 剩余」
        var summary = byId("quotaSummary");
        if (summary) {
            summary.textContent = "";
            summary.appendChild(document.createTextNode("今日已用 "));
            summary.appendChild(quotaFigure(used));
            summary.appendChild(document.createTextNode(" / " + (limit === null ? "—" : String(limit))));
            var sep = document.createElement("span");
            sep.className = "qs-sep";
            sep.textContent = " · ";
            summary.appendChild(sep);
            summary.appendChild(document.createTextNode("剩余 "));
            summary.appendChild(quotaFigure(remaining));
        }

        // 进度条：同时作为 role=progressbar 的可读数值来源
        var max = (limit === null || limit <= 0) ? 0 : limit;
        var now = (used === null || used < 0) ? 0 : used;
        if (max > 0 && now > max) now = max;

        var bar = byId("quotaBar");
        if (bar) {
            bar.setAttribute("aria-valuemin", "0");
            bar.setAttribute("aria-valuemax", String(max));
            bar.setAttribute("aria-valuenow", String(now));
            bar.setAttribute("aria-valuetext",
                "今日已用 " + (used === null ? "未知" : String(used)) +
                " / " + (limit === null ? "未知" : String(limit)) +
                " 次，剩余 " + (remaining === null ? "未知" : String(remaining)) + " 次");
        }

        var fill = byId("quotaFill");
        if (fill) {
            fill.style.width = max > 0 ? Math.min(Math.max(now / max, 0), 1) * 100 + "%" : "0";
            fill.className = "quota-fill" + (remaining === 0 && used !== null ? " is-full" : "");
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
        var titleEl = row ? row.querySelector(".item-title") : null;
        var starTitle = titleEl ? String(titleEl.textContent || "") : "";
        var original = btn.textContent;

        btn.disabled = true;
        btn.textContent = "移除中…";
        showNote(note, "");

        request(ENDPOINTS.stars + "/" + encodeURIComponent(id), { method: "DELETE" }).then(function (res) {
            if (res.ok) {
                if (row && row.parentNode) row.parentNode.removeChild(row);
                refreshStarCount();
                showToast(starTitle ? "已移除收藏：" + starTitle : "已移除收藏。");
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
                    showToast("已登出当前账户。");
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
    // 一轮 20 条：超过 20 条时列表尾部出现“载入更多”，按 limit/offset 追加
    var ADMIN_PAGE_SIZE = 20;
    var QUOTA_MIN = 1;
    var QUOTA_MAX = 1000;
    var adminRows = [];
    var adminTotal = 0;
    var adminQuery = "";

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

    function adminLabel(en, zh, countId, labelId) {
        var label = makeEl("span", "section-label");
        label.id = labelId;
        label.appendChild(document.createTextNode(en));
        label.appendChild(makeEl("span", "sl-zh", zh));
        if (countId) {
            var count = makeEl("span", "count");
            count.id = countId;
            label.appendChild(count);
        }
        return label;
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
        overviewCard.setAttribute("aria-labelledby", "adminOverviewLabel");
        overviewCard.appendChild(adminLabel("OVERVIEW", "管理员概览", null, "adminOverviewLabel"));
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
        usersCard.setAttribute("aria-labelledby", "adminUsersLabel");
        usersCard.appendChild(adminLabel("USERS", "用户管理", "adminUserCount", "adminUsersLabel"));

        var usersBody = makeEl("div", "card-body");

        // 纯前端过滤：只筛已载入的行，不改任何请求参数
        var toolbar = makeEl("div", "admin-toolbar");
        var search = document.createElement("input");
        search.type = "search";
        search.className = "admin-search";
        search.id = "adminSearch";
        search.setAttribute("placeholder", "搜索 ORCID 或姓名…");
        search.setAttribute("aria-label", "按 ORCID 或姓名过滤用户列表");
        search.setAttribute("autocomplete", "off");
        search.addEventListener("input", applyAdminFilter);
        toolbar.appendChild(search);
        toolbar.appendChild(makeBtn("btn btn-ghost btn-sm admin-refresh", "刷新", function () {
            loadAdminOverview();
            loadAdminUsers(true);
        }));
        usersBody.appendChild(toolbar);
        var filterNote = makeEl("p", "admin-filter-note");
        filterNote.id = "adminFilterNote";
        filterNote.setAttribute("aria-live", "polite");
        usersBody.appendChild(filterNote);

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
        row.banBtn.setAttribute("aria-label", (banned ? "解封 " : "封禁 ") + (row.orcid || "该用户"));
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
        input.setAttribute("aria-label", "每日 AI 分析配额（" + QUOTA_MIN + "–" + QUOTA_MAX + " 的整数，用户 " + (orcid || "未知") + "）");
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
        saveBtn.setAttribute("aria-label", "保存 " + (orcid || "该用户") + " 的每日配额");
        var resetBtn = makeBtn("btn btn-ghost btn-sm", "恢复默认", function () { saveQuota(row, true); });
        resetBtn.setAttribute("aria-label", "把 " + (orcid || "该用户") + " 的配额恢复为全局默认");
        quotaRow.appendChild(saveBtn);
        quotaRow.appendChild(resetBtn);
        node.appendChild(quotaRow);

        var actions = makeEl("div", "admin-actions");
        var banBtn = makeBtn("btn btn-sm", "封禁", function () { toggleBan(row); });
        var revokeBtn = makeBtn("btn btn-ghost btn-sm", "撤销会话", function () { revokeTokens(row); });
        revokeBtn.setAttribute("aria-label", "撤销 " + (orcid || "该用户") + " 的全部登录会话");
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
                showToast(useDefault
                    ? "已恢复全局默认配额：" + row.orcid
                    : "配额已保存：" + row.orcid + " 每日 " + payload.quota_limit + " 次");
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
                showToast(banned
                    ? "已解封：" + row.orcid
                    : "已封禁 " + row.orcid + "，并撤销其全部登录会话");
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
                showToast(revoked === null
                    ? "已撤销全部登录会话：" + row.orcid
                    : "已撤销 " + revoked + " 个登录会话：" + row.orcid);
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

    function updateAdminCount(shown) {
        var countEl = byId("adminUserCount");
        if (countEl) {
            if (!adminRows.length) {
                countEl.textContent = "";
            } else if (adminQuery && typeof shown === "number") {
                countEl.textContent = "匹配 " + shown + " / 已载入 " + adminRows.length + " / 共 " + adminTotal;
            } else {
                countEl.textContent = "已载入 " + adminRows.length + " / 共 " + adminTotal;
            }
        }
        var moreBtn = byId("adminMore");
        if (moreBtn) {
            var rest = adminTotal - adminRows.length;
            var hasMore = adminRows.length > 0 && rest > 0;
            moreBtn.hidden = !hasMore;
            moreBtn.setAttribute("aria-label", hasMore
                ? "载入更多用户，还有 " + rest + " 条未载入"
                : "载入更多用户");
        }
    }

    function rowMatchesQuery(row, query) {
        if (!query) return true;
        var orcid = String(row.orcid || "").toLowerCase();
        var data = row.data || {};
        var name = String(String(pick(data, ["name", "display_name"]) || "")).toLowerCase();
        return orcid.indexOf(query) !== -1 || name.indexOf(query) !== -1;
    }

    // 只过滤已载入的行：不发额外请求、不改 API 契约
    function applyAdminFilter() {
        var input = byId("adminSearch");
        adminQuery = input ? String(input.value || "").trim().toLowerCase() : "";

        var shown = 0;
        for (var i = 0; i < adminRows.length; i++) {
            var match = rowMatchesQuery(adminRows[i], adminQuery);
            adminRows[i].node.hidden = !match;
            if (match) shown++;
        }

        var note = byId("adminFilterNote");
        if (note) {
            if (!adminRows.length) {
                note.textContent = "";
            } else if (adminQuery && shown === 0) {
                note.textContent = "没有匹配「" + adminQuery + "」的用户；搜索只覆盖已载入的 " +
                    adminRows.length + " 条，可先载入更多。";
            } else {
                note.textContent = "";
            }
        }
        updateAdminCount(shown);
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
                applyAdminFilter();
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
        bindToastEsc();

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

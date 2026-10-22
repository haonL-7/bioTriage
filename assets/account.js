/* ============================================================
   account.js — 账户页与全站顶部账户入口（原生 JS，无构建步骤）
   - 同源 Cookie 会话：所有请求带 credentials: "same-origin"
   - 不读取、不存储任何 token / 用户标识
   - 401 一律展示未登录态，不做弹窗报错
   - 每个请求默认 8 秒超时（云函数冷启动/数据库唤醒），超时按未登录展示，不无限转圈
   ============================================================ */
(function () {
    "use strict";

    var ENDPOINTS = {
        me: "/api/auth/me",
        stars: "/api/me/stars",
        evaluations: "/api/me/evaluations",
        login: "/api/auth/login",
        logout: "/api/auth/logout"
    };

    // 云函数冷启动 + Neon 唤醒常需 3~8 秒，超时后按未登录展示而不是永久等待
    var REQUEST_TIMEOUT_MS = 8000;
    // 加载态二次提示：3 秒仍未完成则改文案（纯前端计时，不请求后端）
    var LOADING_HINT_MS = 3000;
    var LOADING_TEXT = "正在读取账户信息…";
    var LOADING_HINT_TEXT = "正在唤醒服务，首次访问可能需要 5–10 秒…";
    var loadingHintTimer = null;

    /* ---------------- 请求封装 ---------------- */
    // 返回 { ok, kind, status, data }；kind: network | unauth | http | timeout
    // 永不 reject：超时用 AbortController 中断，统一返回 kind="timeout"
    function request(url, options) {
        var opts = options || {};
        var timeoutMs = typeof opts.timeoutMs === "number" ? opts.timeoutMs : REQUEST_TIMEOUT_MS;
        var init = {
            method: opts.method || "GET",
            credentials: "same-origin",
            headers: Object.assign({ "Accept": "application/json" }, opts.headers || {})
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
                pending = fetch(url, init);
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
        return (n < 10 ? "0" : "") + n;
    }

    function fmtDate(v) {
        if (v === null || v === undefined || v === "") return "";
        var d = new Date(typeof v === "number" && v < 1e12 ? v * 1000 : v);
        if (isNaN(d.getTime())) return String(v);
        return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) +
            " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    }

    function kindLabel(kind) {
        var map = { paper: "论文", doi: "DOI", evaluation: "评估", news: "资讯", note: "笔记" };
        var key = String(kind).toLowerCase();
        return map[key] || String(kind);
    }

    // 失败文案统一出口：网络失败 / 超时 / HTTP 状态各给一句可读中文
    function retryText(res, prefix) {
        if (res.kind === "timeout") return prefix + "：服务响应超时（可能在冷启动），请重试。";
        if (res.kind === "network") return prefix + "：网络连接失败，请重试。";
        return prefix + "（HTTP " + res.status + "），请重试。";
    }

    function showNote(el, message) {
        if (!el) return;
        el.textContent = message || "";
        el.hidden = !message;
    }

    /* ---------------- 顶部账户入口 ---------------- */
    // index.html 里 <a data-account-entry>：未登录显示「登录」，已登录显示「我的」
    function setNavEntry(authenticated) {
        var link = document.querySelector("[data-account-entry]");
        if (!link) return;
        if (authenticated) {
            link.textContent = "我的";
            link.setAttribute("href", "/account");
        } else {
            link.textContent = "登录";
            link.setAttribute("href", ENDPOINTS.login);
        }
    }

    async function refreshNavOnly() {
        var res = await request(ENDPOINTS.me);
        setNavEntry(Boolean(res.ok && res.data && res.data.authenticated));
    }

    /* ---------------- 加载态 ---------------- */
    function stopLoadingHint() {
        if (loadingHintTimer) {
            clearTimeout(loadingHintTimer);
            loadingHintTimer = null;
        }
    }

    // 3 秒仍未完成则把加载文案改成二次提示；纯前端计时，不触发额外请求
    function startLoadingHint() {
        stopLoadingHint();
        var textEl = byId("loadingText");
        if (!textEl) return;
        textEl.textContent = LOADING_TEXT;
        loadingHintTimer = setTimeout(function () {
            loadingHintTimer = null;
            var el = byId("loadingText");
            if (el) el.textContent = LOADING_HINT_TEXT;
        }, LOADING_HINT_MS);
    }

    // 任何分支都必须隐藏 loading，避免首屏永久 spinner
    function hideLoading() {
        stopLoadingHint();
        var loading = byId("loadingBox");
        if (loading) loading.hidden = true;
    }

    /* ---------------- 页面状态切换 ---------------- */
    function showGuest(note) {
        hideLoading();
        var authed = byId("authedBox");
        if (authed) authed.hidden = true;
        var guest = byId("guestBox");
        if (guest) guest.hidden = false;
        showNote(byId("guestNote"), note || "");
        setNavEntry(false);
    }

    function showAuthed() {
        hideLoading();
        var guest = byId("guestBox");
        if (guest) guest.hidden = true;
        var authed = byId("authedBox");
        if (authed) authed.hidden = false;
        setNavEntry(true);
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

        var metaBits = [];
        var doi = pick(item, ["doi"]);
        if (doi) metaBits.push("DOI " + doi);
        var created = fmtDate(pick(item, ["created_at", "createdAt", "time"]));
        if (created) metaBits.push("收藏于 " + created);
        if (metaBits.length) {
            var meta = document.createElement("p");
            meta.className = "item-meta";
            meta.textContent = metaBits.join(" · ");
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

    async function removeStar(event) {
        var btn = event.currentTarget;
        var id = btn.getAttribute("data-star-id");
        if (!id) return;

        var row = btn.closest ? btn.closest(".item") : null;
        var note = row ? row.querySelector(".item-note") : null;
        var original = btn.textContent;

        btn.disabled = true;
        btn.textContent = "移除中…";
        showNote(note, "");

        var res = await request(ENDPOINTS.stars + "/" + encodeURIComponent(id), { method: "DELETE" });

        if (res.ok) {
            if (row && row.parentNode) row.parentNode.removeChild(row);
            refreshStarCount();
            return;
        }

        btn.disabled = false;
        btn.textContent = original;

        if (res.kind === "unauth") {
            showGuest("登录状态已失效，请重新登录。");
            return;
        }
        showNote(note, retryText(res, "移除失败"));
    }

    async function loadStars() {
        var list = byId("starList");
        var errEl = byId("starError");
        if (!list) return;

        var res = await request(ENDPOINTS.stars);

        if (!res.ok) {
            list.textContent = "";
            refreshStarCount();
            if (res.kind === "unauth") {
                showGuest("登录状态已失效，请重新登录。");
                return;
            }
            showNote(errEl, retryText(res, "无法加载收藏列表"));
            return;
        }

        showNote(errEl, "");
        var items = (res.data && Array.isArray(res.data.items)) ? res.data.items : [];
        list.textContent = "";
        items.forEach(function (item) {
            list.appendChild(starRow(item));
        });
        refreshStarCount();
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

        var metaBits = [];
        var created = fmtDate(pick(item, ["created_at", "createdAt", "evaluated_at", "time"]));
        if (created) metaBits.push(created);
        var score = pick(item, ["score", "total_score", "value"]);
        if (score !== null) metaBits.push("评分 " + score);
        if (metaBits.length) {
            var meta = document.createElement("p");
            meta.className = "item-meta";
            meta.textContent = metaBits.join(" · ");
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

    async function loadEvaluations() {
        var list = byId("evalList");
        var errEl = byId("evalError");
        if (!list) return;

        var res = await request(ENDPOINTS.evaluations);

        if (!res.ok) {
            list.textContent = "";
            if (byId("evalEmpty")) byId("evalEmpty").hidden = true;
            if (byId("evalCount")) byId("evalCount").textContent = "";
            if (res.kind === "unauth") {
                showGuest("登录状态已失效，请重新登录。");
                return;
            }
            showNote(errEl, retryText(res, "无法加载评估历史"));
            return;
        }

        showNote(errEl, "");
        var items = (res.data && Array.isArray(res.data.items)) ? res.data.items : [];
        list.textContent = "";
        items.forEach(function (item) {
            list.appendChild(evalRow(item));
        });
        if (byId("evalCount")) {
            byId("evalCount").textContent = items.length ? "共 " + items.length + " 条" : "";
        }
        if (byId("evalEmpty")) byId("evalEmpty").hidden = items.length !== 0;
    }

    /* ---------------- 登出 ---------------- */
    function bindLogout() {
        var btn = byId("logoutBtn");
        if (!btn || btn.getAttribute("data-bound") === "1") return;
        btn.setAttribute("data-bound", "1");

        btn.addEventListener("click", async function () {
            var noteEl = byId("logoutNote");
            btn.disabled = true;
            showNote(noteEl, "");

            var res = await request(ENDPOINTS.logout, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: "{}"
            });

            if (res.ok || res.kind === "unauth") {
                showGuest("已登出。");
                return;
            }

            btn.disabled = false;
            showNote(noteEl, retryText(res, "登出失败"));
        });
    }

    /* ---------------- 页面初始化 ---------------- */
    async function startAccountPage() {
        var me = await request(ENDPOINTS.me);

        if (!me.ok) {
            if (me.kind === "unauth") {
                showGuest("");
            } else if (me.kind === "network") {
                showGuest("暂时无法连接服务器，请检查网络后刷新页面。");
            } else if (me.kind === "timeout") {
                showGuest("服务响应较慢（云函数冷启动中），已按未登录展示；稍后刷新即可。");
            } else {
                showGuest("账户服务暂不可用（HTTP " + me.status + "），请稍后刷新重试。");
            }
            return;
        }

        var session = me.data || {};
        if (!session.authenticated || !session.user) {
            showGuest("");
            return;
        }

        showAuthed();
        renderUser(session.user);
        renderQuota(session.quota);
        bindLogout();

        await Promise.all([loadStars(), loadEvaluations()]);
    }

    function boot() {
        var loading = byId("loadingBox");
        if (loading && !loading.hidden) startLoadingHint();
        try {
            if (byId("authedBox")) {
                // startAccountPage 是 async：必须显式 catch，否则异常会绕过 try/catch 让 spinner 永久停留
                startAccountPage().catch(function () {
                    showGuest("账户信息读取失败，已按未登录展示；请稍后刷新重试。");
                });
            } else {
                refreshNavOnly().catch(function () {
                    setNavEntry(false);
                });
            }
        } catch (err) {
            showGuest("账户信息读取失败，已按未登录展示；请稍后刷新重试。");
            setNavEntry(false);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();

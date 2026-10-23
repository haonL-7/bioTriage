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
    var currentToken = "";
    var accountOrcid = "";
    var loadingHintTimer = null;
    var watchdogTimer = null;

    /* ---------------- 语言：字典 + 取词 + 切换 ----------------
       与主站 biotriage.html 共用 localStorage 键 "bt-lang"，写入值同为 "zh" / "en"
       （主站 detectLang 只认这两个值）；读取时额外容忍 "zh-CN" 等变体。
       token 仍只走 sessionStorage，localStorage 仅此一个键。 */
    var LANG_KEY = "bt-lang";
    var currentLang = "zh-CN";

    var DICT = {
        "zh-CN": {
            "lang-toggle-aria": "切换语言",
            "doc-title": "我的账户 · bio|Triage",
            "skip": "跳到主要内容",
            "nav-tagline": "证据结构化文献智能",
            "nav-home": "首页",
            "nav-evidence": "证据监测器",
            "nav-daily": "文献日报",
            "nav-weekly": "文献周报",
            "nav-method": "方法学",
            "nav-starred": "星级收藏",
            "nav-aa": "学术助手",
            "nav-account": "我的账户",
            "side-nav-label": "主导航",
            "side-label": "导航",
            "topbar-label": "站点顶栏",
            "page-title": "我的账户",
            "page-title-accent": "Account",
            "header-tagline": "用 ORCID 登录，统一管理你的 AI 分析配额、收藏条目与评估历史。",
            "header-meta": "每日 5 次 AI 分析 · 收藏跨设备同步 · 评估历史留档",
            "loading": "正在读取账户信息…",
            "loading-hint": "正在唤醒服务，首次访问可能需要 5–10 秒…",
            "generic-fail": "账户信息读取失败，请稍后刷新重试。",
            "sl-signin": "SIGN IN",
            "sl-signin-zh": "登录",
            "sl-after": "AFTER LOGIN",
            "sl-after-zh": "登录后你可以",
            "sl-account": "ACCOUNT",
            "sl-account-zh": "账户信息",
            "sl-quota": "QUOTA",
            "sl-quota-zh": "今日配额",
            "sl-star": "STARRED",
            "sl-star-zh": "我的收藏",
            "sl-history": "HISTORY",
            "sl-history-zh": "评估历史",
            "guest-title": "用 ORCID 登录，解锁账户能力",
            "guest-text": "登录后本站以你的 ORCID iD 作为唯一身份；凭据只保存在当前标签页。登录后可获得每日 5 次 AI 分析、收藏跨设备同步与可回看的评估历史。",
            "login-btn": "使用 ORCID 登录",
            "value1-k": "每日 5 次 AI 分析",
            "value1-d": "按 ORCID 计次发放，每天自动重置；用完后次日恢复。",
            "value2-k": "收藏跨设备同步",
            "value2-d": "收藏的论文与条目跟随账户，换设备登录同样能找回。",
            "value3-k": "评估历史可回看",
            "value3-d": "每次 AI 证据评估都会留档，结论、评分与层级随时复查。",
            "kv-orcid": "ORCID iD",
            "kv-name": "显示名",
            "kv-tier": "配额档位",
            "logout": "登出",
            "star-empty-title": "还没有收藏",
            "star-empty-text": "在证据监测或文献日报里点条目上的 ★ 收藏，之后在任何设备登录都能看到。",
            "star-empty-link": "去证据监测挑几条 →",
            "eval-empty-title": "暂无评估记录",
            "eval-empty-text": "在证据监测页发起一次 AI 分析后，结论会留在这里，随时可以回看。",
            "eval-empty-link": "去发起一次 AI 分析 →",
            "foot-note": "登录状态只在本标签页有效，关闭标签页后需要重新登录。",
            "foot-tech": "技术说明",
            "foot-tech-body": "凭据仅存放在当前标签页的 sessionStorage 中，关闭标签页即失效；本页只把它作为 Bearer 头发送给本站后端，不会发送给其他地址。",
            "noscript-text": "需要启用 JavaScript 才能显示账户信息。",
            "noscript-link": "直接前往 ORCID 登录",
            "sep": "：",
            "sep-list": "、",
            "net-fail": "网络连接失败，请检查网络后重试。",
            "timeout-fail": "服务响应超时（可能正在冷启动），请稍后重试。",
            "http-fail": "服务返回 HTTP {0}，请稍后重试。",
            "generic-retry": "请稍后重试。",
            "watchdog": "服务响应较慢，已停止等待；请稍后刷新页面重试。",
            "offline-guest": "暂时无法连接服务器，请检查网络后刷新页面。",
            "slow-guest": "服务响应较慢（云函数冷启动中），已按未登录展示；稍后刷新即可。",
            "http-guest": "账户服务暂不可用（HTTP {0}），请稍后刷新重试。",
            "not-provided": "未提供",
            "tier-user": "实名用户",
            "tier-anon": "匿名访客",
            "tier-admin": "管理员",
            "kind-paper": "论文",
            "kind-trial": "试验",
            "kind-strain": "菌株",
            "kind-metabolite": "代谢物",
            "kind-topic": "主题",
            "used-today": "今日已用 ",
            "remaining": "剩余 ",
            "unknown": "未知",
            "quota-value-text": "今日已用 {0} / {1} 次，剩余 {2} 次",
            "reset-at": "配额重置时间：{0}",
            "reset-note": "配额重置时间以服务端返回为准。",
            "count": "共 {0} 条",
            "untitled-item": "（未命名条目）",
            "starred-at": "收藏于 {0}",
            "remove": "移除",
            "removing": "移除中…",
            "removed-toast": "已移除收藏：{0}",
            "removed-toast-generic": "已移除收藏。",
            "remove-fail": "移除失败",
            "stars-load-fail": "无法加载收藏列表",
            "untitled-eval": "（未命名评估）",
            "score": "评分 {0}",
            "evals-load-fail": "无法加载评估历史",
            "logged-out": "已登出当前账户。",
            "logout-local-note": "已在本机退出登录；服务器未确认撤销（{0}），如有顾虑请稍后重新登录再登出。",
            "net-short": "网络连接失败",
            "timeout-short": "响应超时",
            "close-toast": "关闭提示",
            "sl-overview": "管理员概览",
            "sl-users": "用户管理",
            "kpi-total": "总用户",
            "kpi-banned": "封禁用户",
            "kpi-ai": "今日 AI 调用",
            "admin-overview-loading": "正在读取概览…",
            "admin-search-ph": "搜索 ORCID 或姓名…",
            "admin-search-aria": "按 ORCID 或姓名过滤用户列表",
            "refresh": "刷新",
            "admin-users-loading": "正在读取用户列表…",
            "load-more": "载入更多",
            "badge-banned": "已封禁",
            "badge-active": "正常",
            "admin-usage": "今日已用 {0} / {1} · 配额{2}",
            "quota-default": "默认",
            "quota-custom": "自定义",
            "default-ph": "默认",
            "default-ph-n": "默认 {0}",
            "ban": "封禁",
            "unban": "解封",
            "aria-ban": "封禁 {0}",
            "aria-unban": "解封 {0}",
            "no-name": "（未填写姓名）",
            "registered": "注册时间 {0}",
            "daily-quota": "每日配额",
            "aria-quota-input": "每日 AI 分析配额（{0}–{1} 的整数，用户 {2}）",
            "save": "保存",
            "reset-default": "恢复默认",
            "aria-save-quota": "保存 {0} 的每日配额",
            "aria-reset-quota": "把 {0} 的配额恢复为全局默认",
            "revoke": "撤销会话",
            "aria-revoke": "撤销 {0} 的全部登录会话",
            "this-user": "该用户",
            "who": "{0}（{1}）",
            "quota-invalid": "配额需为 {0}–{1} 的整数；想回到全局默认请点“恢复默认”。",
            "quota-reset-done": "已恢复全局默认配额。",
            "quota-saved": "配额已保存。",
            "toast-quota-reset": "已恢复全局默认配额：{0}",
            "toast-quota-saved": "配额已保存：{0} 每日 {1} 次",
            "save-fail-invalid": "保存失败：配额需为 {0}–{1} 的整数。",
            "save-fail": "保存失败",
            "confirm-unban": "确定要解封 {0} 吗？解封后该账号可重新登录。",
            "confirm-ban": "确定要封禁 {0} 吗？\n封禁会同时撤销该用户的全部登录会话，之后无法登录。",
            "confirm-self": "\n注意：这是你自己的账号，封禁后你会立即失去访问权限。",
            "banned-done": "已封禁，并撤销该用户全部会话。",
            "unbanned-done": "已解封。",
            "toast-unban": "已解封：{0}",
            "toast-ban": "已封禁 {0}，并撤销其全部登录会话",
            "ban-fail": "封禁失败",
            "unban-fail": "解封失败",
            "confirm-revoke": "确定要撤销 {0} 的全部登录会话吗？对方需要重新登录。",
            "revoked-all": "已撤销该用户的全部会话。",
            "revoked-n": "已撤销 {0} 个会话。",
            "toast-revoke-all": "已撤销全部登录会话：{0}",
            "toast-revoke-n": "已撤销 {0} 个登录会话：{1}",
            "revoke-fail": "撤销失败",
            "count-filtered": "匹配 {0} / 已载入 {1} / 共 {2}",
            "count-loaded": "已载入 {0} / 共 {1}",
            "aria-more-n": "载入更多用户，还有 {0} 条未载入",
            "aria-more": "载入更多用户",
            "no-match": "没有匹配「{0}」的用户；搜索只覆盖已载入的 {1} 条，可先载入更多。",
            "overview-fail": "概览读取失败",
            "admins-line": "管理员：{0}",
            "none": "（无）",
            "stat-time": "统计时间：{0}",
            "loading-more": "正在载入更多…",
            "users-fail": "无法加载用户列表",
            "no-users": "暂无用户。"
        },
        en: {
            "lang-toggle-aria": "Switch language",
            "doc-title": "My Account · bio|Triage",
            "skip": "Skip to main content",
            "nav-tagline": "Evidence-structured literature intelligence",
            "nav-home": "Home",
            "nav-evidence": "Evidence Monitor",
            "nav-daily": "Literature Daily",
            "nav-weekly": "Literature Weekly",
            "nav-method": "Methodology",
            "nav-starred": "Starred",
            "nav-aa": "Academic Assistant",
            "nav-account": "My Account",
            "side-nav-label": "Main navigation",
            "side-label": "Navigate",
            "topbar-label": "Site header",
            "page-title": "My",
            "page-title-accent": "Account",
            "header-tagline": "Sign in with ORCID to manage your AI analysis quota, starred items, and evaluation history in one place.",
            "header-meta": "5 AI analyses per day · Stars sync across devices · Evaluation history archived",
            "loading": "Loading your account…",
            "loading-hint": "Waking the service up; a first visit can take 5–10 seconds…",
            "generic-fail": "Could not load your account. Please refresh and try again.",
            "sl-signin": "SIGN IN",
            "sl-signin-zh": "",
            "sl-after": "AFTER LOGIN",
            "sl-after-zh": "",
            "sl-account": "ACCOUNT",
            "sl-account-zh": "",
            "sl-quota": "QUOTA",
            "sl-quota-zh": "",
            "sl-star": "STARRED",
            "sl-star-zh": "",
            "sl-history": "HISTORY",
            "sl-history-zh": "",
            "guest-title": "Sign in with ORCID to unlock your account",
            "guest-text": "This site identifies you by your ORCID iD alone, and your credentials stay in this tab. Signing in gives you 5 AI analyses per day, stars that sync across devices, and an evaluation history you can revisit.",
            "login-btn": "Sign in with ORCID",
            "value1-k": "5 AI analyses per day",
            "value1-d": "Counted per ORCID iD and reset daily; the allowance returns the next day.",
            "value2-k": "Stars sync across devices",
            "value2-d": "Starred papers and items follow your account, so a new device finds them too.",
            "value3-k": "Evaluation history, revisited",
            "value3-d": "Every AI evidence assessment is archived, so the verdict, score, and tier stay reviewable.",
            "kv-orcid": "ORCID iD",
            "kv-name": "Display name",
            "kv-tier": "Plan",
            "logout": "Sign out",
            "star-empty-title": "No starred items yet",
            "star-empty-text": "Tap ★ on an item in Evidence Monitor or Literature Daily, then sign in anywhere to see it again.",
            "star-empty-link": "Browse Evidence Monitor →",
            "eval-empty-title": "No evaluations yet",
            "eval-empty-text": "Run an AI analysis in Evidence Monitor and its verdict is archived here for later review.",
            "eval-empty-link": "Run an AI analysis →",
            "foot-note": "Your session lasts only in this tab; closing the tab signs you out.",
            "foot-tech": "Technical notes",
            "foot-tech-body": "Credentials are kept only in this tab sessionStorage and expire when the tab closes. This page sends them to this site backend as a Bearer header and to no other address.",
            "noscript-text": "JavaScript is required to display account information.",
            "noscript-link": "Go straight to ORCID sign-in",
            "sep": ": ",
            "sep-list": ", ",
            "net-fail": "Network error. Check your connection and try again.",
            "timeout-fail": "The server timed out (it may be cold-starting). Please try again.",
            "http-fail": "The server returned HTTP {0}. Please try again.",
            "generic-retry": "Please try again later.",
            "watchdog": "The service is slow, so we stopped waiting. Please refresh the page and try again.",
            "offline-guest": "Cannot reach the server. Check your connection and refresh the page.",
            "slow-guest": "The service is slow (cold start), so the page is shown as signed out. Refresh later.",
            "http-guest": "The account service is unavailable (HTTP {0}). Please refresh later.",
            "not-provided": "Not provided",
            "tier-user": "Verified user",
            "tier-anon": "Guest",
            "tier-admin": "Admin",
            "kind-paper": "Paper",
            "kind-trial": "Trial",
            "kind-strain": "Strain",
            "kind-metabolite": "Metabolite",
            "kind-topic": "Topic",
            "used-today": "Used today ",
            "remaining": "Remaining ",
            "unknown": "unknown",
            "quota-value-text": "Used today {0} of {1}, {2} remaining",
            "reset-at": "Quota resets at: {0}",
            "reset-note": "Reset time follows what the server returns.",
            "count": "{0} total",
            "untitled-item": "(Untitled item)",
            "starred-at": "Starred {0}",
            "remove": "Remove",
            "removing": "Removing…",
            "removed-toast": "Star removed: {0}",
            "removed-toast-generic": "Star removed.",
            "remove-fail": "Could not remove",
            "stars-load-fail": "Could not load starred items",
            "untitled-eval": "(Untitled evaluation)",
            "score": "Score {0}",
            "evals-load-fail": "Could not load the evaluation history",
            "logged-out": "You are signed out.",
            "logout-local-note": "Signed out locally, but the server did not confirm revocation ({0}). If that concerns you, sign in again and sign out later.",
            "net-short": "network error",
            "timeout-short": "timeout",
            "close-toast": "Close notification",
            "sl-overview": "Admin overview",
            "sl-users": "User management",
            "kpi-total": "Total users",
            "kpi-banned": "Banned users",
            "kpi-ai": "AI calls today",
            "admin-overview-loading": "Loading the overview…",
            "admin-search-ph": "Search ORCID or name…",
            "admin-search-aria": "Filter the user list by ORCID or name",
            "refresh": "Refresh",
            "admin-users-loading": "Loading users…",
            "load-more": "Load more",
            "badge-banned": "Banned",
            "badge-active": "Active",
            "admin-usage": "Used today {0} / {1} · {2}",
            "quota-default": "default quota",
            "quota-custom": "custom quota",
            "default-ph": "Default",
            "default-ph-n": "Default {0}",
            "ban": "Ban",
            "unban": "Unban",
            "aria-ban": "Ban {0}",
            "aria-unban": "Unban {0}",
            "no-name": "(No name)",
            "registered": "Registered {0}",
            "daily-quota": "Daily quota",
            "aria-quota-input": "Daily AI analysis quota (an integer from {0} to {1}) for user {2}",
            "save": "Save",
            "reset-default": "Reset to default",
            "aria-save-quota": "Save the daily quota for {0}",
            "aria-reset-quota": "Reset the quota for {0} to the global default",
            "revoke": "Revoke sessions",
            "aria-revoke": "Revoke all sign-in sessions for {0}",
            "this-user": "this user",
            "who": "{0} ({1})",
            "quota-invalid": "The quota must be an integer from {0} to {1}; use \"Reset to default\" for the global default.",
            "quota-reset-done": "Restored the global default quota.",
            "quota-saved": "Quota saved.",
            "toast-quota-reset": "Restored the global default quota for {0}",
            "toast-quota-saved": "Quota saved for {0}: {1} per day",
            "save-fail-invalid": "Save failed: the quota must be an integer from {0} to {1}.",
            "save-fail": "Could not save",
            "confirm-unban": "Unban {0}? The account will be able to sign in again.",
            "confirm-ban": "Ban {0}?\nBanning also revokes all of this user sign-in sessions, so they cannot sign in.",
            "confirm-self": "\nNote: this is your own account. You will lose access immediately.",
            "banned-done": "Banned, and all of this user sessions were revoked.",
            "unbanned-done": "Unbanned.",
            "toast-unban": "Unbanned {0}",
            "toast-ban": "Banned {0} and revoked all their sessions",
            "ban-fail": "Could not ban",
            "unban-fail": "Could not unban",
            "confirm-revoke": "Revoke all sign-in sessions for {0}? They will need to sign in again.",
            "revoked-all": "All sessions for this user were revoked.",
            "revoked-n": "Revoked {0} sessions.",
            "toast-revoke-all": "All sessions revoked for {0}",
            "toast-revoke-n": "Revoked {0} sessions for {1}",
            "revoke-fail": "Could not revoke",
            "count-filtered": "Matching {0} / loaded {1} / total {2}",
            "count-loaded": "Loaded {0} / total {1}",
            "aria-more-n": "Load more users; {0} still not loaded",
            "aria-more": "Load more users",
            "no-match": "No users match \"{0}\". Search covers only the {1} loaded users; load more first.",
            "overview-fail": "Could not load the overview",
            "admins-line": "Admins: {0}",
            "none": "(none)",
            "stat-time": "Measured at: {0}",
            "loading-more": "Loading more…",
            "users-fail": "Could not load the user list",
            "no-users": "No users yet."
        }
    };

    // 运行时状态：语言切换后据此原地重绘，不重新发请求
    var view = {
        mode: "",
        guestKey: "",
        guestArgs: [],
        user: null,
        quota: null,
        stars: [],
        evals: [],
        starsLoaded: false,
        evalsLoaded: false
    };

    function t(key, p0, p1, p2) {
        var table = DICT[currentLang] || DICT["zh-CN"];
        var s = table[key];
        if (s === undefined || s === null) s = DICT["zh-CN"][key];
        if (s === undefined || s === null) return String(key);
        var args = [p0, p1, p2];
        return String(s).replace(/\{(\d)\}/g, function (m, i) {
            var v = args[Number(i)];
            return v === undefined || v === null ? "" : String(v);
        });
    }

    function readLang() {
        var stored = "";
        try {
            stored = String(window.localStorage.getItem(LANG_KEY) || "");
        } catch (err) { stored = ""; }
        var s = stored.toLowerCase();
        if (s.indexOf("zh") === 0) return "zh-CN";
        if (s.indexOf("en") === 0) return "en";
        var nav = "";
        try {
            nav = String(navigator.language || navigator.userLanguage || "").toLowerCase();
        } catch (err2) { nav = ""; }
        if (!nav) return "zh-CN";
        return nav.indexOf("zh") === 0 ? "zh-CN" : "en";
    }

    function storeLang(lang) {
        try {
            window.localStorage.setItem(LANG_KEY, lang === "zh-CN" ? "zh" : "en");
        } catch (err) { /* 存储不可用：本次会话内仍然生效 */ }
    }

    function applyLang(lang, persist) {
        currentLang = lang === "en" ? "en" : "zh-CN";
        try { document.documentElement.lang = currentLang; } catch (err) { }
        var nodes = document.querySelectorAll("[data-i18n]");
        for (var i = 0; i < nodes.length; i++) {
            nodes[i].textContent = t(nodes[i].getAttribute("data-i18n"));
        }
        var richNodes = document.querySelectorAll("[data-i18n-html]");
        for (var j = 0; j < richNodes.length; j++) {
            richNodes[j].innerHTML = t(richNodes[j].getAttribute("data-i18n-html"));
        }
        var barLabel = document.querySelector(".top-bar");
        if (barLabel) barLabel.setAttribute("aria-label", t("topbar-label"));
        var navLabel = document.querySelector(".side-nav");
        if (navLabel) navLabel.setAttribute("aria-label", t("side-nav-label"));
        try { document.title = t("doc-title"); } catch (err2) { }
        var btn = byId("lang-toggle");
        if (btn) {
            // 英文态用 "ZH" 而不是 "中文"：保证英文态 innerText 零 CJK（切换语义不变）
            btn.textContent = currentLang === "zh-CN" ? "EN" : "ZH";
            btn.setAttribute("aria-label", t("lang-toggle-aria"));
        }
        if (persist !== false) storeLang(currentLang);
        renderForLang();
    }

    function renderForLang() {
        dismissToasts();
        var box = byId("loadingBox");
        if (box && !box.hidden) {
            var txt = byId("loadingText");
            if (txt) txt.textContent = t("loading");
        }
        if (view.mode === "guest") showGuest(view.guestKey, view.guestArgs[0], view.guestArgs[1]);
        if (view.mode === "authed") {
            if (view.user) renderUser(view.user);
            if (view.quota) renderQuota(view.quota);
            if (view.starsLoaded) renderStarList(view.stars);
            if (view.evalsLoaded) renderEvalList(view.evals);
            if (byId("adminWrap")) rebuildAdmin();
        }
    }

    function bindLangToggle() {
        var btn = byId("lang-toggle");
        if (!btn || btn.getAttribute("data-bound") === "1") return;
        btn.setAttribute("data-bound", "1");
        btn.addEventListener("click", function () {
            applyLang(currentLang === "zh-CN" ? "en" : "zh-CN", true);
        });
    }

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
        var map = { paper: "kind-paper", trial: "kind-trial", strain: "kind-strain", metabolite: "kind-metabolite", topic: "kind-topic" };
        var key = String(kind || "").toLowerCase();
        return map[key] ? t(map[key]) : String(kind);
    }

    function retryText(res, prefix) {
        var head = t(prefix) + t("sep");
        if (res.kind === "network") return head + t("net-fail");
        if (res.kind === "timeout") return head + t("timeout-fail");
        if (res.kind === "http") return head + t("http-fail", res.status);
        return head + t("generic-retry");
    }

    // 语言无关的失败原因短语（重绘时重新取词）
    function reasonShort(res) {
        if (res.kind === "network") return t("net-short");
        if (res.kind === "timeout") return t("timeout-short");
        return "HTTP " + res.status;
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
        close.setAttribute("aria-label", t("close-toast"));
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
            if (box && !box.hidden && text) text.textContent = t("loading-hint");
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
                showGuest("watchdog");
            }
        }, REQUEST_TIMEOUT_MS + WATCHDOG_EXTRA_MS);
    }

    function hideLoading() {
        stopLoadingHint();
        stopWatchdog();
        var box = byId("loadingBox");
        if (box) box.hidden = true;
        var text = byId("loadingText");
        if (text) text.textContent = t("loading");
    }

    /* ---------------- 视图切换 ---------------- */
    // 未登录态：note 走字典键；argFn 在每次重绘时重新求值，切换语言后参数也是新语言
    function showGuest(key, argFn) {
        hideLoading();
        unmountAdmin();
        var authed = byId("authedBox");
        if (authed) authed.hidden = true;
        var guest = byId("guestBox");
        if (guest) guest.hidden = false;
        view.mode = "guest";
        view.guestKey = key ? String(key) : "";
        view.guestArgFn = typeof argFn === "function" ? argFn : null;
        var args = view.guestArgFn ? view.guestArgFn() : [];
        showNote(byId("guestNote"), key ? t(key, args[0], args[1]) : "");
    }

    function showAuthed() {
        view.mode = "authed";
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
        view.user = user || null;
        var orcid = pick(user, ["orcid", "orcid_id", "orcidId", "orcid_uri"]);
        var name = pick(user, ["name", "display_name", "displayName", "full_name"]);
        accountOrcid = orcid ? String(orcid) : "";
        if (byId("orcidText")) byId("orcidText").textContent = orcid ? String(orcid) : t("not-provided");
        if (byId("nameText")) byId("nameText").textContent = name ? String(name) : t("not-provided");
    }

    function tierLabel(tier) {
        var map = { user: "tier-user", anon: "tier-anon", admin: "tier-admin", free: "tier-anon", paid: "tier-user" };
        var key = String(tier || "").toLowerCase();
        return map[key] ? t(map[key]) : String(tier);
    }

    function quotaFigure(value) {
        var el = document.createElement("strong");
        el.textContent = value === null ? "—" : String(value);
        return el;
    }

    function renderQuota(quota) {
        view.quota = quota || null;
        var q = quota || {};
        var tier = pick(q, ["tier", "plan", "level"]);
        var limit = num(pick(q, ["limit", "total"]));
        var used = num(pick(q, ["used", "usage"]));
        var remaining = num(pick(q, ["remaining", "left"]));
        if (remaining === null && limit !== null && used !== null) {
            remaining = Math.max(limit - used, 0);
        }

        if (byId("tierText")) byId("tierText").textContent = tier ? tierLabel(tier) : t("not-provided");

        // 一句话说清「今日已用 / 上限 · 剩余」
        var summary = byId("quotaSummary");
        if (summary) {
            summary.textContent = "";
            summary.appendChild(document.createTextNode(t("used-today")));
            summary.appendChild(quotaFigure(used));
            summary.appendChild(document.createTextNode(" / " + (limit === null ? "—" : String(limit))));
            var sep = document.createElement("span");
            sep.className = "qs-sep";
            sep.textContent = " · ";
            summary.appendChild(sep);
            summary.appendChild(document.createTextNode(t("remaining")));
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
            bar.setAttribute("aria-valuetext", t("quota-value-text",
                used === null ? t("unknown") : used,
                limit === null ? t("unknown") : limit,
                remaining === null ? t("unknown") : remaining));
        }

        var fill = byId("quotaFill");
        if (fill) {
            fill.style.width = max > 0 ? Math.min(Math.max(now / max, 0), 1) * 100 + "%" : "0";
            fill.className = "quota-fill" + (remaining === 0 && used !== null ? " is-full" : "");
        }

        var resets = fmtDate(q.resets_at || q.reset_at);
        if (byId("quotaReset")) {
            byId("quotaReset").textContent = resets ? t("reset-at", resets) : t("reset-note");
        }
    }

    /* ---------------- 渲染：我的收藏 ---------------- */
    function refreshStarCount() {
        var list = byId("starList");
        var count = list ? list.children.length : 0;
        if (byId("starCount")) byId("starCount").textContent = count ? t("count", count) : "";
        if (byId("starEmpty")) byId("starEmpty").hidden = count !== 0;
    }

    function starRow(item) {
        var li = document.createElement("li");
        li.className = "item";

        var main = document.createElement("div");
        main.className = "item-main";

        var title = document.createElement("div");
        title.className = "item-title";
        title.textContent = String(pick(item, ["title", "name"]) || t("untitled-item"));
        main.appendChild(title);

        var bits = [];
        var doi = pick(item, ["doi"]);
        if (doi) bits.push("DOI " + doi);
        var created = fmtDate(pick(item, ["created_at", "createdAt", "time"]));
        if (created) bits.push(t("starred-at", created));
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
        btn.textContent = t("remove");
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
        btn.textContent = t("removing");
        showNote(note, "");

        request(ENDPOINTS.stars + "/" + encodeURIComponent(id), { method: "DELETE" }).then(function (res) {
            if (res.ok) {
                if (row && row.parentNode) row.parentNode.removeChild(row);
                refreshStarCount();
                showToast(starTitle ? t("removed-toast", starTitle) : t("removed-toast-generic"));
                return;
            }
            btn.disabled = false;
            btn.textContent = original;
            if (res.kind === "unauth") {
                handleUnauth();
                return;
            }
            showNote(note, retryText(res, "remove-fail"));
        });
    }

    function renderStarList(items) {
        var list = byId("starList");
        if (!list) return;
        view.stars = items || [];
        view.starsLoaded = true;
        list.textContent = "";
        for (var i = 0; i < view.stars.length; i++) {
            list.appendChild(starRow(view.stars[i]));
        }
        refreshStarCount();
        if (view.starErrRes) showNote(byId("starError"), retryText(view.starErrRes, "stars-load-fail"));
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
                view.starErrRes = res;
                showNote(errEl, retryText(res, "stars-load-fail"));
                return;
            }
            showNote(errEl, "");
            view.starErrRes = null;
            renderStarList((res.data && Array.isArray(res.data.items)) ? res.data.items : []);
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
        title.textContent = String(pick(item, ["title", "query", "name", "subject", "term", "keyword"]) || t("untitled-eval"));
        main.appendChild(title);

        var bits = [];
        var created = fmtDate(pick(item, ["created_at", "createdAt", "evaluated_at", "time"]));
        if (created) bits.push(created);
        var score = pick(item, ["score", "total_score", "value"]);
        if (score !== null) bits.push(t("score", score));
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

    function renderEvalList(items) {
        var list = byId("evalList");
        if (!list) return;
        view.evals = items || [];
        view.evalsLoaded = true;
        list.textContent = "";
        for (var i = 0; i < view.evals.length; i++) {
            list.appendChild(evalRow(view.evals[i]));
        }
        if (byId("evalCount")) byId("evalCount").textContent = view.evals.length ? t("count", view.evals.length) : "";
        if (byId("evalEmpty")) byId("evalEmpty").hidden = view.evals.length !== 0;
        if (view.evalErrRes) showNote(byId("evalError"), retryText(view.evalErrRes, "evals-load-fail"));
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
                view.evalErrRes = res;
                showNote(errEl, retryText(res, "evals-load-fail"));
                return;
            }
            showNote(errEl, "");
            view.evalErrRes = null;
            renderEvalList((res.data && Array.isArray(res.data.items)) ? res.data.items : []);
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
                    showToast(t("logged-out"));
                    return;
                }
                // 服务端没确认撤销：本机先退出，并如实告知
                dropToken();
                showGuest("logout-local-note", function () { return [reasonShort(res)]; });
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
    var adminItems = [];
    var adminTotal = 0;
    var adminQuery = "";
    var adminOverviewData = null;

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

    function adminLabel(caps, key, countId, labelId) {
        var label = makeEl("span", "section-label");
        label.id = labelId;
        label.appendChild(document.createTextNode(caps));
        label.appendChild(makeEl("span", "sl-zh", currentLang === "zh-CN" ? t(key) : ""));
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
        overviewCard.appendChild(adminLabel("OVERVIEW", "sl-overview", null, "adminOverviewLabel"));
        var overviewBody = makeEl("div", "card-body");
        var figures = makeEl("div", "quota-figures");
        figures.appendChild(adminFigure("adminTotalUsers", t("kpi-total")));
        figures.appendChild(adminFigure("adminBannedUsers", t("kpi-banned")));
        figures.appendChild(adminFigure("adminAiToday", t("kpi-ai")));
        overviewBody.appendChild(figures);
        var overviewMeta = makeEl("p", "quota-meta", t("admin-overview-loading"));
        overviewMeta.id = "adminOverviewMeta";
        overviewBody.appendChild(overviewMeta);
        overviewCard.appendChild(overviewBody);
        wrap.appendChild(overviewCard);

        // 用户管理
        var usersCard = makeEl("section", "card");
        usersCard.setAttribute("aria-labelledby", "adminUsersLabel");
        usersCard.appendChild(adminLabel("USERS", "sl-users", "adminUserCount", "adminUsersLabel"));

        var usersBody = makeEl("div", "card-body");

        // 纯前端过滤：只筛已载入的行，不改任何请求参数
        var toolbar = makeEl("div", "admin-toolbar");
        var search = document.createElement("input");
        search.type = "search";
        search.className = "admin-search";
        search.id = "adminSearch";
        search.setAttribute("placeholder", t("admin-search-ph"));
        search.setAttribute("aria-label", t("admin-search-aria"));
        search.setAttribute("autocomplete", "off");
        search.addEventListener("input", applyAdminFilter);
        toolbar.appendChild(search);
        toolbar.appendChild(makeBtn("btn btn-ghost btn-sm admin-refresh", t("refresh"), function () {
            loadAdminOverview();
            loadAdminUsers(true);
        }));
        usersBody.appendChild(toolbar);
        var filterNote = makeEl("p", "admin-filter-note");
        filterNote.id = "adminFilterNote";
        filterNote.setAttribute("aria-live", "polite");
        usersBody.appendChild(filterNote);

        var statusEl = makeEl("p", "empty", t("admin-users-loading"));
        statusEl.id = "adminStatus";
        usersBody.appendChild(statusEl);
        var list = makeEl("ul", "item-list admin-user-list");
        list.id = "adminUserList";
        usersBody.appendChild(list);
        var errorEl = makeEl("p", "inline-error");
        errorEl.id = "adminUserError";
        errorEl.hidden = true;
        usersBody.appendChild(errorEl);
        var moreBtn = makeBtn("btn btn-ghost btn-sm admin-more", t("load-more"), function () {
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
        row.badge.textContent = banned ? t("badge-banned") : t("badge-active");
        row.badge.className = "badge admin-badge " + (banned ? "admin-badge-danger" : "admin-badge-ok");
        row.metaUsage.textContent = t("admin-usage",
            used === null ? "—" : used,
            effective === null ? "—" : effective,
            custom === null ? t("quota-default") : t("quota-custom"));
        row.input.value = custom === null ? "" : String(custom);
        row.input.placeholder = effective === null ? t("default-ph") : t("default-ph-n", effective);
        row.banBtn.textContent = banned ? t("unban") : t("ban");
        row.banBtn.className = "btn btn-sm " + (banned ? "btn-ghost" : "btn-danger-ghost");
        row.banBtn.setAttribute("aria-label", t(banned ? "aria-unban" : "aria-ban", row.orcid || t("this-user")));
    }

    function adminUserRow(item) {
        var data = (item && typeof item === "object") ? item : {};
        var orcid = String(pick(data, ["orcid_id", "orcid", "orcidId"]) || "");
        var name = pick(data, ["name", "display_name"]);
        var created = fmtDate(pick(data, ["created_at", "createdAt"]));

        var node = makeEl("li", "item admin-user");
        var head = makeEl("div", "admin-user-head");
        head.appendChild(makeEl("div", "item-title", name ? String(name) : t("no-name")));
        var badge = makeEl("span", "badge admin-badge");
        head.appendChild(badge);
        node.appendChild(head);

        node.appendChild(makeEl("p", "item-meta admin-orcid", "ORCID " + (orcid || "—")));
        node.appendChild(makeEl("p", "item-meta", t("registered", created || "—")));
        var metaUsage = makeEl("p", "item-meta");
        node.appendChild(metaUsage);

        var quotaRow = makeEl("div", "admin-quota");
        quotaRow.appendChild(makeEl("span", "admin-quota-label", t("daily-quota")));
        var input = document.createElement("input");
        input.type = "number";
        input.className = "admin-quota-input";
        input.setAttribute("min", String(QUOTA_MIN));
        input.setAttribute("max", String(QUOTA_MAX));
        input.setAttribute("step", "1");
        input.setAttribute("inputmode", "numeric");
        input.setAttribute("aria-label", t("aria-quota-input", QUOTA_MIN, QUOTA_MAX, orcid || t("unknown")));
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

        var saveBtn = makeBtn("btn btn-ghost btn-sm", t("save"), function () { saveQuota(row, false); });
        saveBtn.setAttribute("aria-label", t("aria-save-quota", orcid || t("this-user")));
        var resetBtn = makeBtn("btn btn-ghost btn-sm", t("reset-default"), function () { saveQuota(row, true); });
        resetBtn.setAttribute("aria-label", t("aria-reset-quota", orcid || t("this-user")));
        quotaRow.appendChild(saveBtn);
        quotaRow.appendChild(resetBtn);
        node.appendChild(quotaRow);

        var actions = makeEl("div", "admin-actions");
        var banBtn = makeBtn("btn btn-sm", t("ban"), function () { toggleBan(row); });
        var revokeBtn = makeBtn("btn btn-ghost btn-sm", t("revoke"), function () { revokeTokens(row); });
        revokeBtn.setAttribute("aria-label", t("aria-revoke", orcid || t("this-user")));
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
                rowNote(row, t("quota-invalid", QUOTA_MIN, QUOTA_MAX), false);
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
                rowNote(row, t(useDefault ? "quota-reset-done" : "quota-saved"), true);
                showToast(useDefault
                    ? t("toast-quota-reset", row.orcid)
                    : t("toast-quota-saved", row.orcid, payload.quota_limit));
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
                rowNote(row, t("save-fail-invalid", QUOTA_MIN, QUOTA_MAX), false);
                return;
            }
            rowNote(row, retryText(res, "save-fail"), false);
        });
    }

    function toggleBan(row) {
        if (row.busy || !row.orcid) return;
        var banned = row.data.is_banned === true || row.data.banned === true;
        var who = row.data.name ? t("who", row.orcid, row.data.name) : row.orcid;
        var ask = t(banned ? "confirm-unban" : "confirm-ban", who);
        if (row.orcid === accountOrcid && !banned) ask += t("confirm-self");
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
                rowNote(row, t(banned ? "unbanned-done" : "banned-done"), true);
                showToast(banned ? t("toast-unban", row.orcid) : t("toast-ban", row.orcid));
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
            rowNote(row, retryText(res, banned ? "unban-fail" : "ban-fail"), false);
        });
    }

    function revokeTokens(row) {
        if (row.busy || !row.orcid) return;
        var who = row.data.name ? t("who", row.orcid, row.data.name) : row.orcid;
        if (!window.confirm(t("confirm-revoke", who))) return;

        rowNote(row, "");
        setRowBusy(row, true);
        request(adminUserUrl(row.orcid) + "/revoke-tokens", { method: "POST" }).then(function (res) {
            setRowBusy(row, false);
            if (res.ok) {
                var revoked = num(res.data ? res.data.revoked : null);
                rowNote(row, revoked === null ? t("revoked-all") : t("revoked-n", revoked), true);
                showToast(revoked === null ? t("toast-revoke-all", row.orcid) : t("toast-revoke-n", revoked, row.orcid));
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
            rowNote(row, retryText(res, "revoke-fail"), false);
        });
    }

    function updateAdminCount(shown) {
        var countEl = byId("adminUserCount");
        if (countEl) {
            if (!adminRows.length) {
                countEl.textContent = "";
            } else if (adminQuery && typeof shown === "number") {
                countEl.textContent = t("count-filtered", shown, adminRows.length, adminTotal);
            } else {
                countEl.textContent = t("count-loaded", adminRows.length, adminTotal);
            }
        }
        var moreBtn = byId("adminMore");
        if (moreBtn) {
            var rest = adminTotal - adminRows.length;
            var hasMore = adminRows.length > 0 && rest > 0;
            moreBtn.hidden = !hasMore;
            moreBtn.setAttribute("aria-label", hasMore ? t("aria-more-n", rest) : t("aria-more"));
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
                note.textContent = t("no-match", adminQuery, adminRows.length);
            } else {
                note.textContent = "";
            }
        }
        updateAdminCount(shown);
    }

    function renderAdminOverview(data) {
        var d = data || {};
        setFigure("adminTotalUsers", num(d.total_users));
        setFigure("adminBannedUsers", num(d.banned_users));
        setFigure("adminAiToday", num(d.ai_calls_today));
        var meta = byId("adminOverviewMeta");
        if (!meta) return;
        var admins = Array.isArray(d.admins) ? d.admins : [];
        var bits = [t("admins-line", admins.length ? admins.join(t("sep-list")) : t("none"))];
        var nowText = fmtDate(d.now_utc);
        if (nowText) bits.push(t("stat-time", nowText));
        meta.textContent = bits.join(" · ");
    }

    function loadAdminOverview() {
        var meta = byId("adminOverviewMeta");
        if (!meta) return Promise.resolve();
        meta.textContent = t("admin-overview-loading");

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
                meta.textContent = retryText(res, "overview-fail");
                return;
            }
            adminOverviewData = res.data || {};
            renderAdminOverview(adminOverviewData);
        });
    }

    // 语言切换后原地重建管理员区块：复用已缓存的数据，不重新发请求
    function rebuildAdminRows() {
        var list = byId("adminUserList");
        if (!list) return;
        list.textContent = "";
        adminRows = [];
        for (var i = 0; i < adminItems.length; i++) {
            var row = adminUserRow(adminItems[i]);
            list.appendChild(row.node);
            adminRows.push(row);
        }
        var search = byId("adminSearch");
        if (search && adminQuery) search.value = adminQuery;
        applyAdminFilter();
    }

    function rebuildAdmin() {
        var total = adminTotal;
        unmountAdmin();
        if (!mountAdmin()) return;
        adminTotal = total;
        if (adminOverviewData) renderAdminOverview(adminOverviewData);
        rebuildAdminRows();
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
            statusEl.textContent = reset ? t("admin-users-loading") : t("loading-more");
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
                    showNote(byId("adminUserError"), retryText(res, "users-fail"));
                    return;
                }

                var data = res.data || {};
                var items = Array.isArray(data.items) ? data.items : [];
                var total = num(data.total);
                if (reset) adminItems = [];
                adminItems = adminItems.concat(items);
                if (total !== null) adminTotal = total;
                rebuildAdminRows();
                if (statusEl) {
                    statusEl.textContent = adminRows.length ? "" : t("no-users");
                    statusEl.hidden = adminRows.length > 0;
                }
            });
    }

    /* ---------------- 启动 ---------------- */
    function startAccountPage() {
        return request(ENDPOINTS.me).then(function (res) {
            if (!res.ok) {
                if (res.kind === "unauth") {
                    handleUnauth();
                } else if (res.kind === "network") {
                    showGuest("offline-guest");
                } else if (res.kind === "timeout") {
                    showGuest("slow-guest");
                } else {
                    showGuest("http-guest", function () { return [res.status]; });
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
        applyLang(readLang(), false);
        bindLangToggle();
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
            showGuest("generic-fail");
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();

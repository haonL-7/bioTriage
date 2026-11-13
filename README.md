# bioTriage-Open

面向生命科学的文献噪声过滤平台：正文按证据框架分级，日报/周报由 8 个学科频道自动聚合，学术助手在 480+ 篇本地语料上检索作答。

**站点：https://haonl-7.github.io/bioTriage/**

- Literature Daily / Weekly：跨频道聚合的当日与本周新文
- 8 个学科频道：computational-genomics、metagenomics-informatics、single-cell-omics 等
- 学术助手：中英混合检索，回答附带证据等级与来源
- 方法论：证据分级与结对判定标准（ms1 / ms2）

技术栈：静态 HTML + FastAPI + 轻量检索（无向量库），GitHub Actions 每日抓取与部署。

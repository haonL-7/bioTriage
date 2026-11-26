# Biomni 离线对比原型（方案 D）

目的：用同一批论文，比较 **Biomni（Stanford, snap-stanford/Biomni）** 与 **bioTriage 现有分级**的差异，
判断它的优势是否在我们的场景里成立（例如"能跑复现分析"，而不是"分级更准"）。

本目录**不影响生产代码**，也不进入 CI。

## 一、准备 Biomni（一次性，约 30–60 分钟）

> 以下命令以官方 README 为准；Biomni 更新较快，若与环境冲突以官方说明为准。
> 我这边沙箱禁网，**未能实测**这些命令，属于待验证步骤。

```bash
git clone https://github.com/snap-stanford/Biomni.git
cd Biomni
conda env create -f biomni_env.yml      # 官方提供的环境文件
conda activate biomni_e1
pip install -e .
```

然后配置模型密钥（Biomni 需要 LLM 才能规划）：

```bash
export ANTHROPIC_API_KEY=...      # 或 OPENAI_API_KEY / DEEPSEEK 兼容端点
```

成本预估：单篇论文的分析在分钟级，主要花费是模型 token；**先跑 3 篇试水**，不要一次跑完。

## 二、生成本次对比样本

```bash
cd experiments/biomni-probe
python extract.py --per 1     # 每个频道 1 篇，共 6 篇
```

产出：

- `papers.json`：含 `bioTriage` 的分级、摘要、tags（我们的基线）
- `papers.md`：人读版，方便直接贴给 Biomni

## 三、让 Biomni 出结果

1. 打开 `papers.md` 与 `PROMPT.md`；
2. 把 `PROMPT.md` 的提示 + `papers.json` 里的标题与摘要交给 Biomni；
3. 把它的 JSON 输出存成 `biomni_out.json`，格式：

```json
[
  {
    "id": "59d004ef34e0",
    "significance": "High",
    "evidence": "n=400 前瞻队列，含外部验证集",
    "summary": "……"
  }
]
```

## 四、对比

```bash
python compare.py
```

产出 `report.md`：完全一致率、±1 档一致率、Cohen's kappa、逐条对照。

## 五、判读标准（事先定好，避免事后找理由）

| 结果 | 结论 |
|---|---|
| kappa ≥ 0.6 且 Biomni 的 evidence 更具体 | 值得把它的"证据抽取"思路并入我们的评估 prompt |
| kappa 0.3–0.6 | 两者互补：用 Biomni 的输出做第二意见，不替换 |
| kappa < 0.3 | 说明两者在评不同维度，**不要合并**；转去验证它的"执行分析"能力 |
| Biomni 明显更慢/更贵且无额外信息 | 放弃集成，只保留其工具名录作为参考 |

## 六、注意事项

- 两边必须**信息对等**：都只看标题+摘要，不许联网补信息，否则对比无意义；
- 样本量 6 篇只能做方向性判断，要下结论建议 20 篇以上；
- Biomni 的许可证与依赖在本机实测前请以官方仓库为准（我未能联网核实）。

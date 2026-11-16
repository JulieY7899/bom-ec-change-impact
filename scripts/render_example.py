#!/usr/bin/env python3
"""Draw an exact, code-native SVG overview from the synthetic result JSON."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    result = json.loads((ROOT / "examples" / "sample-result.json").read_text(encoding="utf-8"))
    metrics = result["metrics"]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="850" viewBox="0 0 1400 850" role="img" aria-labelledby="title desc">',
             '<title id="title">BOM 工程变更：虚构案例的实际核查结果</title>',
             '<desc id="desc">COMP-001 替换为 COMP-NEW，识别两个成品、三条使用路径、两条销售订单行及一个制造核查对象；供应与质量证据待补。</desc>',
             '<style>text{font-family:Inter,"Noto Sans CJK SC","Microsoft YaHei",sans-serif} .muted{fill:#64748b} .ink{fill:#172638} .small{font-size:17px} .label{font-size:20px;font-weight:600} .code{font-family:"DejaVu Sans Mono",monospace}</style>',
             '<rect width="1400" height="850" rx="24" fill="#f4f6fa"/>']

    def text(x, y, value, size=20, color="#172638", weight=400, extra=""):
        parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" {extra}>{escape(str(value))}</text>')

    def rect(x, y, width, height, fill="#fff", stroke="#e2e8f0", radius=16):
        parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>')

    text(48, 59, "BOM / EC IMPACT", 17, "#4060a0", 650, 'letter-spacing="2"')
    text(48, 113, "一次组件替换，哪些对象需要核查？", 37, weight=700)
    text(48, 150, "确定性规则与图遍历 · 保留路径、来源与待确认事项", 21, "#64748b")
    rect(1054, 48, 298, 47, "#e7edf9", "#e7edf9", 23)
    text(1075, 79, "虚构数据 · 实际运行结果", 18, "#315497", 600)

    rect(48, 184, 1304, 73, "#172638", "#172638")
    text(72, 229, result["change"]["old_component"], 25, "#fff", 600, 'class="code"')
    text(258, 229, "→", 27, "#9ab8fd")
    text(310, 229, result["change"]["new_component"], 25, "#fff", 600, 'class="code"')
    text(644, 228, "计划生效  " + result["change"]["effective_date"], 20, "#dae3ee")
    text(1035, 228, "核查支持 · 人工决策", 20, "#dae3ee")

    cards = [("涉及成品", metrics["top_materials"], "FG-A / FG-B"),
             ("成品使用路径", metrics["upstream_paths"], "每条保留来源行证据"),
             ("关联销售订单行", metrics["sales_order_items"], "对应两个成品"),
             ("制造核查对象", metrics["production_orders"], "已释放 / 执行中")]
    for index, (label, value, note) in enumerate(cards):
        x = 48 + index * 332
        rect(x, 281, 308, 143)
        text(x + 22, 315, label, 18, "#64748b")
        text(x + 22, 368, value, 44, "#284eaf", 700)
        text(x + 22, 399, note, 16, "#64748b")

    rect(48, 450, 790, 293)
    text(72, 490, "从变更组件向上追溯", 23, weight=650)
    paths = sorted(result["directions"]["upstream"]["paths"], key=lambda p: (p["top_material"], p["nodes"]))
    for index, path in enumerate(paths):
        y = 537 + index * 64
        text(73, y + 17, f"0{index+1}", 17, "#8290a4", 600)
        nodes = path["nodes"]
        x = 118
        for number, node in enumerate(nodes):
            width = max(107, len(str(node)) * 10 + 25)
            rect(x, y - 10, width, 42, "#edf2fc" if number == 0 else "#f8fafc", "#dce4ef", 9)
            text(x + 12, y + 17, node, 17, "#284eaf" if number == 0 else "#334155", 500, 'class="code"')
            x += width + 12
            if number < len(nodes) - 1:
                text(x, y + 17, "→", 20, "#94a3b8")
                x += 31

    rect(862, 450, 490, 293, "#fffdf8", "#eee4cd")
    text(886, 490, "下一步：补证与人工确认", 23, "#785722", 650)
    for y, heading, detail in [(535, "计划", "库存、在途、未结采购与组件需求待补"),
                              (608, "工程 / 质量", "新组件质量、认证与替代适用性待确认"),
                              (681, "制造", "已释放订单需核查投料、工序与处置")]:
        text(886, y, heading, 19, "#785722", 650)
        text(886, y + 28, detail, 17, "#776c57")

    text(48, 785, "结果示意图，依据 examples/sample-result.json 生成；非页面截图。", 17, "#64748b")
    text(48, 816, "范围：识别待核查对象，不执行审批、EBOM 版本迁移或 ERP / PLM / MES 写回。", 17, "#64748b")
    parts.append('</svg>')
    destination = ROOT / "docs" / "assets" / "demo-overview.svg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(destination.relative_to(ROOT))


if __name__ == "__main__":
    main()

target_file = r"d:\PAW\paw-beta\frontend\dist\paw_console.js"
with open(target_file, "r", encoding="utf-8") as f:
    code = f.read()

old_block = """                  ${g.items.map((item) => `
                    <tr style="border-top: 1px solid #FAF7F1;">
                      <td style="padding: 6px 8px;"><input type="checkbox" class="chk-item chk-group-${gIdx}" data-id="${item.id}" data-conf="${item.confidence}" /></td>
                      <td style="padding: 6px 8px;"><span class="paw-badge ${item.candidate_type === 'knowledge' ? 'paw-badge-info' : item.candidate_type === 'todo' ? 'paw-badge-pass' : 'paw-badge-gray'}">${item.candidate_type}</span></td>
                      <td style="padding: 6px 8px; color: #302B26; font-weight: 500;" title="${item.summary || ""}">${item.title}<div style="font-size: 11px; color: #A1998E; font-weight: 400; margin-top: 2px;">${(item.summary || "").slice(0, 60)}</div></td>
                      <td style="padding: 6px 8px;"><span class="paw-badge ${item.confidence >= 0.85 ? 'paw-badge-pass' : 'paw-badge-warn'}">${(item.confidence * 100).toFixed(0)}%</span></td>
                      <td style="padding: 6px 8px; font-size: 11px; color: #756E65;">${item.rule_fallback_notice ? "规则提炼" : "AI 分析"}</td>
                    </tr>
                  `).join("")}"""

new_block = """                  ${g.items.map((item) => {
                    const cVal = item.confidence;
                    let isHigh = true;
                    let confDisplay = "高";
                    if (typeof cVal === "number") {
                      isHigh = cVal >= 0.85;
                      confDisplay = (cVal * 100).toFixed(0) + "%";
                    } else if (typeof cVal === "string") {
                      if (cVal === "HIGH") { isHigh = true; confDisplay = "高"; }
                      else if (cVal === "MEDIUM") { isHigh = false; confDisplay = "中"; }
                      else if (cVal === "LOW") { isHigh = false; confDisplay = "低"; }
                      else if (!isNaN(parseFloat(cVal))) {
                        const num = parseFloat(cVal);
                        isHigh = num >= 0.85;
                        confDisplay = (num * 100).toFixed(0) + "%";
                      } else {
                        confDisplay = cVal || "高";
                      }
                    }
                    return `
                    <tr style="border-top: 1px solid #FAF7F1;">
                      <td style="padding: 6px 8px;"><input type="checkbox" class="chk-item chk-group-${gIdx}" data-id="${item.id}" data-conf="${isHigh ? '1' : '0'}" /></td>
                      <td style="padding: 6px 8px;"><span class="paw-badge ${item.candidate_type === 'knowledge' ? 'paw-badge-info' : item.candidate_type === 'todo' ? 'paw-badge-pass' : 'paw-badge-gray'}">${item.candidate_type}</span></td>
                      <td style="padding: 6px 8px; color: #302B26; font-weight: 500;" title="${item.summary || ""}">${item.title}<div style="font-size: 11px; color: #A1998E; font-weight: 400; margin-top: 2px;">${(item.summary || "").slice(0, 60)}</div></td>
                      <td style="padding: 6px 8px;"><span class="paw-badge ${isHigh ? 'paw-badge-pass' : 'paw-badge-warn'}">${confDisplay}</span></td>
                      <td style="padding: 6px 8px; font-size: 11px; color: #756E65;">${item.rule_fallback_notice ? "规则提炼" : "AI 分析"}</td>
                    </tr>
                    `;
                  }).join("")}"""

assert old_block in code, "old_block not found in code"
code = code.replace(old_block, new_block)

code = code.replace(
    'if (parseFloat(c.dataset.conf) >= 0.85) c.checked = true;',
    'if (parseFloat(c.dataset.conf) >= 0.85 || c.dataset.conf === "1" || c.dataset.conf === "HIGH") c.checked = true;'
)

code = code.replace(
    '勾选高置信度 (>=85%)',
    '勾选高置信度'
)

with open(target_file, "w", encoding="utf-8") as f:
    f.write(code)

print("Confidence rendering patched successfully!")

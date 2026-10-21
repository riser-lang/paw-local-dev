(function (root) {
  "use strict";

  const knownEnglishReasons = [
    [/database is locked|database locked/i, "本地数据暂时被占用，请稍后重试。"],
    [/candidate.*not found|not found/i, "待审核条目已不存在或已处理。"],
    [/already processed/i, "该条目已经处理。"],
    [/missing|required|cannot be empty/i, "必填信息不完整。"],
  ];

  function chineseFailureReason(value) {
    const reason = typeof value === "string" ? value.trim() : "";
    if (!reason) return "未知原因";

    const translated = knownEnglishReasons.find(([pattern]) => pattern.test(reason));
    if (translated) return translated[1];

    if (!/[\u3400-\u9fff]/.test(reason)) return "未知原因";
    return reason
      .replace(/\b[A-Z][A-Z0-9_]{2,}\b/g, "相关内容")
      .replace(/\bBearer\s+\S+/gi, "授权信息")
      .replace(/sk-[A-Za-z0-9_-]{8,}/g, "密钥信息")
      .trim() || "未知原因";
  }

  root.formatBatchFailureDetails = function (details) {
    if (!Array.isArray(details) || details.length === 0) return "未知原因";
    return details.map((item, index) => `第 ${index + 1} 条：${chineseFailureReason(item && item.error)}`).join("\n");
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { formatBatchFailureDetails: root.formatBatchFailureDetails };
  }
})(typeof window === "undefined" ? globalThis : window);

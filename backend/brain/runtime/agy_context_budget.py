"""Bounded model context for Agy Web conversations.

Why this module exists
----------------------
Rolling history retention (7 days) bounds what the *user* can scroll back to.
It does **not** bound what the *model* carries: the Antigravity CLI resumes a
``conversation_id`` and that server-side thread keeps growing turn after turn,
so token/quota cost grows without limit even though the visible transcript is
pruned.  This module implements the missing half: an explicit, bounded context
window that is periodically *rotated*.

Rotation semantics
------------------
When the current epoch exceeds :data:`MAX_CONTEXT_ROUNDS` rounds (or the carried
context exceeds :data:`CONTEXT_CHAR_BUDGET` characters), the caller must:

1. build a short ``conversation_summary`` from the **visible transcript** plus
   ``v2_context`` only (never from hidden reasoning — there is none to read);
2. keep the most recent :data:`KEEP_RECENT_ROUNDS` complete rounds verbatim;
3. **drop the old ``conversation_id``** so the next call starts a fresh
   Antigravity conversation;
4. prepend summary + recent rounds to the next prompt.

The summary is generated only when rotation fires, so its cost is amortised
over a whole epoch instead of being paid every turn.  It is deterministic —
no model call, no extra quota, and no risk of a summarisation call failing
mid-turn.  It is *not* a crude character slice: it reads the structured
sections Agy already emits (``### 1. 综合判断`` …) and keeps the load-bearing
parts (goal, project, formed conclusions, open questions).

Pure functions only: no I/O, no database, no model.  Safe to unit test.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence

#: A round is one user turn plus the answer it produced.
MAX_CONTEXT_ROUNDS = 8

#: How many complete rounds survive a rotation verbatim.
KEEP_RECENT_ROUNDS = 3

#: Summary size budget (spec §15.3: 800–1200 Chinese characters).
MIN_SUMMARY_CHARS = 200
MAX_SUMMARY_CHARS = 1200

#: The Web helper hard-limits the whole prompt (agy_v2_runtime).
#: The project pack, evidence index and the current user message must keep the
#: bulk of that budget, so carried-over context is capped far below it rather
#: than mechanically consuming the full allowance.
MAX_WEB_PROMPT_CHARS = 7600

#: ``paw-agy-web`` rejects any message longer than this outright
#: (``MAX_MESSAGE_LEN``).  The carried block is prepended to an already-rendered
#: PAW prompt, so it must be sized against the *combined* message, not against
#: the prompt alone — otherwise rotation turns fail with "Message too long".
HARD_MESSAGE_LIMIT = 8000

#: Slack left for the newlines/joining the helper adds around the payload.
PROMPT_SAFETY_MARGIN = 200

#: Ceiling for the carried block: ~42% of the 7600 prompt allowance.  The block
#: is a minority share by design (spec §14); the effective size is usually much
#: smaller because it is additionally clamped to whatever room the rendered PAW
#: prompt leaves inside HARD_MESSAGE_LIMIT.
MAX_CONTEXT_BLOCK_CHARS = 3200

#: Early-trigger safety valve on the *current epoch's* transcript.  Measured
#: epoch-relative on purpose: a whole-transcript measure never shrinks, so it
#: would fire on every single turn once crossed.  Calibrated so a typical epoch
#: reaches the 8-round trigger first, and only unusually heavy turns rotate
#: early.  The block itself is separately capped at MAX_CONTEXT_BLOCK_CHARS.
CONTEXT_CHAR_BUDGET = 9600

_HEADING_RE = re.compile(r"^\s*#{2,4}\s*(.+?)\s*$", re.MULTILINE)
_WS_RE = re.compile(r"[ \t\u3000]+")


def _flat(text: Any) -> str:
    """Collapse whitespace, including the CJK ideographic space."""
    return " ".join(str(text or "").split())


def _clip(text: str, limit: int) -> str:
    text = _flat(text)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


# --------------------------------------------------------------------- counting
def user_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in messages if str(m.get("role")) == "user"]


def count_rounds(messages: Sequence[dict[str, Any]]) -> int:
    """Number of user turns — one round each."""
    return len(user_messages(messages))


def estimate_context_chars(messages: Sequence[dict[str, Any]]) -> int:
    """Characters the transcript would add if carried verbatim."""
    return sum(len(str(m.get("content") or "")) for m in messages)


def epoch_messages(messages: Sequence[dict[str, Any]],
                   epoch_start_turn: int) -> list[dict[str, Any]]:
    """Only the messages accumulated since the current epoch began.

    The char trigger must be epoch-relative.  Measuring the whole transcript
    would never shrink after a rotation, so the trigger would stay true and the
    conversation would rotate on every subsequent turn.
    """
    start = max(0, int(epoch_start_turn or 0))
    if start == 0:
        return list(messages)
    starts = [i for i, m in enumerate(messages)
              if str(m.get("role")) == "user"]
    if start >= len(starts):
        return []
    return list(messages[starts[start]:])


def carry_allowance(message_chars: int, *,
                    hard_limit: int = HARD_MESSAGE_LIMIT,
                    margin: int = PROMPT_SAFETY_MARGIN,
                    cap: int = MAX_CONTEXT_BLOCK_CHARS) -> int:
    """Room left for the carried block once the PAW prompt is in place.

    The block is prepended to the already-rendered prompt, so its budget is
    whatever is left of the helper's hard limit — never the prompt allowance.
    """
    return max(0, min(int(cap),
                      int(hard_limit) - int(message_chars) - int(margin)))


def should_rotate(*, rounds: int, epoch_start_turn: int,
                  estimated_chars: int,
                  max_rounds: int = MAX_CONTEXT_ROUNDS,
                  char_budget: int = CONTEXT_CHAR_BUDGET) -> tuple[bool, str]:
    """Decide whether the current epoch must be rotated.

    Returns ``(rotate, reason)``.  ``reason`` is a short stable token suitable
    for logging and for the acceptance evidence.
    """
    rounds_in_epoch = max(0, int(rounds) - int(epoch_start_turn))
    if rounds_in_epoch >= max(1, int(max_rounds)):
        return True, "max_context_rounds"
    if int(estimated_chars) > max(1, int(char_budget)):
        return True, "context_char_budget"
    return False, "within_budget"


def split_recent_rounds(messages: Sequence[dict[str, Any]],
                        keep: int = KEEP_RECENT_ROUNDS
                        ) -> list[dict[str, Any]]:
    """The last ``keep`` complete rounds, in order.

    A round starts at a user message.  Anything before the start of the kept
    window is dropped, so the kept slice always begins with a user turn — a
    trailing answer with no question in front of it reads as a non-sequitur.
    """
    keep = max(0, int(keep))
    if keep == 0:
        return []
    starts = [i for i, m in enumerate(messages)
              if str(m.get("role")) == "user"]
    if not starts:
        return []
    window = starts[-keep:] if len(starts) > keep else starts
    return list(messages[window[0]:])


# -------------------------------------------------------------------- summary
def _section_body(text: str, keywords: Iterable[str]) -> str:
    """Body of the first ``###`` section whose heading matches a keyword.

    Agy emits a stable sectioned answer, so this is a structural read rather
    than a guess.
    """
    body = str(text or "")
    marks = list(_HEADING_RE.finditer(body))
    for idx, mark in enumerate(marks):
        heading = _flat(mark.group(1))
        if not any(k in heading for k in keywords):
            continue
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(body)
        chunk = body[mark.end():end]
        # Drop horizontal rules and stray heading residue.
        chunk = re.sub(r"^\s*-{3,}\s*$", "", chunk, flags=re.MULTILINE)
        return _flat(chunk)
    return ""


def build_summary(messages: Sequence[dict[str, Any]],
                  v2_context: Optional[dict[str, Any]] = None,
                  *, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    """Deterministic conversation summary from the visible transcript.

    Keeps exactly the five things a rotation must not lose: the user's goal,
    the active project, the constraints the user stated, the conclusions
    already formed, and what is still open.
    """
    ctx = dict(v2_context or {})
    users = [m for m in messages if str(m.get("role")) == "user"]
    answers = [m for m in messages if str(m.get("role")) == "agy"]

    goal = _clip(users[0].get("content"), 140) if users else ""
    latest_question = _clip(users[-1].get("content"), 140) if users else ""
    project = _flat(ctx.get("active_project_name") or ctx.get("current_topic") or "")

    conclusion = ""
    open_items = ""
    for answer in reversed(answers):
        text = str(answer.get("content") or "")
        if not conclusion:
            conclusion = _section_body(
                text, ("综合判断", "结论", "答案", "我的分析", "分析"))
        if not open_items:
            open_items = _section_body(
                text, ("需要确认", "不确定", "待确认", "需要补充"))
        if conclusion and open_items:
            break

    # Constraints the user stated outright, in their own words.
    constraint_hits: list[str] = []
    for msg in users:
        for sentence in re.split(r"[。；;\n]", str(msg.get("content") or "")):
            if re.search(r"(必须|不要|不能|只能|优先|约束|预算|截止|红线)", sentence):
                clean = _clip(sentence, 60)
                if clean and clean not in constraint_hits:
                    constraint_hits.append(clean)
    constraints = "；".join(constraint_hits[:3])

    lines = ["【会话摘要 · 自动收敛】"]
    if goal:
        lines.append("- 用户目标：%s" % goal)
    if project:
        lines.append("- 当前项目：%s" % project)
    if constraints:
        lines.append("- 用户已明确的约束：%s" % constraints)
    if conclusion:
        lines.append("- 已形成的分析结论：%s" % _clip(conclusion, 520))
    if open_items:
        lines.append("- 待确认/未完成：%s" % _clip(open_items, 320))
    if latest_question:
        lines.append("- 最近用户问题：%s" % latest_question)

    summary = "\n".join(lines)
    if len(summary) <= max_chars:
        return summary

    # Trim the two softest fields first so the hard facts survive.
    for field, budget in (("- 待确认/未完成：", 200),
                          ("- 已形成的分析结论：", 320)):
        for i, line in enumerate(lines):
            if line.startswith(field) and len(line) > budget:
                lines[i] = field + _clip(line[len(field):], budget)
        summary = "\n".join(lines)
        if len(summary) <= max_chars:
            return summary
    return _clip(summary, max_chars)


_BLOCK_FOOTER = (
    "\n\n（以上是本次对话在此前轮次形成的上下文，用于保持连续性。"
    "它与下面的 PAW 事实并不冲突：凡涉及 PAW 状态、数字、决定的事实陈述，"
    "一律以本轮 PAW 上下文与证据为准。）"
)


def render_context_block(summary: str,
                         recent: Sequence[dict[str, Any]],
                         *, epoch: int = 1,
                         max_chars: int = MAX_CONTEXT_BLOCK_CHARS) -> str:
    """The prompt prefix that carries the rotated context forward.

    Guarantees ``len(result) <= max_chars``.  ``max_chars`` is normally the
    leftover room inside the helper's hard limit (see :func:`carry_allowance`),
    so the block can be small; the summary is placed first because it carries
    the load-bearing state, and the newest rounds are then added while they
    fit, clipped per message rather than dropped wholesale.
    """
    max_chars = max(0, int(max_chars))
    budget = max_chars - len(_BLOCK_FOOTER)
    if budget <= 0:
        return ""
    summary = str(summary or "").strip()
    recent = list(recent or [])

    body = _clip(summary, budget) if summary else ""
    remaining = budget - len(body)

    # Newest rounds first: continuity matters most for what was just said.
    rows: list[str] = []
    for msg in reversed(recent):
        if remaining <= 0:
            break
        speaker = "用户" if str(msg.get("role")) == "user" else "Agy"
        content = str(msg.get("content") or "")
        line = "%s：%s" % (speaker, _clip(content, 700))
        if len(line) + 1 > remaining:
            room = remaining - len(speaker) - 2      # "：" plus the newline
            if room <= 0:
                break
            line = "%s：%s" % (speaker, _clip(content, room))
        if len(line) + 1 > remaining:
            break
        rows.append(line)
        remaining -= len(line) + 1
    rows.reverse()

    if rows:
        body = (body + "\n" if body else "") + "【最近对话】\n" + "\n".join(rows)
    if not body:
        return ""
    block = body + _BLOCK_FOOTER
    # Belt and braces: the arithmetic above should already guarantee this.
    return block if len(block) <= max_chars else ""


def rotation_state(v2_context: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Read the rotation counters out of ``v2_context`` with safe defaults."""
    ctx = dict(v2_context or {})
    return {
        "epoch": int(ctx.get("context_epoch") or 1),
        "epoch_start_turn": int(ctx.get("epoch_start_turn") or 0),
        "rotation_count": int(ctx.get("rotation_count") or 0),
        "summary": str(ctx.get("context_summary") or ""),
    }

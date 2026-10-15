(() => {
  // versions.js
  async function renderVersions({ app: app2, id: id2, node: node2, action: action2, api: api2, reference: reference2, notice: notice2, refresh }) {
    const base = "/v1/pm/projects/" + encodeURIComponent(id2);
    const families = (await api2(base + "/families")).items;
    if (families.length) {
      const area = node2("section");
      area.append(node2("h2", "\u8D44\u6599\u7248\u672C"));
      for (const family of families) {
        const card = node2("article");
        card.append(node2("h3", family.title));
        for (const version of family.versions) {
          card.append(node2("p", version.title + " \xB7 " + (version.status === "current" ? "\u25CF \u5F53\u524D\u7248\u672C" : "\u5386\u53F2 \xB7 \u5DF2\u66FF\u6362")), reference2(version));
        }
        const latest = family.rulings.find((r) => !r.undone_by);
        if (latest && latest.previous_source_id) card.append(action2("\u64A4\u9500\u7248\u672C\u88C1\u5B9A", async () => {
          await api2("/v1/pm/changes/" + latest.change_id + "/undo", {});
          await refresh();
          notice2("\u7248\u672C\u53CA\u680F\u76EE\u5F15\u7528\u5DF2\u6062\u590D\u3002");
        }));
        area.append(card);
      }
      app2.append(area);
    }
    const cards = (await api2("/v1/pm/decisions")).items.filter((c) => c.project_id === id2);
    for (const decision of cards) {
      const card = node2("article", void 0, "ai");
      card.append(node2("h2", "\u9700\u8981\u4F60\u51B3\u5B9A"), node2("p", decision.prompt));
      const select = node2("select");
      const fresh = node2("option", "\u4F5C\u4E3A\u72EC\u7ACB\u6587\u6863\u65CF");
      fresh.value = "";
      select.append(fresh);
      for (const family of families) {
        const option = node2("option", "\u5F52\u5165\uFF1A" + family.title);
        option.value = family.id;
        select.append(option);
      }
      card.append(select);
      const name = node2("input");
      name.placeholder = "\u72EC\u7ACB\u6587\u6863\u65CF\u540D\u79F0";
      name.setAttribute("aria-label", "\u6587\u6863\u65CF\u540D\u79F0");
      card.append(name);
      card.append(action2("\u786E\u8BA4\u5F52\u5C5E\u4E0E\u7248\u672C\u88C1\u5B9A", async () => {
        const family = families.find((f) => f.id === select.value);
        const title = family ? family.title : name.value.trim();
        if (!title) throw Error("\u8BF7\u586B\u5199\u6587\u6863\u65CF\u540D\u79F0\u3002");
        await api2("/v1/pm/decisions/" + decision.id + "/resolve", { project_id: id2, family_id: family?.id || null, family_title: title });
        await refresh();
      }));
      app2.append(card);
    }
  }

  // updates.js
  async function renderUpdates({ app: app2, id: id2, node: node2, action: action2, api: api2, reference: reference2, notice: notice2, refresh, sections }) {
    const base = "/v1/pm/projects/" + encodeURIComponent(id2);
    const title = (sectionId) => sections.find((s) => s.id === sectionId)?.title || "\u5F85\u786E\u8BA4\u680F\u76EE";
    const conflicts = (await api2(base + "/conflicts")).items;
    if (conflicts.length) {
      const area = node2("section");
      area.append(node2("h2", "\u51B2\u7A81\u6E05\u5355"));
      for (const conflict of conflicts) {
        const card = node2("article");
        card.append(node2("h3", title(conflict.section_id) + " \xB7 " + (conflict.status === "open" ? "\u25B2 \u5F85\u7528\u6237\u88C1\u51B3" : conflict.status === "stale" ? "\u9700\u91CD\u65B0\u5224\u5B9A" : "\u2713 \u5DF2\u88C1\u51B3")));
        const pair = node2("div", void 0, "comparison");
        for (const [label, text, sid] of [["\u5F53\u524D\u7ED3\u8BBA", conflict.left_text, conflict.left_source_id], ["\u53E6\u4E00\u4EFD\u6709\u6548\u8D44\u6599", conflict.right_text, conflict.right_source_id]]) {
          const col = node2("div");
          col.append(node2("strong", label), node2("p", text), reference2({ source_url: "/v1/pm/sources/" + sid }));
          pair.append(col);
        }
        card.append(pair);
        if (conflict.status === "open") {
          card.append(node2("p", "\u91C7\u7EB3\u65B0\u8D44\u6599\u4F1A\u66FF\u6362\u5F53\u524D\u7ED3\u8BBA\uFF1B\u4FDD\u7559\u5F53\u524D\u5219\u4E0D\u6539\u680F\u76EE\u3002\u4E24\u79CD\u88C1\u51B3\u90FD\u53EF\u64A4\u9500\u3002"));
          for (const [label, choice] of [["\u91C7\u7EB3\u65B0\u8D44\u6599", "incoming"], ["\u4FDD\u7559\u5F53\u524D", "current"]]) card.append(action2(label, async () => {
            await api2("/v1/pm/assessments/" + conflict.assessment_id + "/resolve", { choice });
            await refresh();
            notice2("\u88C1\u51B3\u5DF2\u8BB0\u5F55\u3002");
          }));
        } else if (conflict.resolved_by) card.append(action2("\u64A4\u9500\u88C1\u51B3", async () => {
          await api2("/v1/pm/changes/" + conflict.resolved_by + "/undo", {});
          await refresh();
        }));
        area.append(card);
      }
      app2.append(area);
    }
    const decisions = (await api2(base + "/assessments")).items.filter((a) => a.status === "needs_user");
    for (const decision of decisions) {
      const card = node2("article", void 0, "ai");
      card.append(node2("h2", "\u9700\u8981\u4F60\u51B3\u5B9A \xB7 \u680F\u76EE\u7ED3\u8BBA"), node2("p", decision.text), reference2({ source_url: "/v1/pm/sources/" + decision.source_version_id }), node2("p", "\u6D89\u53CA\u7528\u6237\u4E13\u5C5E\u4E8B\u9879\u6216\u65E0\u6CD5\u81EA\u52A8\u66FF\u6362\u3002\u91C7\u7EB3\u5C06\u66FF\u6362\u8BE5\u680F\u76EE\u7684\u5F53\u524D\u7ED3\u8BBA\uFF1B\u62D2\u7EDD\u5219\u4FDD\u6301\u5F53\u524D\u7ED3\u8BBA\u3002"));
      for (const [label, choice] of [["\u786E\u8BA4\u91C7\u7EB3", "incoming"], ["\u62D2\u7EDD", "reject"]]) card.append(action2(label, async () => {
        await api2("/v1/pm/assessments/" + decision.id + "/resolve", { choice });
        await refresh();
      }));
      app2.append(card);
    }
    const sourceIds = new Set((await api2(base + "/families")).items.flatMap((f) => f.versions.map((v) => v.source_version_id)));
    const coverage = (await api2("/v1/pm/coverage")).items.filter((c) => sourceIds.has(c.source_version_id));
    if (coverage.length) {
      const area = node2("section");
      area.append(node2("h2", "\u8D44\u6599\u8BFB\u53D6\u8986\u76D6"));
      for (const read of coverage) {
        const card = node2("article");
        card.append(node2("h3", read.title), node2("p", read.ratio === null ? "\u8986\u76D6\u7387\u672A\u77E5" : `\u5DF2\u8BFB ${read.read_count}/${read.total_count} \u4E2A\u7ED3\u6784\u5355\u4F4D\uFF08${Math.round(read.ratio * 100)}%\uFF09`));
        for (const unit of read.units.filter((u) => !u.read)) card.append(node2("p", `\u672A\u8BFB ${unit.kind} ${unit.id}\uFF1A${unit.reason}`));
        const details = node2("details");
        details.append(node2("summary", "\u8303\u56F4\u4E0E\u9650\u5236"));
        for (const limitation of read.limitations) details.append(node2("p", limitation));
        card.append(details);
        area.append(card);
      }
      area.append(action2("\u5904\u7406\u672C\u8F6E\u5B9A\u5411\u590D\u8BFB\uFF08\u6700\u591A 5 \u6761\uFF09", async () => {
        const result = await api2("/v1/pm/reread/run", { limit: 5 });
        await refresh();
        notice2(`\u672C\u8F6E\u5904\u7406 ${result.processed} \u6761\uFF1B\u4EC5\u5904\u7406\u5DF2\u89E6\u53D1\u7684\u8D44\u6599\u3002`);
      }));
      app2.append(area);
    }
  }

  // app.js
  var app = document.querySelector("#app");
  var id = decodeURIComponent(location.pathname.split("/").pop());
  function node(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== void 0) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  var session;
  async function api(url, body) {
    let options = {};
    if (body !== void 0) {
      session ?? (session = fetch("/v1/session").then(async (r2) => {
        if (!r2.ok) throw Error("\u672C\u5730\u4F1A\u8BDD\u6682\u4E0D\u53EF\u7528");
        return r2.json();
      }).catch((e) => {
        session = void 0;
        throw e;
      }));
      const local = await session;
      options = { method: "POST", headers: { "Content-Type": "application/json", "x-paw-local-token": local.local_token }, body: JSON.stringify(body) };
    }
    const r = await fetch(url, options);
    const data = await r.json();
    if (!r.ok) throw Error(data.detail || "\u64CD\u4F5C\u5931\u8D25");
    return data;
  }
  function action(label, callback) {
    const b = node("button", label);
    b.onclick = async () => {
      b.disabled = true;
      try {
        await callback();
      } catch (e) {
        notice(e.message);
      } finally {
        b.disabled = false;
      }
    };
    return b;
  }
  function notice(text) {
    let p = document.querySelector("#notice");
    if (!p) {
      p = node("p", void 0, "notice");
      p.id = "notice";
      app.prepend(p);
    }
    p.textContent = text;
  }
  function reference(revision) {
    const a = node("a", "\u67E5\u770B\u6765\u6E90");
    a.href = revision.source_url;
    a.onclick = async (e) => {
      e.preventDefault();
      try {
        const s = await api(a.getAttribute("href"));
        document.querySelector("#source-title").textContent = s.title;
        document.querySelector("#source-text").textContent = s.body;
        document.querySelector("#source").showModal();
      } catch (err) {
        notice(err.message);
      }
    };
    return a;
  }
  function references(revision) {
    const frag = document.createDocumentFragment();
    frag.append(reference(revision));
    for (const source of revision.source_refs || []) if (source.source_version_id !== revision.source_version_id) frag.append(node("span", " \xB7 "), reference(source));
    return frag;
  }
  async function render() {
    const data = await api("/v1/pm/projects/" + encodeURIComponent(id));
    app.replaceChildren(node("h1", data.project.name + " \xB7 \u6D3B\u7684\u9879\u76EE\u6863\u6848"));
    const grid = node("div", void 0, "sections");
    app.append(grid);
    for (const section of data.sections) {
      const card = node("article");
      card.dataset.section = section.id;
      card.append(node("h2", section.title));
      if (section.current) {
        card.append(node("p", section.current.text), references(section.current), node("small", " \xB7 \u5DF2\u6838\u51C6"));
        card.append(action("\u64A4\u9500\u6B64\u6B21\u53D8\u66F4", async () => {
          await api("/v1/pm/changes/" + encodeURIComponent(section.current.change_id) + "/undo", {});
          await render();
          notice("\u5DF2\u64A4\u9500\uFF0C\u539F\u7ED3\u8BBA\u5DF2\u6062\u590D\u3002");
        }));
      } else card.append(node("p", "\u5C1A\u672A\u8BB0\u5F55", "empty"));
      const history = node("details");
      history.append(node("summary", "\u5386\u53F2\uFF08" + section.history.length + "\uFF09"));
      for (const r of section.history) {
        history.append(node("p", r.text + (r.withdrawn ? "\uFF08\u5DF2\u64A4\u9500\uFF09" : "")), references(r));
      }
      card.append(history);
      grid.append(card);
    }
    for (const draft of data.ai_generated) {
      const card = node("article", void 0, "ai");
      card.append(node("strong", "AI \u751F\u6210 \xB7 \u5C1A\u672A\u6210\u4E3A\u6B63\u5F0F\u4E8B\u5B9E"));
      for (const [k, label] of Object.entries({ one_sentence: "\u4E00\u53E5\u8BDD\u8BF4\u660E", current_state: "\u76EE\u524D\u505A\u5230\u54EA\u91CC", key_open_issue: "\u5F53\u524D\u5173\u952E\u95EE\u9898", next_step: "\u4E0B\u4E00\u6B65\u5EFA\u8BAE" })) card.append(node("p", label + "\uFF1A" + draft.content[k]));
      card.append(action("\u91C7\u7EB3", async () => {
        const r = await api("/v1/pm/projects/" + encodeURIComponent(id) + "/derived/" + encodeURIComponent(draft.id) + "/adopt", {});
        notice("\u5DF2\u751F\u6210\u5019\u9009\uFF0C\u524D\u5F80\u201C\u5F85\u6211\u5BA1\u6838\u201D\u6838\u51C6\u540E\u624D\u4F1A\u5199\u5165\u6B63\u5F0F\u6863\u6848\u3002\u5019\u9009\uFF1A" + r.candidate_id);
      }));
      app.append(card);
    }
    if (!data.ai_generated.length) app.append(node("p", "\u6682\u65E0 AI \u751F\u6210\u8BF4\u660E\u3002\u9700\u8981\u586B\u5199 Key \u624D\u80FD\u751F\u6210\uFF1B\u5DF2\u6709\u6B63\u5F0F\u4E8B\u5B9E\u53EF\u76F4\u63A5\u67E5\u770B\u3002", "empty"));
    await renderVersions({ app, id, node, action, api, reference, notice, refresh: render });
    await renderUpdates({ app, id, node, action, api, reference, notice, refresh: render, sections: data.sections });
  }
  document.querySelector("#close-source").onclick = () => document.querySelector("#source").close();
  render().catch((e) => {
    app.replaceChildren(node("h1", "\u9879\u76EE\u6863\u6848\u6682\u4E0D\u53EF\u7528"), node("p", e.message));
  });
})();

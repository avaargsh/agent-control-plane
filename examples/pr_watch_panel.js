(() => {
  "use strict";
  let token = "";
  const el = (id) => document.getElementById(id);
  const msg = (text) => { el("message").textContent = text; };
  const label = (text, css = "") => {
    const node = document.createElement("span");
    node.textContent = String(text);
    if (css) node.className = css;
    return node;
  };
  const api = async (path, options = {}) => {
    if (!token) throw Error("Connect with a local token first.");
    const result = await fetch(path, {
      method: options.method || "GET",
      headers: {
        Authorization: "Bearer " + token,
        ...(options.body ? { "Content-Type": "application/json" } : {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
      credentials: "omit",
    });
    const body = await result.json();
    if (!result.ok) throw Error(body.error || "Request failed");
    return body;
  };
  const goalCard = (goal) => {
    const card = document.createElement("article");
    card.className = "goal";
    const top = document.createElement("div");
    top.className = "goal-top";
    top.append(label(goal.goal_id, "goal-name"), label(goal.state, "pill"));
    card.append(top);
    const meta = document.createElement("p");
    meta.className = "muted";
    meta.textContent = goal.repo + " #" + goal.number + " · " +
      (goal.phase || "NOT_OBSERVED") + " · v" + goal.version;
    card.append(meta);
    const actions = document.createElement("div");
    actions.className = "actions";
    const available = goal.state === "RUNNING" ? ["pause", "cancel"] :
      goal.state === "PAUSED" ? ["resume", "cancel"] : [];
    for (const action of available) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = action === "cancel" ? "danger" : "secondary";
      button.textContent = action[0].toUpperCase() + action.slice(1);
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await api("/api/goals/" + encodeURIComponent(goal.goal_id) + "/" + action, {
            method: "POST", body: { expected_version: goal.version }
          });
          msg(goal.goal_id + ": " + action + " recorded");
          await refresh();
        } catch (error) { msg(error.message); button.disabled = false; }
      });
      actions.append(button);
    }
    card.append(actions);
    return card;
  };
  const refresh = async () => {
    if (!token) return;
    try {
      const results = await Promise.all([
        api("/api/goals"), api("/api/events?after=0")
      ]);
      const goals = results[0];
      const events = results[1];
      el("goals").replaceChildren(...goals.goals.map(goalCard));
      if (!goals.goals.length) el("goals").append(label("No goals registered yet.", "muted"));
      const nodes = events.events.slice().reverse().map((event) => {
        const row = document.createElement("article");
        row.className = "event";
        row.append(label(event.kind, "event-type"));
        const body = document.createElement("p");
        body.textContent = event.summary;
        row.append(body);
        row.append(label(event.goal_id + " · " + event.occurred_at + " · " +
          (event.emitted ? "outbox processed" : "pending outbox"), "muted"));
        return row;
      });
      el("events").replaceChildren(...nodes);
      if (!nodes.length) el("events").append(label("No events yet.", "muted"));
    } catch (error) { msg(error.message); }
  };
  el("connect").addEventListener("click", async () => {
    token = el("token").value;
    el("token").value = "";
    msg("");
    await refresh();
  });
  el("refresh").addEventListener("click", refresh);
  el("create").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/goals", { method: "POST", body: {
        goal_id: el("goal-id").value, repo: el("repo").value,
        number: Number(el("number").value),
      }});
      msg("Goal registered. An authorized worker will perform observations.");
      el("create").reset();
      await refresh();
    } catch (error) { msg(error.message); }
  });
  setInterval(() => { if (token) refresh(); }, 5000);
})();

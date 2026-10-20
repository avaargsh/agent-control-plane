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
  const goalCard = (goal, task) => {
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
    if (task) {
      const status = document.createElement("p");
      status.className = "task-status";
      status.append(label("Task: " + task.state, "pill"));
      status.append(label(" · Launch: " + task.launch_state, "muted"));
      status.append(label(" · Verified outcome: NO", "muted"));
      card.append(status);
    }
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
          const outcome = await api(
            "/api/goals/" + encodeURIComponent(goal.goal_id) + "/" + action, {
              method: "POST", body: { expected_version: goal.version }
            }
          );
          const control = outcome.control;
          const signalState = control ? control.signal_state : "UNCONFIRMED";
          msg(goal.goal_id + ": " + action + " committed; Temporal " + signalState +
            (signalState === "ACKED" ? " (wake accepted, not execution proof)" :
              " (durable action; poll fallback remains active)"));
          await refresh();
          if (control && (signalState === "UNKNOWN" || signalState === "PENDING")) {
            const verify = document.createElement("button");
            verify.type = "button";
            verify.className = "secondary";
            verify.textContent = "Verify signal";
            verify.addEventListener("click", async () => {
              verify.disabled = true;
              try {
                const status = await api(
                  "/api/goals/" + encodeURIComponent(goal.goal_id) +
                    "/controls/" + control.version + "/reconcile",
                  { method: "POST", body: {} }
                );
                msg("Temporal signal history: " + status.control.signal_state +
                  " (missing proof never permits blind resend)");
              } catch (error) { msg(error.message); }
              finally { verify.disabled = false; }
            });
            el("message").after(verify);
          }
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
      const params = new URLSearchParams();
      const state = el("task-state").value;
      const repo = el("task-repo").value.trim();
      if (state) params.set("state", state);
      if (repo) params.set("repo", repo);
      params.set("limit", el("task-limit").value);
      const results = await Promise.all([
        api("/api/goals"), api("/api/events?after=0"),
        api("/api/tasks?" + params.toString()),
      ]);
      const goals = results[0];
      const events = results[1];
      const tasks = results[2].tasks;
      const byId = new Map(goals.goals.map((goal) => [goal.goal_id, goal]));
      const cards = tasks.filter((task) => byId.has(task.task_id)).map(
        (task) => goalCard(byId.get(task.task_id), task)
      );
      el("goals").replaceChildren(...cards);
      if (!cards.length) el("goals").append(label("No matching tasks in the current scoped snapshot.", "muted"));
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
  el("task-state").addEventListener("change", refresh);
  el("task-repo").addEventListener("change", refresh);
  el("task-limit").addEventListener("change", refresh);
  el("create").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const outcome = await api("/api/goals", { method: "POST", body: {
        goal_id: el("goal-id").value, repo: el("repo").value,
        number: Number(el("number").value),
      }});
      const launch = outcome.launch;
      const detail = launch && launch.mode === "LOCAL_ONLY"
        ? "Local-only: configure the trusted Temporal bridge to auto-enroll."
        : launch && (launch.state === "ACKED" || launch.state === "OBSERVED")
          ? "Temporal accepted enrollment; Worker execution is not yet proven."
          : "Enrollment " + (launch ? launch.state : "UNCONFIRMED") +
            ": inspect status before attempting any recovery.";
      msg("Goal registered. " + detail);
      el("create").reset();
      await refresh();
    } catch (error) { msg(error.message); }
  });
  setInterval(() => { if (token) refresh(); }, 5000);
})();

(() => {
  "use strict";
  let token = "";
  let selectedTask = "";
  let refreshEpoch = 0;
  let refreshing = false;
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
    const requestToken = token;
    const result = await fetch(path, {
      method: options.method || "GET",
      headers: {
        Authorization: "Bearer " + requestToken,
        ...(options.body ? { "Content-Type": "application/json" } : {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
      credentials: "omit",
    });
    const body = await result.json();
    if (requestToken !== token) throw Error("Session changed; stale response discarded.");
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
        if (action === "cancel" && !window.confirm("Cancel " + goal.goal_id + "? This cannot be resumed.")) return;
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
    const inspect = document.createElement("button");
    inspect.type = "button";
    inspect.className = "secondary";
    inspect.textContent = "Inspect";
    inspect.addEventListener("click", () => {
      selectedTask = goal.goal_id;
      el("activity-goal").value = selectedTask;
      refresh();
    });
    actions.append(inspect);
    card.append(actions);
    return card;
  };
  const refresh = async () => {
    if (!token) return;
    const epoch = ++refreshEpoch;
    const currentToken = token;
    const currentTask = selectedTask;
    const isCurrent = () => epoch === refreshEpoch && currentToken === token && currentTask === selectedTask;
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
      if (!isCurrent()) return;
      const goals = results[0];
      const events = results[1];
      const tasks = results[2].tasks;
      const byId = new Map(goals.goals.map((goal) => [goal.goal_id, goal]));
      const cards = tasks.filter((task) => byId.has(task.task_id)).map(
        (task) => goalCard(byId.get(task.task_id), task)
      );
      el("goals").replaceChildren(...cards);
      if (!cards.length) el("goals").append(label("No matching tasks in the current scoped snapshot.", "muted"));
      // Keep the selection owner-scoped. Never trust a URL/query supplied
      // identity or display an old user's Task after token changes.
      const choices = goals.goals.map((goal) => goal.goal_id);
      if (selectedTask && !choices.includes(selectedTask)) selectedTask = "";
      const activitySelect = el("activity-goal");
      const options = [new Option("All tasks", "")];
      for (const id of choices) options.push(new Option(id, id));
      activitySelect.replaceChildren(...options);
      activitySelect.value = selectedTask;
      const detail = el("task-detail");
      detail.replaceChildren();
      if (selectedTask) {
        // Fetch the single owner-scoped Task directly rather than treating
        // the filtered list as authoritative for detail.
        const response = await api("/api/tasks/" + encodeURIComponent(selectedTask));
        if (!isCurrent()) return;
        const task = response.task;
        const title = document.createElement("strong");
        title.textContent = task.task_id + " · " + task.state;
        const description = document.createElement("p");
        description.textContent = task.repo + " #" + task.pull_number +
          " · " + (task.observation_phase || "NOT_OBSERVED") +
          " · launch " + task.launch_state +
          " · workflow " + (task.workflow_status || "NOT_OBSERVED");
        const warning = document.createElement("p");
        warning.className = "muted";
        warning.textContent = "Business outcome independently verified: NO";
        detail.append(title, description, warning);
        const [launchResponse, controlResponse] = await Promise.all([
          api("/api/goals/" + encodeURIComponent(selectedTask) + "/launch"),
          api("/api/goals/" + encodeURIComponent(selectedTask) + "/controls"),
        ]);
        if (!isCurrent()) return;
        const launch = launchResponse.launch;
        const launchLine = document.createElement("p");
        launchLine.textContent = "Enrollment: " + launch.state +
          " · " + (launch.mode || "UNSPECIFIED") +
          " · workflow " + (launch.workflow_status || "NOT_OBSERVED");
        detail.append(launchLine);
        const launchVerify = document.createElement("button");
        launchVerify.type = "button";
        launchVerify.className = "secondary";
        launchVerify.textContent = "Reconcile enrollment";
        launchVerify.addEventListener("click", async () => {
          launchVerify.disabled = true;
          try {
            const result = await api(
              "/api/goals/" + encodeURIComponent(selectedTask) +
                "/launch/reconcile", { method: "POST", body: {} }
            );
            msg("Enrollment reconciliation: " + result.launch.state +
              " (does not prove workflow completion)");
            await refresh();
          } catch (error) { msg(error.message); }
          finally { launchVerify.disabled = false; }
        });
        if (["UNKNOWN", "IN_FLIGHT", "PENDING"].includes(launch.state)) {
          detail.append(launchVerify);
        }
        const controlsTitle = document.createElement("strong");
        controlsTitle.textContent = "Control journal";
        detail.append(controlsTitle);
        const controls = controlResponse.controls.slice().reverse();
        if (!controls.length) {
          detail.append(label("No control actions recorded.", "muted"));
        }
        for (const control of controls) {
          const line = document.createElement("div");
          line.className = "control-entry";
          line.append(label(control.action + " · v" + control.version +
            " · Temporal signal " + control.signal_state));
          if (["UNKNOWN", "PENDING"].includes(control.signal_state)) {
            const reconcile = document.createElement("button");
            reconcile.type = "button";
            reconcile.className = "secondary";
            reconcile.textContent = "Reconcile signal";
            reconcile.addEventListener("click", async () => {
              reconcile.disabled = true;
              try {
                const result = await api(
                  "/api/goals/" + encodeURIComponent(selectedTask) +
                    "/controls/" + control.version + "/reconcile",
                  { method: "POST", body: {} }
                );
                msg("Control signal reconciliation: " +
                  result.control.signal_state + " (not execution proof)");
                await refresh();
              } catch (error) { msg(error.message); }
              finally { reconcile.disabled = false; }
            });
            line.append(reconcile);
          }
          detail.append(line);
        }
      } else {
        detail.append(label("Select Inspect on a task to see its details.", "muted"));
      }
      const scopedEvents = events.events.filter(
        (event) => !selectedTask || event.goal_id === selectedTask
      );
      const nodes = scopedEvents.slice().reverse().map((event) => {
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
    } catch (error) { if (isCurrent()) msg(error.message); }
  };
  el("connect").addEventListener("click", async () => {
    ++refreshEpoch;
    token = el("token").value;
    selectedTask = "";
    el("goals").replaceChildren(label("Loading tasks…", "muted"));
    el("task-detail").replaceChildren(label("Select a task to inspect.", "muted"));
    el("events").replaceChildren(label("Loading activity…", "muted"));
    el("token").value = "";
    msg("");
    await refresh();
  });
  el("refresh").addEventListener("click", refresh);
  el("task-state").addEventListener("change", refresh);
  el("task-repo").addEventListener("change", refresh);
  el("task-limit").addEventListener("change", refresh);
  el("activity-goal").addEventListener("change", () => {
    selectedTask = el("activity-goal").value;
    refresh();
  });
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

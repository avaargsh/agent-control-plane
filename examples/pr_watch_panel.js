(() => {
  "use strict";
  let token = "";
  let sessionEpoch = 0;
  let selectedTask = "";
  let refreshEpoch = 0;
  let refreshBusy = false;
  let refreshHadError = false;
  const pendingControls = new Set();
  // In-tab only: uncertainty is not persisted across reloads or logins.
  // A network failure cannot prove that the CAS mutation did not commit.
  const uncertainControls = new Map();
  const el = (id) => document.getElementById(id);
  const msg = (text) => { el("message").textContent = String(text); };
  const setSync = (text, state) => {
    el("sync-state").textContent = text;
    el("sync-state").dataset.state = state;
  };
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
    const body = await result.json().catch(() => ({}));
    if (requestToken !== token) throw Error("Session changed; stale response discarded.");
    if (!result.ok) {
      const detail = typeof body.error === "string" ? body.error : "Request failed";
      const failure = Error("HTTP " + result.status + ": " + detail);
      failure.httpStatus = result.status;
      throw failure;
    }
    if (!body || typeof body !== "object" || Array.isArray(body) || !Object.keys(body).length) {
      throw Error("Missing or invalid response body; write outcome may be unknown.");
    }
    return body;
  };
  const renderRecovery = () => {
    const area = el("control-recovery");
    area.replaceChildren();
    for (const [goalId, attempt] of uncertainControls) {
      const entry = document.createElement("section");
      entry.className = "recovery-entry";
      const note = document.createElement("p");
      note.textContent = goalId + ": " + attempt.action +
        " response lost or uncertain at v" + attempt.expectedVersion +
        ". Do not repeat the action. Read the durable control journal.";
      const check = document.createElement("button");
      check.type = "button";
      check.className = "secondary";
      check.textContent = "Check committed record";
      check.addEventListener("click", async () => {
        check.disabled = true;
        const checkSession = sessionEpoch;
        try {
          // Read-only authority checks: never automatically re-POST the action
          // or send a second Temporal signal after an ambiguous response.
          const [goalReply, recordsReply] = await Promise.all([
            api("/api/goals/" + encodeURIComponent(goalId)),
            api("/api/goals/" + encodeURIComponent(goalId) + "/controls"),
          ]);
          if (checkSession !== sessionEpoch || uncertainControls.get(goalId) !== attempt) return;
          const nextVersion = attempt.expectedVersion + 1;
          const matching = recordsReply.controls.find((record) =>
            record.version === nextVersion && record.action === attempt.action
          );
          if (matching && goalReply.goal.version >= nextVersion) {
            uncertainControls.delete(goalId);
            msg(goalId + ": matching durable " + attempt.action + " recorded at v" +
              nextVersion + " (Temporal " + matching.signal_state +
              "). This does not establish which request committed it or prove execution.");
            await refresh();
          } else if (goalReply.goal.version > attempt.expectedVersion &&
                     recordsReply.controls.some((record) =>
                       record.version === nextVersion && record.action !== attempt.action)) {
            uncertainControls.delete(goalId);
            msg(goalId + ": another action holds v" + nextVersion +
              "; the ambiguous " + attempt.action +
              " cannot commit against that stale version. Review current state.");
            await refresh();
          } else {
            msg(goalId + ": no conclusive control record yet. The request may still arrive; " +
              "keep controls locked and check again. No automatic retry.");
          }
        } catch (error) {
          if (checkSession === sessionEpoch) {
            msg(goalId + ": readback failed: " + error.message +
              ". Outcome still unknown; controls remain locked.");
          }
        } finally {
          if (checkSession === sessionEpoch) {
            check.disabled = false;
            renderRecovery();
          }
        }
      });
      entry.append(note, check);
      area.append(entry);
    }
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
        if (pendingControls.has(goal.goal_id) || uncertainControls.has(goal.goal_id)) return;
        if (action === "cancel" && !window.confirm("Cancel " + goal.goal_id + "? This cannot be resumed.")) return;
        const actionToken = token;
        const actionSession = sessionEpoch;
        pendingControls.add(goal.goal_id);
        for (const controlButton of actions.querySelectorAll("button")) controlButton.disabled = true;
        try {
          const outcome = await api(
            "/api/goals/" + encodeURIComponent(goal.goal_id) + "/" + action, {
              method: "POST", body: { expected_version: goal.version }
            }
          );
          if (actionToken !== token || actionSession !== sessionEpoch) return;
          if (!outcome.control || !outcome.goal ||
              outcome.goal.version !== goal.version + 1 ||
              outcome.control.version !== goal.version + 1 ||
              outcome.control.action !== action) {
            throw Error("Control receipt incomplete; committed outcome must be read back.");
          }
          const control = outcome.control;
          const signalState = control ? control.signal_state : "UNCONFIRMED";
          msg(goal.goal_id + ": " + action + " committed; Temporal " + signalState +
            (signalState === "ACKED" ? " (wake accepted, not execution proof)" :
              " (durable action; poll fallback remains active)") +
            (["UNKNOWN", "PENDING"].includes(signalState) ?
              " — Inspect task to reconcile the uncertain signal." : ""));
        } catch (error) {
          if (actionToken === token && actionSession === sessionEpoch) {
            // A definite 4xx rejection is different from a lost response or
            // 5xx: in the latter case the server may already have committed.
            if (!error.httpStatus || error.httpStatus >= 500) {
              uncertainControls.set(goal.goal_id, {
                action, expectedVersion: goal.version,
              });
              renderRecovery();
              msg(goal.goal_id + ": " + action + " outcome UNKNOWN (" +
                error.message + "). Do not retry. Check committed record.");
            } else {
              msg(goal.goal_id + ": rejected (" + error.message +
                "). Reloading authoritative state; no automatic retry.");
            }
          }
        } finally {
          if (actionSession === sessionEpoch) pendingControls.delete(goal.goal_id);
          // Always re-read the authoritative version, including HTTP 409.
          // Never retry a rejected CAS action automatically.
          if (actionToken === token && actionSession === sessionEpoch) await refresh();
          if (!uncertainControls.has(goal.goal_id) && actionSession === sessionEpoch) {
            for (const controlButton of actions.querySelectorAll("button")) controlButton.disabled = false;
          }
        }
      });
      if (pendingControls.has(goal.goal_id) || uncertainControls.has(goal.goal_id)) button.disabled = true;
      actions.append(button);
    }
    const inspect = document.createElement("button");
    inspect.type = "button";
    inspect.className = "secondary";
    inspect.textContent = "Inspect";
    inspect.addEventListener("click", () => {
      selectedTask = goal.goal_id;
      el("activity-goal").value = selectedTask;
      el("task-detail").replaceChildren(label("Loading task details…", "muted"));
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
    const isRefreshCurrent = () => epoch === refreshEpoch && currentToken === token;
    const isCurrent = () => isRefreshCurrent() && currentTask === selectedTask;
    refreshBusy = true;
    el("refresh").disabled = true;
    setSync("Refreshing…", "loading");
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
      const detail = el("task-detail");
      detail.replaceChildren();
      if (selectedTask) {
        detail.append(label("Loading task details…", "muted"));
        // Fetch the single owner-scoped Task directly rather than treating
        // the filtered list as authoritative for detail.
        const response = await api("/api/tasks/" + encodeURIComponent(selectedTask));
        if (!isCurrent()) return;
        const task = response.task;
        const detailTaskId = selectedTask;
        const detailSession = sessionEpoch;
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
        detail.replaceChildren(title, description, warning);
        const loadingControls = label("Loading enrollment and controls…", "muted");
        detail.append(loadingControls);
        const [launchResponse, controlResponse] = await Promise.all([
          api("/api/goals/" + encodeURIComponent(selectedTask) + "/launch"),
          api("/api/goals/" + encodeURIComponent(selectedTask) + "/controls"),
        ]);
        if (!isCurrent()) return;
        loadingControls.remove();
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
              "/api/goals/" + encodeURIComponent(detailTaskId) +
                "/launch/reconcile", { method: "POST", body: {} }
            );
            if (detailSession !== sessionEpoch) return;
            msg("Enrollment reconciliation: " + result.launch.state +
              " (does not prove workflow completion)");
            await refresh();
          } catch (error) { if (detailSession === sessionEpoch) msg(error.message); }
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
                  "/api/goals/" + encodeURIComponent(detailTaskId) +
                    "/controls/" + control.version + "/reconcile",
                  { method: "POST", body: {} }
                );
                if (detailSession !== sessionEpoch) return;
                msg("Control signal reconciliation: " +
                  result.control.signal_state + " (not execution proof)");
                await refresh();
              } catch (error) { if (detailSession === sessionEpoch) msg(error.message); }
              finally { reconcile.disabled = false; }
            });
            line.append(reconcile);
          }
          detail.append(line);
        }
      } else {
        detail.append(label("Select Inspect on a task to see its details.", "muted"));
      }
      if (refreshHadError) { msg(""); refreshHadError = false; }
      setSync("Up to date", "ready");
    } catch (error) {
      if (isCurrent()) {
        refreshHadError = true;
        setSync("Update failed", "error");
        el("goals").replaceChildren(label("Unable to load tasks; retry Refresh.", "muted"));
        el("task-detail").replaceChildren(label("Task details unavailable.", "muted"));
        el("events").replaceChildren(label("Activity unavailable.", "muted"));
        msg(error.message);
      }
    } finally {
      if (isRefreshCurrent()) {
        refreshBusy = false;
        el("refresh").disabled = false;
      }
    }
  };
  el("connect").addEventListener("click", async () => {
    const enteredToken = el("token").value;
    if (!enteredToken) { msg("Enter a local token to connect."); return; }
    ++refreshEpoch;
    ++sessionEpoch;
    token = enteredToken;
    refreshHadError = false;
    pendingControls.clear();
    uncertainControls.clear();
    renderRecovery();
    selectedTask = "";
    el("goals").replaceChildren(label("Loading tasks…", "muted"));
    el("task-detail").replaceChildren(label("Select a task to inspect.", "muted"));
    el("events").replaceChildren(label("Loading activity…", "muted"));
    setSync("Connecting…", "loading");
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
    el("task-detail").replaceChildren(label("Loading task details…", "muted"));
    refresh();
  });
  el("create").addEventListener("submit", async (event) => {
    event.preventDefault();
    const createSession = sessionEpoch;
    const submit = el("create").querySelector('button[type="submit"]');
    if (submit.disabled) return;
    submit.disabled = true;
    try {
      const outcome = await api("/api/goals", { method: "POST", body: {
        goal_id: el("goal-id").value, repo: el("repo").value,
        number: Number(el("number").value),
      }});
      if (createSession !== sessionEpoch) return;
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
    } catch (error) { if (createSession === sessionEpoch) msg(error.message); }
    finally { submit.disabled = false; }
  });
  setInterval(() => { if (token && !refreshBusy) refresh(); }, 5000);
})();

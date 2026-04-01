Alright. Based on everything we've discussed so far, let me organize the final planning document so you can see it all at once.

1. Product Definition

This system is a context-routing CLI that manages multiple coding agent terminal sessions behind a single user interface.

The user gives natural language commands to only one agent.
Then the upper-level context agent:
	•	Records the user's command
	•	Leaves a short routing memo
	•	Checks the currently alive sessions
	•	Queues work to the most appropriate session, or
	•	If no matching session exists, creates a new terminal session and assigns it

The core purpose is singular.

Reduce context rot that occurs when unrelated tasks get mixed into the same session,
and route efficiently while maintaining sessions on a per-feature/domain basis.

---

2. Core Philosophy

This system is not "an agent that implements tasks better," but rather an orchestrator that first assigns tasks to the appropriate session.

The important principles are as follows.

1) The user's original prompt is preserved as-is

The upper-level agent does not rewrite the user's words and inject them into the actual coding agent.

2) task note is merely a reference memo for routing

The task note is not an implementation directive.
It briefly records in 1-2 lines "which area this task is closest to."

3) task note is not passed to the worker session

The task note is an internal memo for the context agent to reference during the next routing.
Only the user_prompt is delivered to the actual coding agent.

4) sessions.json accumulates past tasks continuously

What tasks each session has handled so far becomes the core basis for the next routing decision.

5) Prioritize reusing existing sessions, but separate if there's a risk of context rot

If relevance is high, queue to the same session; otherwise, create a new session.

---

3. Final Workflow

Step 1. User command input

The user gives natural language commands from a single interface.

Examples:
	•	"Add pagination to the admin user list"
	•	"Add 401/403 distinction to the auth error handling session"

Step 2. Task creation

The context agent creates a task based on this command.

However, what happens here is not refined prompt generation.
Instead, only these two things are recorded:
	•	user_prompt: the user's original text
	•	task_note: a short memo for routing

Examples:
	•	"Looks like an expansion of the admin/users area. Route to related session if available."
	•	"Appears close to the existing auth error handling task."

Step 3. Query current sessions.json

The context agent reads the state and past task history of currently alive terminal sessions.

What it checks:
	•	Current status: idle / busy / blocked / dead
	•	Current task
	•	Queue length
	•	Past task_history
	•	The nature of tasks this session has recently handled

Step 4. Routing decision

Determine which session to assign the new task to.

Decision criteria:
	•	How contextually aligned the new user_prompt is with the existing session's past tasks
	•	Whether it belongs to the same domain/feature group based on task_note
	•	Whether queuing to that session is natural
	•	Whether the risk of context rot is high if assigned incorrectly
	•	Whether the queue is already too long

Step 5. Queue to an existing session if appropriate

If an appropriate session exists:
	•	If idle, assign immediately
	•	If busy, add to the queue to be executed after the current task finishes

In other words, tasks are processed sequentially without interrupting ongoing work.

Step 6. Review new session creation if no appropriate session exists

If there's no related session, check the max_terminal_num set in config.
	•	If there's remaining terminal capacity, create a new session
	•	If the limit has been reached, put it in the global pending queue and wait

Step 7. Only user_prompt is delivered to the worker

Internal memos are not given to the actual coding agent session.

What is delivered is essentially:
	•	The user's original prompt

Minimal system-level session management information can be provided separately if needed,
but the routing task_note is not injected.

Step 8. Update sessions.json after task completion

When a task finishes, it is accumulated and saved in the session's task_history.

What should be recorded:
	•	What the prompt was
	•	What the task_note was
	•	Whether it was completed/failed/aborted
	•	A short result memo if needed

This history becomes the basis for subsequent routing.

---

4. Final Concept of a Task

A task is not an execution specification but a routing unit.

The essential concepts are roughly these:
	•	task_id
	•	user_prompt
	•	task_note
	•	routing
	•	queue_status
	•	created_at

Here, task_note should be very simple.

Example:

{
  "task_id": "task_20260401_014",
  "user_prompt": "Add pagination to the admin user list",
  "task_note": "Looks like an expansion of admin/users list functionality. Route to related session if available.",
  "routing": {
    "assigned_session_id": null,
    "decision": "pending",
    "reason": null
  },
  "queue_status": "pending",
  "created_at": "2026-04-01T10:20:00Z"
}

Important points:
	•	task_note is not a bundle of structured tags
	•	Complex intermediate schemas like intent, domain_tags are removed from the MVP
	•	Keep it at the level of the context agent leaving a one or two line memo

---

5. Final Role of sessions.json

sessions.json is not simply a current state file,
but a registry that holds current state + accumulated past task history.

Each session should have at least the following information:
	•	session_id
	•	terminal_id
	•	status
	•	current_task_id
	•	queue
	•	task_history
	•	created_at
	•	last_heartbeat

Example structure:

{
  "sessions": [
    {
      "session_id": "sess_admin_01",
      "terminal_id": "term_2",
      "status": "busy",
      "current_task_id": "task_20260401_014",
      "queue": ["task_20260401_015"],
      "task_history": [
        {
          "task_id": "task_20260401_003",
          "user_prompt": "Add sorting functionality to the admin user table",
          "task_note": "Task related to admin/users table. Fits well with existing admin session.",
          "status": "completed"
        },
        {
          "task_id": "task_20260401_014",
          "user_prompt": "Add pagination to the admin user list",
          "task_note": "Looks like an expansion of admin/users list functionality. Route to related session if available.",
          "status": "running"
        }
      ],
      "created_at": "2026-04-01T09:28:00Z",
      "last_heartbeat": "2026-04-01T10:22:14Z"
    }
  ]
}

The key point is this.

When the next prompt comes in,
the context agent looks at the current session state and past task_history
and determines "what kind of tasks this session has originally been handling."

---

6. Routing Policy

Routing operates with the following priority.

1) Can it naturally continue in an existing session?

Examples:
	•	Same feature group
	•	Same subdomain
	•	Similar in nature to past prompts
	•	Low session pollution when appended in the same context

If so, send to the existing session.

2) An existing session exists, but would attaching to it make the session dirty?

Examples:
	•	Looks similar but is actually a different feature axis
	•	Near the same folder but with a different purpose
	•	The session has already become too broad

In this case, creating a new session is better even if there's some relevance.

3) Create a new session if no appropriate session exists

Only when below max_terminal_num.

4) If a new session can't be created either, wait globally

If the terminal limit is exceeded, put it in the global queue and wait.

---

7. Queue Policy

Two types of queues are needed.

Session-internal queue

Where tasks already assigned to a specific session wait.

Examples:
	•	Follow-up commands attached to the same admin/users session

Global waiting queue

Tasks that haven't entered any session yet.

Required cases:
	•	No related session
	•	Terminal maximum exceeded
	•	Waiting for creation

So the states are at minimum:
	•	pending
	•	queued_in_session
	•	queued_globally
	•	running
	•	completed
	•	failed

---

8. Role Separation Between Context Agent and Worker Agent

In this design, the two roles are clearly separated.

Context Agent
	•	Receive user input
	•	Create task
	•	Leave task_note
	•	Check sessions.json
	•	Session routing
	•	Queue management
	•	Decide on new terminal/session creation
	•	Update state files

Worker Coding Agent
	•	Execute the assigned user_prompt
	•	Actual coding within the assigned session terminal
	•	Return results
	•	Reflect result status upon completion

In other words:

The context agent is in charge of placement and organization
The coding agent is in charge of execution

---

9. Why We Removed Refined Prompts

This was an important change in this discussion.

Initially, we intended to rewrite the user's input into a clearer prompt and put it into the task, but ultimately it was excluded.

The reasons are as follows.

1) Routing-oriented phrasing can contaminate the execution prompt

The upper-level agent's interpretation can degrade worker performance.

2) The upper-level agent is not an implementation director

The role of the upper layer is "where to assign" not "how to implement."

3) Using the user's original prompt as-is is safer

It's better for the worker to directly receive the user's actual intent.

So the final conclusion is:

refined_prompt is not created.
Instead, only task_note is left, and it is used only for routing reference.

---

10. File Structure

For the MVP, the required files are roughly these.

config.json

System configuration

Examples:
	•	max_terminal_num
	•	queue_policy
	•	session_idle_timeout_min

sessions.json

Currently alive session registry + past task history

tasks.jsonl or tasks.json

Storage for all task creation/routing/state change history

Recommendation:
	•	Full history as an append-only log in tasks.jsonl
	•	Current session state in sessions.json

---

11. Minimum Feature Scope (MVP)

Based on this planning, the MVP covers the following.

Included
	•	User command input
	•	Task creation
	•	Short task_note generation
	•	sessions.json query
	•	Existing session routing
	•	Session queuing
	•	New session creation
	•	Terminal maximum limit
	•	Accumulating task_history in sessions.json
	•	Only user_prompt delivered to worker

Not yet included
	•	Complex multi-agent planner
	•	Inter-session collaboration DAG
	•	Automatic PR creation
	•	Advanced conflict resolution
	•	Long-term memory optimization
	•	Complex semantic vector search

In other words, the MVP is a thin and practical session router.

---

12. Expected Edge Cases

The edge cases that must be considered in the final design are roughly these.

1) sessions.json shows a session as alive, but the actual terminal is dead

Needs to be detected via heartbeat and marked as dead

2) A related session exists but the queue is too long

Need to look at load as well, not just relevance

3) Looks similar but attaching would cause severe context rot

"Whether it's the same task axis" matters more than simple keyword matching

4) Terminal maximum exceeded

Must send to global queue instead of creating a new session

5) A single user command might actually be multiple tasks

In the MVP, it's fine to handle as a single task first, but splitting can be considered later

---

13. Final One-Line Definition

Now your system is most accurately defined as follows.

A CLI-based context orchestrator that preserves the user's original prompt as-is, leaves a short task note for routing, and based on per-session accumulated task history and current state, queues work to the appropriate terminal session or creates a new session to assign it.

---

14. Very Brief Summary

This system ultimately works like this.
	1.	The user speaks to a single agent
	2.	The context agent records that as a task and leaves a short memo
	3.	It looks at existing session history to determine where to assign it
	4.	If there's a matching session, it queues the task
	5.	If not, it checks terminal availability and creates a new session
	6.	Only the original prompt is passed to the worker
	7.	When the task finishes, it continues to accumulate in that session's task history

If you want, we can continue to the next step and draft the minimum schemas (config.json, tasks.jsonl, sessions.json) right away.
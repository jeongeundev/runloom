# Gateless - Product Discovery Context

> Status: Discovery handoff document
>
> Purpose: Preserve the reasoning, changes of direction, weakened hypotheses, current thesis, and unresolved questions from the Gateless product-discovery process so that a coding/design agent can challenge the product before PRD and implementation.
>
> This document is **not** the final PRD or architecture and should not be treated as immutable source of truth.

---

## 1. Why this document exists

Gateless has already gone through substantial product discovery before implementation.

The next step is not to ask a coding agent to implement the current idea immediately. The next step is to give that agent enough context to understand:

- where the idea started;
- which hypotheses were challenged;
- why several directions were weakened or repositioned;
- what currently appears to be the core problem;
- what is considered relatively stable;
- what is still uncertain;
- what should be challenged again before the PRD, domain model, architecture, ADRs, and implementation plan are finalized.

The desired next workflow is:

```text
GATELESS_CONTEXT.md
        ↓
Coding/design agent reads the full discovery history
        ↓
Grill / challenge / identify hidden assumptions
        ↓
PRD
        ↓
Domain Model
        ↓
Work Engine Specification
        ↓
MVP Specification
        ↓
Architecture
        ↓
ADRs
        ↓
Implementation Plan
        ↓
Code
```

The coding/design agent should **not** assume that every current conclusion in this document is correct.

---

## 2. Competition and delivery context

Gateless is being prepared for a competition.

Important constraints:

- The final submission must be a **web service**, not only a library, protocol implementation, CLI, or architecture concept.
- A judge/user should be able to visit the service and understand the product through interaction.
- Implementation time is constrained.
- The product should eventually be publishable on GitHub and reasonably easy for others to try.
- A local/self-hosted path such as `docker compose up` is desirable, but the competition-facing hosted web experience is the immediate priority.
- The MVP should demonstrate the core thesis end-to-end rather than implement a broad enterprise platform.
- Research and falsification have already consumed substantial time. Further discussion should focus on assumptions that materially affect the product or implementation, not restart broad market discovery from zero.

---

## 3. Original motivation: reduce bottlenecks

The broad idea has remained relatively stable:

> **Reduce bottlenecks in work.**

The initial intuition came from organizational coordination.

Work often stops not because nobody is capable of performing it, but because someone must:

- notice that another person/team is needed;
- find the correct owner;
- explain the context;
- request an action;
- wait for approval;
- follow up;
- observe the result;
- decide what happens next.

This led to an early orchestration concept roughly like:

```text
Request
  ↓
Capability Resolution
  ↓
Target Agent
  ↓
Authority Check
  ↓
Human Approval / Escalation
  ↓
Execution
  ↓
Result
```

Early conceptual primitives included:

- Identity
- Capability
- Ownership
- Authority
- Request
- Approval
- Result Correlation

At this stage, Gateless looked close to an **agent-to-agent organizational orchestrator**.

---

## 4. Early falsification: generic orchestration was too weak

The initial orchestration hypothesis was challenged by asking whether ordinary existing tools could already solve much of the problem.

Examples:

### Sharing state or information

Could often be handled by:

- Git repositories;
- documentation;
- shared systems of record.

### Fixed task handoff

Could often be handled by:

- GitHub Actions;
- n8n;
- ordinary webhooks;
- existing workflow automation.

### Messaging

Could often be handled by:

- GitHub Issues/Comments;
- Slack;
- ticketing systems.

### Small cross-owner requests

Could often be:

```text
Webhook
→ Agent
→ Approval
```

without requiring a new platform.

### Result

Several early directions became insufficient as a product on their own:

- generic agent messaging;
- generic agent orchestration;
- GitHub task handoff;
- simple cross-agent requests.

This created the first important constraint:

> **Gateless cannot exist merely because agents need to send messages or tasks to each other.**

---

## 5. Shift from "Target Agent" to "Capability Provider"

The next major conceptual change was to stop assuming that every piece of work should be routed to an AI agent.

A required capability may already be provided by many kinds of systems.

Examples:

```text
backend.code
    → Backend Agent

test.backend
    → GitHub Actions

deploy.staging
    → n8n workflow

security.review
    → Agent or human

approve.api.contract
    → Human

infra.deploy
    → Terraform pipeline
```

This changed the abstraction from:

```text
Target Agent
```

to:

```text
Capability Provider
```

A Provider could be:

- Agent
- Workflow / automation
- API
- Human
- existing external system

The runtime concept became closer to:

```text
Work outcome
    ↓
Required capability
    ↓
Provider resolution
    ↓
Execute
    ↓
Observe
    ↓
Replan
```

This was important because Gateless should **reuse existing execution infrastructure rather than rebuild it**.

---

## 6. Dynamic workflow coordination hypothesis

At this point the product started to resemble a dynamic workflow coordination layer.

An intermediate product statement was:

> **Gateless is a dynamic workflow coordination layer that observes the outcome of work, determines what needs to happen next, and hands the work - with context - to the right human, agent, or system.**

A compact version was:

> **Observe → Decide → Handoff**

This was stronger than static handoff because Gateless would react to outcomes rather than merely execute a predefined sequence.

However, a serious falsifier remained:

> Could one sufficiently capable "super agent" with access to GitHub, Jira, n8n, APIs, and tools simply perform this orchestration itself?

If yes, dynamic orchestration alone would still be a weak product boundary.

This pushed the discovery toward understanding **what coordination problem remains even when execution agents are powerful**.

---

## 7. n8n and existing automation challenged the workflow-builder direction

Existing workflow automation products are already capable.

n8n, for example, can provide:

- visual workflows;
- integrations;
- triggers;
- AI agents;
- human approval;
- execution history;
- observability;
- AI-assisted workflow generation;
- external agent interaction.

Therefore Gateless should **not** differentiate by saying:

- "AI can create an n8n workflow";
- "we add agents to workflows";
- "we connect SaaS APIs";
- "we provide a visual workflow canvas";
- "we execute predefined automation."

These are existing capabilities.

A key conclusion emerged:

> **n8n and similar systems should be treated as execution providers/backends, not as products Gateless needs to replace.**

This also produced a broader product principle:

> **Use what already exists instead of rebuilding execution systems.**

---

## 8. Enterprise AX context sharpened the problem

A relevant enterprise AX role was examined.

The role involved operating and improving an AI/automation environment containing things such as:

- n8n;
- internal agents;
- GitHub Actions;
- Cloudflare/AWS;
- LLM APIs;
- MCP;
- observability;
- Datadog;
- Langfuse;
- Terraform;
- secrets and access permissions.

The important observation was that as organizations adopt more automation, they create another operational layer that humans still coordinate.

Humans may still need to:

- inspect failures;
- decide which problem matters first;
- find the responsible system or person;
- rerun a workflow;
- escalate to DevOps;
- request approval;
- start an agent;
- gather context;
- follow execution;
- decide the next action.

This suggested a possible framing:

> Current AX reduces execution effort, but the automation/agent ecosystem itself creates coordination work.

However, this enterprise framing later proved too narrow as the universal definition of Gateless.

---

## 9. "Logs" evolved into organizational execution data

One idea was to use operational history to discover coordination patterns automatically.

The term "logs" was initially used broadly, but this was refined into several kinds of execution information:

### GitHub

- workflow runs;
- failed steps;
- PR events;
- issue events;
- CI results.

### n8n

- workflow executions;
- success/failure;
- retries;
- execution history.

### Agents

- traces;
- tool calls;
- latency;
- errors;
- results.

### Jira / work-management systems

- issue creation/update;
- assignment;
- status transitions;
- comments;
- changelogs.

### Infrastructure / observability

- deployment failures;
- HTTP errors;
- metrics;
- traces.

A better umbrella concept became:

- **Work Events**
- **Execution Traces**
- or more broadly **Organizational Execution Data**

A possible input model was:

```text
Events
  GitHub / Jira / n8n webhooks

Execution History
  n8n / CI / deploys

Agent Traces
  Langfuse / A2A

Work Context
  Jira / GitHub / other tools

Organization Context
  owners / teams / permissions / capabilities
```

---

## 10. Automation discovery from real work history

A related hypothesis emerged:

Instead of asking users to manually design every workflow, Gateless might observe how work actually moves and identify repeated bottlenecks or handoffs.

Traditional user-driven automation:

```text
Human observes repeated work
    ↓
Human realizes it can be automated
    ↓
Human opens workflow tool
    ↓
Human defines trigger / conditions / actions
    ↓
Human configures credentials
    ↓
Human tests workflow
```

Possible Gateless direction:

```text
Collect actual work events
    ↓
Reconstruct recurring work patterns
    ↓
Find repeated handoffs / waits / approvals
    ↓
Identify automation opportunity
    ↓
Reuse existing agent / workflow / human capability
```

Example:

```text
CI failure
→ Bob reads logs
→ realizes backend issue
→ contacts Alice
→ Alice launches coding agent
→ fix
→ CI
```

Potentially inferred as:

```text
CI failure
→ backend.fix required
→ Backend Agent
→ result
→ CI
```

This led to product language such as:

> **Connect your work. Gateless discovers what to automate.**

and:

> **Observe work. Find bottlenecks. Automate the handoffs.**

This remains interesting, but **full process mining / automatic workflow discovery is currently considered too large for the competition MVP**.

It may be a later capability rather than the first product proof.

---

## 11. Process mining challenged the novelty of "observe and discover bottlenecks"

Research showed that process mining is already an established category.

Products can already:

- ingest event logs;
- reconstruct real processes;
- identify variants;
- find bottlenecks;
- detect deviations;
- provide recommendations;
- sometimes recommend or trigger automation.

Agent execution logs are also increasingly analyzed as process data.

Therefore:

> **"Analyze logs and find bottlenecks" is not sufficient differentiation by itself.**

The useful idea may still remain as an input mechanism, but Gateless needs a clearer execution/coordination thesis.

---

## 12. Existing orchestration products challenged "connect tools and agents"

Another important correction:

Existing enterprise platforms already connect:

- SaaS products;
- APIs;
- agents;
- workflows;
- robots;
- humans;
- external systems.

Therefore:

> **"We connect the tools and agents you already use" is useful product behavior but not a differentiator.**

This matters because Gateless had started drifting toward:

```text
GitHub + Jira + n8n + Agents
→ Gateless
```

as though cross-tool connectivity itself were novel.

It is not.

The question became:

> What does Gateless uniquely decide or manage once those systems are connected?

---

## 13. Jira-centric thinking was explicitly rejected

At one point, examples increasingly revolved around Jira, GitHub, and n8n.

This was corrected.

Gateless did **not** originate as:

- a Jira extension;
- a smarter Jira queue;
- a GitHub workflow product;
- an n8n companion.

These are examples of systems that may already exist in a user's environment.

The principle became:

> **Gateless should reuse existing systems through APIs/protocols because rebuilding their capabilities is wasteful.**

Possible connected systems could include far more than developer tools:

- email;
- calendar;
- CRM;
- commerce;
- support systems;
- documents;
- finance tools;
- internal APIs;
- agents;
- automation products.

Therefore the domain model should not be designed around Jira issues or GitHub artifacts.

---

## 14. The major bottleneck insight: many work streams converge on scarce executors

A more useful model of bottlenecks emerged.

Real work is not simply:

```text
A → B → C
```

Multiple independent work streams often converge on the same person, team, agent, or capability.

Example:

```text
Project A ─ Backend error ─┐
Project B ─ API change ────┤
Project C ─ DB migration ──┤
Project D ─ Incident ──────┼→ Backend capability
Project E ─ Code review ───┤
Project F ─ Permission ────┤
Project G ─ Deploy failure ┘
```

Even if every item has a Jira priority, the actual execution order may still require judgment.

Many tasks can simultaneously be:

- Critical;
- High;
- P1;
- urgent.

But their real impact differs.

Possible considerations include:

- urgency;
- customer/production impact;
- deadline;
- number of downstream tasks blocked;
- dependency depth;
- number of waiting people/agents;
- whether an executor is available;
- whether an agent can perform it;
- whether human judgment is required;
- whether approval is required.

This produced a major conceptual shift:

> **Finding the next executor is not enough. Gateless must first determine which work should happen next.**

---

## 15. "What next?" moved before "Who next?"

Earlier thinking emphasized:

```text
Which agent/provider can do this?
```

The newer model is:

```text
1. WHAT NEXT?
   Which work should happen now?

2. WHO / WHAT NEXT?
   Which executor/provider should perform it?
```

This distinction is central.

Example:

```text
Work A
  High priority
  Blocks 5 downstream works

Work B
  Critical
  Blocks nothing

Work C
  Medium
  Blocks 2 agents and an imminent release
```

A simple static priority field may not produce the best global execution order.

Therefore Gateless may need to reason over a **work graph** rather than merely sort a task list.

This is currently one of the strongest candidates for Gateless's core intelligence.

---

## 16. Current bottleneck definition

The definition broadened from:

> "A large organization cannot find the right owner quickly."

toward:

> **A bottleneck occurs when work progression is delayed because the next useful action, its ordering, or its executor is not resolved quickly enough.**

More technically:

> **Multiple Work items compete for limited executors/capabilities, and Work items can depend on the results of other Work. Poor sequencing and slow delegation reduce overall flow.**

This definition does not require a large organization.

---

## 17. Enterprise → individual generalization

A significant later insight was that the same coordination problem exists for one person.

A solo user may have:

```text
Customer email
Research
Blog writing
Website changes
Accounting
Social media
```

and multiple executors:

```text
Research Agent
Coding Agent
Writing Agent
Browser Agent
Automations
The user
```

Even with many agents, the person may still repeatedly decide:

```text
What should I do next?
    ↓
Which agent should do it?
    ↓
What context should I pass?
    ↓
What result came back?
    ↓
What does that unblock?
    ↓
What should happen next?
```

Thus:

> **One person operating many agents can experience a coordination problem structurally similar to a small organization.**

The number of humans is not the fundamental variable.

The more general variables are:

- number of Work items;
- dependencies;
- available executors;
- capabilities;
- execution state;
- sequencing decisions.

---

## 18. Non-developer direction

This generalization raised another possibility:

Gateless may be especially useful to people who **do not know how to build workflows themselves**.

A workflow automation product generally expects the user to express something like:

```text
When X happens
→ do Y
→ if condition Z
→ call A
→ then call B
```

Even when AI assists workflow creation, the user often begins with an intended workflow.

Gateless could pursue a different UX:

```text
"These are the things happening in my work."

        ↓

Gateless determines:

- what needs attention;
- what should happen first;
- what is blocked;
- what can be delegated;
- which agent/automation/human can do it;
- what becomes possible after completion.
```

This suggests that the primary UI may **not** be a visual workflow canvas.

It may instead look like a dynamic work/attention surface:

```text
Today

12 things need attention

Customer refund
  Waiting 17h
  → Needs your approval

Inventory shortage
  8 orders at risk
  → Purchasing Agent working

Customer inquiries
  → Support Agent working

Campaign content
  → Content Agent preparing

Monthly report
  → Completed
```

The user observes work state rather than manually drawing the execution graph.

This direction is promising but still needs to be challenged:

- Where does the necessary structure come from if the user does not define workflows?
- How much can be inferred safely?
- Is the initial target really non-technical users?
- Which integrations make this credible in an MVP?

---

## 19. Example: non-developer / solo business use case

A possible example is a one-person online business.

Incoming work:

```text
7 refund requests
13 product questions
4 low-stock products
new product registration
Instagram content
supplier order
monthly sales summary
```

Connected systems might eventually include:

```text
Commerce platform
Email
Calendar
Drive
Other SaaS
        ↓
     Gateless
```

Gateless could produce:

```text
1. Reorder low-stock product
   12 orders at risk

2. Refund request #182
   Response SLA approaching

3. Customer questions
   Support Agent can handle

4. Instagram post
   Content Agent can handle

5. Monthly report
   Accounting Agent can handle
```

Then automatically delegate what can be delegated, leaving the human primarily with:

```text
Refund approval
Supplier purchase approval
Content review
```

This example is useful because it demonstrates that Gateless is not inherently a developer product.

It is **not yet a committed MVP persona**.

---

## 20. A2A: from product idea to delegation mechanism

A2A was initially close to the center of the product idea.

The original finance-inspired scenario made A2A attractive because different agents may have different data-access boundaries.

Example:

```text
Fraud Agent

Fraud DB            ✓
Customer Risk       ✓
Compliance DB       ✗


Compliance Agent

Compliance DB       ✓
Regulatory Data     ✓
Fraud DB            ✗
```

A bad architecture would be:

```text
Fraud Agent
→ receives Compliance Agent's credentials
→ reads Compliance DB directly
```

A better collaboration model is:

```text
Fraud Agent
→ "Need compliance.review for TX-123"
→ Compliance Agent
→ Compliance Agent uses its own access
→ returns necessary result
→ Fraud Agent
```

The important security idea is:

> **Each agent executes inside its existing access boundary.**

And:

> **Agents exchange only the request, context, and result needed for collaboration.**

However, A2A itself is not a unique product thesis.

Capability discovery and agent delegation already exist as protocol/platform concepts.

Therefore A2A has been repositioned:

### Earlier

```text
A2A ≈ core Gateless product idea
```

### Current

```text
A2A = an important mechanism by which Gateless can delegate Work to compatible agents
```

A2A may still be important to the MVP because it visibly demonstrates heterogeneous agent collaboration, but this remains an implementation/MVP decision rather than the product definition itself.

---

## 21. Authority and security: central engine was reduced

An earlier architecture included a substantial Gateless Authority Engine.

The finance/A2A discussion revealed that this could be overbuilt.

If an Agent already has:

- its own DB access;
- its own API credentials;
- its own repository permissions;
- its own cloud permissions;

Gateless does not need to reproduce those permissions centrally.

Current principle:

> **Gateless does not give agents more access. It lets agents collaborate while keeping the access they already have.**

Gateless should generally **not**:

- replace IAM;
- centrally manage every DB permission;
- centralize all credentials;
- centralize all business data;
- control an agent's internal tools.

Gateless may still need lightweight business policy.

For example:

```text
backend.code
→ Backend Agent
→ automatic

deploy.staging
→ deployment workflow
→ automatic

deploy.production
→ deployment workflow
→ human confirmation required

budget.approval
→ Finance Manager
→ human
```

This distinction should be preserved:

### Access permission

Existing external IAM / DB / SaaS / cloud permission.

### Business decision / approval policy

A coordination rule that may belong in Gateless.

The exact boundary is still open for design.

---

## 22. Agents reduce execution bottlenecks, but coordination bottlenecks remain

A key insight can be summarized through the human loop.

Without Gateless:

```text
Open task system
    ↓
Inspect many tasks
    ↓
Decide what matters
    ↓
Gather context
    ↓
Choose an agent/tool
    ↓
Start it
    ↓
Pass context
    ↓
Review result
    ↓
Choose next task
    ↓
Repeat
```

Simply notifying the user:

```text
"BACK-53 is important"
```

does not remove the coordination bottleneck if the user must still:

- launch the agent;
- transfer context;
- monitor it;
- determine the next work.

The stronger Gateless behavior is:

```text
Gateless decides:
BACK-53 is the highest-value executable Work
        ↓
Resolves:
backend.fix
        ↓
Finds:
Backend Agent
        ↓
Delegates:
goal + context + expected result
        ↓
Observes result
        ↓
Updates Work state
        ↓
Recomputes what should happen next
```

Humans intervene where judgment, policy, or approval is actually required.

---

## 23. Current core loop

The current strongest conceptual loop is:

```text
Prioritize
    ↓
Delegate
    ↓
Observe
    ↓
Reprioritize
```

A more explicit version is:

```text
OBSERVE
External systems / user / agents produce work and events
        ↓

UNDERSTAND
Normalize the current Work state
        ↓

PRIORITIZE
What should happen next?
        ↓

RESOLVE
Who or what can perform it?
        ↓

DELEGATE
Agent / workflow / API / human
        ↓

OBSERVE RESULT
What changed?
        ↓

UPDATE WORK GRAPH
What became blocked/unblocked/completed?
        ↓

REPRIORITIZE
What should happen next now?
```

Dynamic recomputation is important.

A static workflow might define:

```text
A → B → C → D
```

Gateless is interested in situations where:

- multiple independent Work items coexist;
- results change what becomes executable;
- priorities change;
- providers become available/unavailable;
- downstream Work becomes unblocked;
- the best next action changes.

---

## 24. Current product thesis

The current thesis can be expressed as:

> **Gateless decides what should happen next, then gets it to whoever - or whatever - can do it.**

A longer version:

> **Gateless is a coordination layer for work across humans, AI agents, automations, and existing software. It determines what should happen next, delegates the work to an appropriate executor, observes the result, and continuously reprioritizes the remaining work.**

An earlier useful product line remains:

> **Observe work. Find bottlenecks. Automate the handoffs.**

Another useful principle is:

> **Use what you already have. Coordinate what happens next.**

None of these should be treated as final marketing copy yet.

---

## 25. Current conceptual architecture

The current conceptual architecture is approximately:

```text
       Existing systems / work sources

 GitHub   Jira   Email   SaaS   n8n   Agents   APIs
    \      |       |      |      |      |      /
                     ↓
                 Gateless
                     │
                Observe Work
                     │
                Work Graph
                     │
                 Prioritize
                     │
             Required Capability
                     │
              Provider Resolution
                     │
                  Delegate
          ┌──────────┼──────────┐
          ↓          ↓          ↓
        Agent     Automation   Human
          │
        A2A/API
          │
        Result
          │
          └────────→ Gateless
                        ↓
                 Update + Reprioritize
```

This is conceptual only.

It is **not yet the final software architecture**.

---

## 26. Candidate core domain concepts

Several concepts have repeatedly appeared:

### Work

Something that needs to be accomplished.

### Dependency

A relationship where the state/result of one Work affects whether another Work can proceed.

### Capability

A description of what kind of action is required.

Examples:

```text
backend.fix
content.write
security.review
deploy.staging
```

### Provider / Executor

Something capable of performing Work.

Possible types:

- Agent
- Workflow
- API
- Human

### Execution

One attempt by a Provider to perform Work.

### Result

The output/outcome of an Execution.

### Event

A change observed from an external system or from execution.

These concepts are **candidates**, not finalized definitions.

The next design process should explicitly challenge whether all are necessary and where their boundaries belong.

---

## 27. Why Work and Execution probably need to be separate

One likely domain distinction is:

```text
Work
"What needs to be accomplished?"

Execution
"One attempt to accomplish that Work."
```

Example:

```text
Work:
Fix payment API

Execution #1:
Backend Agent
FAILED

Execution #2:
Backend Agent
SUCCESS
```

Without this distinction, retries, provider changes, failures, and audit history become difficult to model.

However, this should still be validated during domain modeling.

---

## 28. Why the Work Graph may be more important than a task list

If Gateless only sorts independent tasks, it risks becoming an AI task manager.

The stronger hypothesis is that the system understands relationships such as:

```text
A ──→ C ──→ D
└─────────→ D
```

Completion of A may make C executable.

Completion of C may make D executable.

Therefore:

```text
A completed
    ↓
Graph updated
    ↓
C becomes READY
    ↓
Queue recalculated
```

The importance of a Work item may depend partly on how much downstream progress it unlocks.

This is central to the current thesis, but raises major unresolved questions about how dependencies are obtained and trusted.

---

## 29. Candidate prioritization model

For an MVP, a deterministic scoring model has been discussed.

Possible signals:

```text
Base priority
Dependency impact
Deadline urgency
Waiting time
Executor availability
Customer / production impact
```

Example:

```text
Priority Score

Base priority       0-40
Dependency impact   0-25
Deadline urgency    0-20
Waiting time        0-10
Executor available  0-5
```

The exact formula is not decided.

One important architectural preference emerged:

> **Use LLMs to understand unstructured context, but do not initially make the entire scheduler an opaque LLM decision.**

Possible flow:

```text
Unstructured event / issue / request
        ↓
LLM extracts structured signals
        ↓
Deterministic coordination logic
        ↓
Ranked Work
```

This can make the "Why now?" explanation inspectable.

Example UI:

```text
Recommended Next

Fix payment API

Why now?

Production impact
Blocks 4 dependent works
Deadline in 3 hours
Backend Agent available

Expected impact:
4 works unblocked
```

This is still an MVP design hypothesis, not a final algorithm.

---

## 30. Candidate provider-resolution model

After selecting Work:

```text
Work
    ↓
Required capability
    ↓
Available Providers
    ↓
Provider selection
```

Example:

```text
requiredCapability = backend.fix

Backend Agent A
backend.fix ✓

Backend Agent B
backend.fix ✓

Deploy Workflow
backend.fix ✗
```

Possible selection signals:

- capability match;
- availability;
- load;
- cost;
- latency;
- trust;
- policy.

For the MVP, exact capability matching plus availability may be sufficient.

The full semantic capability model is unresolved.

---

## 31. Current web-service UX direction

Because the competition requires a web service, the product needs a visible experience.

The UI should help a user understand:

1. what Work exists;
2. what is blocked/ready/running;
3. what Gateless recommends next;
4. why;
5. who/what is executing it;
6. what result came back;
7. what changed afterward.

Candidate screens:

### Dashboard

```text
Active Work
Running
Blocked
Completed

Recommended Next
```

### Work Queue

A ranked view of current Work.

### Work Detail

Shows:

- state;
- priority/score;
- explanation;
- dependencies;
- required capability;
- selected Provider;
- execution history.

### Activity

Shows the coordination trace:

```text
Event received
→ Work created
→ capability identified
→ priority calculated
→ Provider selected
→ delegated
→ execution completed
→ downstream Work unblocked
→ queue recalculated
```

A dependency graph visualization may also help communicate the thesis.

These screens are illustrative, not locked.

---

## 32. Competition demo concept

One candidate demo uses several Work items:

```text
A. Fix payment API
   blocks C and D
   capability: backend.fix

B. Generate campaign copy
   capability: content.write

C. Run payment tests
   blocked by A
   capability: test.run

D. Deploy payment service
   blocked by A and C
   capability: deploy.staging

E. Security review
   capability: security.review

F. Refactor auth
   capability: backend.fix
```

Providers:

```text
Backend Agent
→ backend.fix

Content Agent
→ content.write

Security Agent
→ security.review

GitHub Actions
→ test.run

n8n
→ deploy.staging
```

Possible execution:

```text
Gateless selects A
    ↓
Backend Agent
    ↓
A completed
    ↓
C becomes READY
    ↓
Gateless reprioritizes
    ↓
C selected
    ↓
GitHub Actions
    ↓
C completed
    ↓
D becomes READY
    ↓
Gateless reprioritizes
    ↓
D selected
    ↓
n8n
```

A human approval could be inserted for production deployment.

The exact demo scenario is still open.

---

## 33. Demo mode is likely important

Because external integrations can fail during judging, a deterministic Demo Mode has been proposed.

Example:

```text
[Run Demo]

Create scenario
    ↓
Show initial Work Graph
    ↓
Calculate Next Work
    ↓
Delegate
    ↓
Simulate or execute result
    ↓
Update graph
    ↓
Reprioritize
```

Real integrations can then replace selected mocked components.

This allows the product thesis to remain demonstrable even if an external service is unavailable.

---

## 34. Potential initial real integrations

A previous implementation discussion proposed:

### Real

- GitHub
- one A2A-compatible Agent

### Optional real

- n8n

### Mock/demo

- Jira
- Slack
- Datadog
- other future connectors

The reasoning:

- GitHub provides visible events/results;
- an Agent demonstrates dynamic delegation;
- n8n demonstrates that Gateless can reuse an existing automation provider rather than replace it.

This is not finalized and should be challenged against the eventual MVP persona.

If the target becomes non-developer users, this integration set may be wrong.

---

## 35. Important distinction: product breadth vs initial use case

The problem appears general enough to apply to:

- individuals;
- solo businesses;
- small teams;
- developer teams;
- AX teams;
- enterprises.

But the competition pitch should **not** become:

> "Everyone can use Gateless."

A useful strategy is:

> **Broad problem model, narrow initial use case.**

The underlying domain may remain general while the demo/pitch selects one clear persona and scenario.

The initial persona remains unresolved.

---

## 36. Competitive observations that shaped the idea

The following broad market observations influenced the product direction.

### Process mining

Already handles:

- event-log ingestion;
- process reconstruction;
- bottleneck detection;
- deviations;
- recommendations;
- some automation recommendations.

Implication:

> Bottleneck discovery alone is insufficient.

### Workflow automation

Already handles:

- triggers;
- conditions;
- APIs;
- agents;
- approvals;
- execution.

Implication:

> Gateless should not become another workflow builder.

### Enterprise orchestration

Existing platforms already coordinate combinations of:

- agents;
- workflows;
- robots;
- humans;
- external systems.

Some already perform dynamic routing/prioritization.

Implication:

> Broad "agentic orchestration" is not a unique category.

### Work-management platforms

Some already:

- assign work to agents;
- suggest priority;
- use organizational context;
- trigger coding agents;
- pass issue/repository context.

Implication:

> "AI prioritizes a ticket and sends it to an agent" alone is insufficient.

### A2A

Already provides capability-oriented agent delegation and separate execution boundaries.

Implication:

> A2A is infrastructure/mechanism, not the product moat.

---

## 37. Novelty claim should remain modest

No claim should be made that:

> "Nobody else does this."

Most individual components already exist.

The possible novelty lies in the **product boundary and combination**:

- neutral/lightweight coordination layer;
- existing work sources;
- dynamic Work/dependency state;
- "what next?" scheduling;
- heterogeneous executors;
- existing permission boundaries;
- result-driven reprioritization;
- potentially usable from individual scale upward.

This combination still needs competitive pressure-testing, but broad market research should not be restarted unless a design decision depends on it.

---

## 38. What Gateless currently is NOT

Current exclusions or strong non-goals:

### Not a Jira replacement

Work must not be modeled as "Jira Issue with extra fields."

### Not an n8n replacement

Do not build another visual workflow automation engine for the MVP.

### Not an agent framework

Gateless should coordinate existing agents rather than require all agents to be implemented inside Gateless.

### Not a central IAM system

Existing Provider permissions remain authoritative.

### Not just an A2A implementation

A2A is one delegation mechanism.

### Not just a notification system

Telling a human "this task is important" does not sufficiently remove the coordination bottleneck.

### Not just process mining

Finding bottlenecks without acting on the coordination loop is insufficient.

### Not just a task-priority assistant

If it only sorts tasks without considering execution/dependency/result state, it risks becoming an AI to-do list.

### Not initially a universal enterprise platform

The competition MVP must remain small enough to implement and explain.

---

## 39. Ideas that were weakened, not fully rejected

Some ideas remain useful but moved away from the center.

### Automatic workflow discovery

Potential future feature.

Not required for MVP.

### Process/event history analysis

Useful input for learning and bottleneck detection.

Not sufficient as the product itself.

### A2A

Important for Agent delegation.

Not the primary product definition.

### Authority

Business approvals may remain.

Centralized permission management is not desired.

### Enterprise AX

Strong use case.

Not the only target or fundamental definition.

### GitHub/Jira/n8n

Useful examples and possible MVP integrations.

Not the product boundary.

### Capability Registry

Likely useful abstraction.

Exact discovery/matching semantics remain open.

---

## 40. Decisions currently considered relatively stable

These should still be challenged if necessary, but there is meaningful reasoning behind them.

1. **The core problem is coordination bottlenecks, not lack of execution tools.**

2. **Work ordering matters before executor selection.**

3. Gateless should answer:
   - What should happen next?
   - Who/what should do it?

4. **The loop is dynamic**, because results can change what is executable/important.

5. **Existing tools should be reused**, not broadly reimplemented.

6. **Executors are heterogeneous**:
   - humans;
   - agents;
   - workflows;
   - APIs.

7. **Existing access boundaries should generally be preserved.**

8. **A2A can be used for agent delegation but is not the product itself.**

9. **The underlying problem can exist for one person as well as a large organization.**

10. **The competition output must be an interactive web service.**

11. **The MVP should demonstrate the coordination loop end-to-end.**

12. **The user should be able to understand why Gateless selected a Work item.**

13. **The primary UX should probably not require users to manually author a full workflow graph.**

---

## 41. Assumptions that MUST still be challenged

The next design agent should aggressively test these.

### A. Does dependency-aware ordering really produce enough user value?

Could a strong agent/task manager already solve most cases?

### B. Where does the Work Graph come from?

Possible sources:

- user input;
- existing project-management systems;
- inferred dependencies;
- agent-created Work;
- event correlation;
- workflow metadata.

This is one of the largest unresolved problems.

### C. Can the system infer dependencies safely?

What happens when inferred dependencies are wrong?

How are cycles handled?

How is confidence represented?

### D. What exactly is "Work"?

Is Work:

- a goal?
- a task?
- a case?
- an incident?
- a request?
- an event-derived unit?

Should Goal / Work / Task be separate concepts?

### E. What does "optimal next Work" mean?

Optimize for:

- total completion time?
- throughput?
- urgency?
- deadline risk?
- number of blocked items?
- business impact?
- user preference?
- cost?

There may be no universal objective function.

### F. Is priority the same as scheduling?

Probably not.

A high-priority Work may not be executable.

A lower-priority Work may unlock more downstream progress.

The model needs clarity.

### G. How autonomous should Gateless be?

Possible modes:

```text
Recommend
→ user clicks Run

Auto-delegate low-risk Work
→ ask for approval on high-risk Work

Fully autonomous within policy
```

The MVP behavior is unresolved.

### H. How are capabilities defined?

- exact strings?
- taxonomy?
- semantic matching?
- A2A Agent Cards?
- user-defined?
- inferred?

### I. What happens when multiple Providers can perform the same capability?

What does routing optimize?

### J. How is execution completion verified?

Is Provider-reported success enough?

Does Gateless need external verification events?

### K. How does a non-technical user connect enough context?

If Gateless does not ask them to build workflows, where does structure come from?

### L. Who is the first competition persona?

Potential candidates include:

- individual using multiple agents;
- solo operator / small business;
- developer;
- small AI-native team;
- enterprise AX operator.

A broad architecture does not remove the need for a narrow pitch.

### M. Is A2A necessary in the MVP?

It may demonstrate the vision well, but it could also consume implementation time without proving the main "what next?" thesis.

### N. What makes this visibly different from an AI task manager?

This must be obvious in the interaction, not only in architecture slides.

### O. What makes this visibly different from an agent orchestrator?

Likewise, the distinction must be experienced.

---

## 42. Open domain-model questions

Before architecture, explicitly resolve:

1. `Goal` vs `Work` vs `Task`
2. `Work` vs `Event`
3. `Work` vs `Execution`
4. `Execution` vs `Result`
5. Dependency types
6. Blocking vs informational relationships
7. Work lifecycle/state machine
8. Retry semantics
9. Cancellation
10. Failure
11. Partial completion
12. Human waiting state
13. Provider availability
14. Capability ownership
15. Dynamic Provider discovery
16. Context references
17. External source identity
18. Correlation/case identity
19. Audit/event history
20. Whether scheduling decisions themselves are persisted entities

Do not allow the database schema to implicitly decide these concepts.

---

## 43. Open work-engine questions

The future `WORK_ENGINE.md` should resolve at least:

### Eligibility

What makes Work executable?

### Blocking

How are dependencies evaluated?

### Scheduling

How are READY works ranked?

### Reprioritization

Which events trigger recalculation?

### Concurrency

Can several Work items execute simultaneously?

This is important: Gateless should probably not choose only one global next Work if multiple independent executors are available.

### Resource constraints

How is Provider capacity represented?

### Preemption

Can a running Work be interrupted because something more important appears?

Probably not needed in MVP, but the model should not accidentally imply it.

### Human approval

Does approval create another Work, Execution state, or policy gate?

### Failure

When does Gateless retry, reroute, or escalate?

### Explainability

How does the system produce "Why now?" without fabricating reasoning?

---

## 44. A particularly important unresolved issue: concurrency

The phrase:

> "What should happen next?"

can accidentally imply that there is exactly one next Work.

In reality:

```text
Backend Agent available
Content Agent available
Human available
```

may allow three independent Work items to execute simultaneously.

Therefore the stronger scheduling question may be:

> **Given the current Work Graph and available executors, which set of Work should be dispatched now?**

This is an important point to challenge in the next design session.

The UI can still highlight a "Recommended Next" item while the engine may schedule multiple executable items.

---

## 45. Another important unresolved issue: optimization objective

"Reduce bottlenecks" is intuitive but not yet mathematically precise.

Potential objectives conflict.

Example:

```text
Work A
High customer urgency
Blocks nothing

Work B
Medium urgency
Unblocks 8 downstream Work items
```

Which should run first?

There may need to be:

- configurable objectives;
- policy weights;
- persona-specific defaults;
- explainable heuristics rather than a universal optimizer.

The MVP can use a simple heuristic, but the PRD should not claim universal optimal scheduling unless justified.

---

## 46. Another unresolved issue: source of truth

Gateless will integrate with systems that already own state.

Examples:

- GitHub owns PR/CI state;
- Jira may own issue state;
- n8n owns workflow execution;
- an Agent owns its execution;
- a commerce platform owns order state.

The architecture must decide:

> Is Gateless a source of truth for Work, or a coordination projection over external sources of truth?

A likely direction is:

```text
External systems own their domain state.
Gateless owns coordination state.
```

But this has not been finalized.

---

## 47. Another unresolved issue: context boundaries

Delegation requires context.

But "pass all available context" conflicts with the permission-boundary principle.

The design needs to determine:

- what context Gateless stores;
- what context is referenced rather than copied;
- what is sent to Providers;
- how sensitive context is filtered;
- whether Providers retrieve context themselves using existing credentials.

For the MVP, this can be simplified, but the architecture should not accidentally centralize all organizational data.

---

## 48. Candidate implementation philosophy

A useful principle for implementation discussion:

> **Make the core loop real first, then replace mocks with integrations.**

Possible progression:

```text
Mock Work
Mock Agent
Mock Workflow
    ↓
Real Work Engine
    ↓
Real reprioritization loop
    ↓
Replace selected boundaries:
GitHub
A2A Agent
n8n
```

The key is that mock components should exercise the **same interfaces and domain logic** as real providers/connectors.

---

## 49. Candidate documentation sequence

After challenging this context, create documents approximately in this order:

```text
docs/
├── GATELESS_CONTEXT.md
│
├── product/
│   ├── PRD.md
│   └── MVP_SPEC.md
│
├── domain/
│   ├── DOMAIN_MODEL.md
│   └── WORK_ENGINE.md
│
├── architecture/
│   └── ARCHITECTURE.md
│
├── adr/
│   ├── 001-....md
│   ├── 002-....md
│   └── ...
│
└── implementation/
    └── PLAN.md
```

Suggested order of reasoning:

```text
PRD
→ Domain Model
→ Work Engine
→ MVP Spec
→ Architecture
→ ADR
→ Implementation Plan
```

Do not select infrastructure first and then force the product model into it.

---

## 50. Candidate ADR topics

Do not create these automatically. Create ADRs only after decisions are actually made.

Likely candidates:

- Work as the core abstraction
- Work vs Execution separation
- Capability-based Provider routing
- Existing permission boundaries remain authoritative
- Deterministic scheduling first
- Provider adapter model
- A2A for agent delegation
- Coordination state vs external source-of-truth state
- Event-driven reprioritization
- Human approval representation
- Context-reference strategy

---

## 51. Instructions for the next coding/design agent

Read this entire document before proposing implementation.

### Your role

You are not being asked to validate the idea politely.

You are being asked to act as a product/domain/architecture design partner before implementation.

### First task

**Grill the current design.**

Find:

- hidden assumptions;
- contradictory abstractions;
- concepts that are too broad;
- concepts that are unnecessarily complex;
- missing state transitions;
- missing failure modes;
- places where the product is indistinguishable from existing categories;
- requirements that sound good in a pitch but cannot be implemented credibly in the competition timeframe;
- assumptions that only work for developer tools;
- assumptions that break for individuals/non-developers;
- places where the proposed domain model leaks details of GitHub/Jira/n8n;
- security or permission assumptions that contradict the "existing boundary" principle.

### Do NOT

- write implementation code yet;
- restart the entire idea from zero;
- suggest a Jira clone;
- suggest an n8n-style workflow builder as the main product;
- treat "connect tools" as the differentiation;
- treat A2A as the product itself;
- introduce a giant enterprise IAM/policy engine without strong justification;
- assume the current context is correct simply because it is documented.

### Do

- challenge unresolved assumptions;
- preserve the reasoning behind already-weakened directions;
- reopen a previous direction only if you can explain why the previous objection was incomplete;
- separate product decisions from implementation decisions;
- distinguish MVP constraints from long-term architecture;
- explicitly identify which questions must be resolved before PRD;
- keep the competition timeframe in mind.

---

## 52. Suggested opening prompt for Codex / Claude Code

Use this document as the starting context and then give the coding/design agent an instruction similar to:

> Read `docs/GATELESS_CONTEXT.md` in full. It contains the product-discovery history for Gateless, including hypotheses that were challenged or repositioned. It is not a final PRD and should not be treated as immutable truth.
>
> Before writing any code, act as a rigorous product/domain/architecture design partner. Grill the current idea. Focus on hidden assumptions, contradictory abstractions, missing domain concepts, failure cases, MVP feasibility, and places where the product may collapse into an AI task manager, agent orchestrator, workflow builder, or process-mining product.
>
> Do not restart broad ideation from zero and do not spend time re-proposing directions whose objections are already documented unless you have a concrete reason to reopen them.
>
> First, produce the smallest set of high-impact questions that must be answered before we can write `PRD.md`. Ask them interactively rather than inventing answers.
>
> After those questions are resolved, help me produce:
>
> 1. `docs/product/PRD.md`
> 2. `docs/domain/DOMAIN_MODEL.md`
> 3. `docs/domain/WORK_ENGINE.md`
> 4. `docs/product/MVP_SPEC.md`
> 5. `docs/architecture/ARCHITECTURE.md`
> 6. necessary ADRs
> 7. `docs/implementation/PLAN.md`
>
> Only after these are sufficiently stable should implementation begin.

---

## 53. Final handoff summary

The Gateless idea has moved through several layers:

```text
Reduce organizational bottlenecks
        ↓
Agent-to-agent orchestration
        ↓
Generic orchestration challenged
        ↓
Target Agent → Capability Provider
        ↓
Dynamic workflow coordination
        ↓
Existing automation/orchestration challenged
        ↓
Observe execution and discover bottlenecks
        ↓
Process mining challenged novelty
        ↓
Cross-tool integration challenged novelty
        ↓
Scarce capability / queue bottleneck insight
        ↓
"What should happen next?" becomes primary
        ↓
"Who/what should do it?" becomes secondary
        ↓
A2A repositioned as delegation mechanism
        ↓
Central authority reduced in favor of
existing Provider permission boundaries
        ↓
Enterprise-only framing challenged
        ↓
Same coordination problem recognized
for individuals with multiple agents
        ↓
Potential non-developer / workflow-less UX
        ↓
Current thesis:

Prioritize
→ Delegate
→ Observe
→ Reprioritize
```

The current product direction is coherent enough to begin formal product/domain design, but **not mature enough to skip the challenge phase and jump directly into code**.

The next agent should use this history to find what the previous discussion missed, then turn the surviving decisions into precise documents.

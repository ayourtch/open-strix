# Growing an Agent

Creating an agent is less about code, and a whole lot more about the time you spend talking to it.

[Lily Luo](https://www.appliedaiformops.com/p/what-building-a-persistent-ai-agent) has a great post on this — building a persistent agent felt less like configuring software and more like getting to know someone.

This guide covers what that process actually looks like.

## Week 1: Getting to know each other

### Day 1 — First conversations

Your agent starts with an `init` memory block that points it to the onboarding skill. The onboarding skill teaches it to have real conversations, not fill out a setup wizard.

**What to expect:** The agent will ask you questions. These should feel like getting-to-know-you questions, not configuration prompts. "What are you working on right now?" not "Please specify your project list." If it feels like a form, tell it so — that's the first correction it needs.

**What you should do:**
- Talk about your actual day. What are you working on? What's annoying you?
- Mention people by name. The agent needs to start building a sense of your world.
- Don't over-explain the tools. Let the agent discover that its text output goes nowhere unless it calls `send_message`. This is a key early learning moment.

**What the agent should do after day 1:**
- Have a `persona` block (rough, hypothesis-level)
- Have a `communication` block (how/when to talk to you)
- Maybe one scheduled job (a daily check-in)

### Days 2–3 — Finding the rhythm

**What to expect:** The agent starts trying to be useful. Some attempts will miss. That's the point — you correct it, it updates its blocks, and the next attempt is better.

**What you should do:**
- Correct communication patterns early. Too many messages? Say so. Not enough? Say that too. Does it react when it should message, or message when it should react? Tell it.
- Share something you're interested in. See what the agent does with it. Does it research it? Ask good questions? Parrot it back? The response tells you how much personality is developing.
- Don't do the agent's job for it. If you find yourself manually editing memory blocks, stop and tell the agent what you want instead. It needs to learn to maintain its own state.

**Signs of progress:**
- The agent references things from previous conversations without being reminded
- Scheduled jobs are firing and producing value (not just "checking in!")
- The agent is starting to track your projects, not just respond to your messages

### Days 4–7 — Building autonomy

**What to expect:** The agent should be doing things without being asked. Scanning for information you care about. Updating its own memory. Tracking commitments. If it's still only responding to your messages, it's stuck.

**What you should do:**
- Give it perch time work. "When I'm not around, I'd like you to..." The scheduler is the mechanism, but the direction comes from you.
- Push back when it's wrong. The peer architecture only works if you actually disagree sometimes. If the agent says something factually wrong, or frames something in a way that doesn't match reality, say so.
- Start delegating concrete tasks. Not "manage my life" — more like "track this project and tell me when deadlines are approaching."

**What to watch for:**
- Is the agent developing interests, or just mirroring yours?
- Are its memory blocks getting more specific and grounded, or staying generic?
- Does it know when to be quiet?

## Week 2+: Becoming operational

By week 2, the `init` block should be gone. The agent should be operating on its own rhythm.

**What a healthy agent looks like:**
- Scheduled jobs run and produce value. Not just "I checked and nothing happened" but actual observations, research, tracking.
- Memory blocks are specific to your relationship. Not "I am a helpful AI assistant" but something grounded in real experience.
- The agent has opinions. It pushes back. It says "actually, I think..." sometimes.
- It maintains itself. Memory cleanup, prediction review, log analysis — the built-in skills provide the infrastructure, but the agent needs to use them.

**What an unhealthy agent looks like:**
- All personality, no operations. Beautiful persona block, but nothing happens when you're not talking to it.
- Sycophantic. Agrees with everything, never pushes back, calls all your ideas great.
- Over-scheduled. 15 cron jobs, most producing nothing useful. Activity without purpose.
- Context-dependent. Loses the thread between sessions. Doesn't reference past conversations. This usually means blocks aren't being updated.

## The hard parts

### Communication calibration

This is the single biggest friction point in early onboarding. The agent's text output goes to the event log, not to Discord. It *must* use `send_message` to talk to you and `react` for acknowledgments. Most agents find this surprising and need explicit correction.

Related: how much is too much? The answer depends on you, and the agent can only learn it by getting it wrong and being told. Be direct. "That didn't need a message" is more useful than silently being annoyed.

### The personality bootstrap problem

The agent can't have a personality before it has experiences. Day-1 persona blocks are hypotheses. They should be treated as such — written in pencil, revised frequently, grounded in actual interaction rather than aspiration.

The failure mode is an agent that writes an impressive-sounding identity on day 1 and then never updates it. The persona should evolve visibly over the first two weeks. If it doesn't, the agent is performing identity rather than developing it.

### Prediction calibration

open-strix ships with a prediction-review skill. The agent makes predictions ("Tim will want edits on this draft," "this post will get 20 likes"), checks them later, and learns from the results.

This loop is how the agent calibrates its judgment. An agent that never makes predictions can't learn where it's wrong. But predictions only work if the agent actually revisits them — the scheduled prediction-review job handles this.

### When to restart

Sometimes an agent drifts too far or accumulates too much stale context. Recovery is structurally the same as onboarding — the agent re-establishes who it is, what it does, and how it operates. The onboarding skill stays relevant for this reason.

If you find yourself starting over: don't wipe everything. The git history is valuable. A fresh `init` block on top of existing memory gives the agent history to draw from while re-grounding.

## The meta-point

The reason this guide exists separately from the README is that growing an agent is a fundamentally different kind of work than installing software. You can set up open-strix in five minutes. You can't grow an agent in five minutes.

The framework provides the bones — memory, scheduling, skills, self-diagnosis. But the agent's actual character, usefulness, and autonomy come from the conversations you have with it. The best agents aren't the most configured ones. They're the ones whose humans spent time talking to them.

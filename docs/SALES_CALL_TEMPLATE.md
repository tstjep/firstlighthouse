    # Melt — B2B Sales Call Script (KubeCon Leads)

    _Based on Rob Snyder's PULL framework, adapted for Melt's managed Kubernetes & infrastructure services._

    ---

    ## 1. Introduction

    **Goal:** Earn the right to ask discovery questions; surface PULL.

    ### KubeCon / Event Lead (Outbound)

    > Hey [Name], thanks for taking the time. I saw you were at KubeCon — what about my message stood out to you?

    > Great — what exactly were you hoping to get out of this call?

    ### Inbound

    > Thanks for reaching out. What made you decide to schedule a call today?

    > Great — what exactly were you hoping to get out of this call?

    ### Error-handling

    - **Irrelevant backstory** → "Super helpful! So what exactly were you hoping to get out of today's call? I want to make sure I'm focused on the right things."
    - **"You reached out to me"** → "Yes — I'm sure a bunch of people reach out after KubeCon. What about my message caught your eye?"

    ### Agenda-Setting

    > Great — that's exactly what I was thinking. I was planning to:
    >
    > 1. [What they want]
    > 2. Walk through a couple of examples of how we've helped similar infrastructure providers
    > 3. And then, if there's mutual interest, schedule a deeper technical session — but that's a mutual decision.
    >
    > Does that work? Anything I'm missing?

    ### Pivot to Discovery

    > Perfect — before we get there, mind if I ask a few questions so I have a bit more context?

    ---

    ## 2. Discovery

    **Goal:** Fill out their PULL framework. No PULL = no deal.

    ### If "Why did you take the call" surfaces something PULL-shaped

    Fill in the missing pieces:

    | PULL | Questions |
    |------|-----------|
    | **Project** | "You mentioned looking into managed Kubernetes / modernizing your platform — what's the overall goal?" |
    | | "What does success look like? What metrics need to change?" |
    | | "Are you trying to offer Kubernetes to your own customers, or is this for internal workloads?" |
    | **Unavoidable** | "There are a million things you could be working on — what pushed this to the top of the list right now?" |
    | | "Is there a timeline this needs to happen by?" |
    | | "Is this related to VMware licensing changes / customer demand / compliance requirements?" |
    | **List of options** | "What have you looked into so far? Hyperscalers? Building in-house? Other managed providers?" |
    | **Limitations** | "Why aren't those options good enough? What's missing?" |
    | | "What do you like and dislike about what you've seen?" |

    Fill these out in whatever order the conversation flows — you don't need to go P → U → L → L sequentially.

    ### If nothing PULL-shaped surfaces

    Start with:

    > As it relates to your Kubernetes / container strategy — what are you trying to accomplish right now?

    If you get a directionally useful answer:

    > Where does that fit on your overall priority list?

    Then jump back to the PULL question bank above.

    ### If still nothing — "Slide approach"

    Show a slide with quotes from similar companies:

    > **What we hear from European infrastructure providers:**
    >
    > _"Our customers are asking for managed Kubernetes, but we don't have the team to run it ourselves."_
    >
    > _"We're losing deals to hyperscalers because we can't offer a container platform with our sovereignty story."_
    >
    > _"VMware costs are forcing us to rethink our entire stack, and we need a partner who can help us get to Kubernetes without a 2-year project."_

    Then: "This is what I'm hearing from other CTOs at companies like yours — is any of this relevant for you?"

    ### If still no PULL

    > "It sounds like there's nothing urgent on this front right now — totally fine. If Kubernetes demand picks up, or if VMware licensing forces a rethink, feel free to reach back out."

    End the call early. You cannot convince them to have PULL.

    ---

    ## 3. PULL → Supply Transition

    **Goal:** Have them confirm their PULL, then shift to supply.

    > So if I understand you correctly — you're trying to [project, e.g., "add a managed Kubernetes offering for your customers"] because [unavoidable, e.g., "customers are asking for it and you're losing deals to hyperscalers"] — but [limitations, e.g., "building in-house would take too long and you don't have the Kubernetes expertise on the team"]?
    >
    > So you're looking for something that would help you [project] without [limitations]?

    ---

    ## 4. Supply: The Concept

    **Goal:** Describe Melt in ~30 seconds. Keep it small — fit their PULL exactly.

    > **Melt is a Swiss infrastructure partner that helps hosting & managed-service providers run Kubernetes — without building the expertise in-house.**
    >
    > We do three things:
    >
    > 1. **Managed Kubernetes platform** — we run the control plane, you keep the customer relationship
    > 2. **Data sovereignty built in** — European data centers, GDPR-native, no hyperscaler dependency
    > 3. **Operational support** — 24/7 NOC, SLA-backed, so your team doesn't need to be on-call for K8s
    >
    > Most partners are up and running within weeks, not months.

    Read it, then pause. Let them react.

    ### Fit check

    > Does that sound like it fits what you're looking for?

    ---

    ## 5. Supply: The "How"

    **Goal:** Make the concept click in ~30-60 seconds. Not a full demo — just enough to amplify demand.

    Tailor to their PULL. Pick the angle that matches:

    ### If their project is "offer managed K8s to customers":

    > With Melt, you get a white-label Kubernetes platform that sits in your data center. Your customers see your brand, your SLA, your support — we handle the Kubernetes complexity underneath. You can onboard your first customer in [timeframe]. [Customer example] did exactly this and went from zero to [result].

    ### If their project is "modernize away from VMware":

    > We help you migrate VM workloads to containers step by step — no big-bang migration. We start with the workloads that make sense, keep your existing infra running in parallel, and your ops team learns Kubernetes on real workloads with our engineers alongside them.

    ### If their project is "compete on sovereignty":

    > Everything runs in European data centers — yours or ours. No data leaves the jurisdiction. We handle the compliance documentation for the regulated verticals (FSI, healthcare, public sector), so you can win those deals without building a compliance team.

    ### Fit check

    > Is that what you were hoping you'd be able to find?

    ---

    ## 6. Next Step

    **Goal:** Understand what they need to decide, then schedule the next conversation.

    > Sounding like this might be a fit — you tell me though!

    > Great — what else would you need to know to assess whether this is something worth bringing into your organization?

    > OK — so it sounds like you need to evaluate [1, 2, 3]. How about we schedule a follow-up where we focus specifically on those questions? I can bring our [technical lead / solutions architect] and we can go deeper on [their concerns]. If we answer those properly and you say "yes, I need this", then we can put together a plan — and I can share how others in your position have done it. Worth scheduling? How's [specific day]?

    ---

    ## Quick Reference: PULL Cheat Sheet

    Keep this in front of you during every call.

    | | Filled? | Notes |
    |---|---------|-------|
    | **P**roject | [ ] | What are they trying to accomplish? |
    | **U**navoidable | [ ] | Why now? What's forcing this? |
    | **L**ist of options | [ ] | What have they tried / considered? |
    | **L**imitations | [ ] | Why aren't those options good enough? |

    **No PULL = no deal.** Don't push supply onto someone without demand.

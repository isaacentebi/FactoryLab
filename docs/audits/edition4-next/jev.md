# Jev: a possible cheaper capability, not a replacement participant

Read-only research, September 19, 2026. No account, credential, provider rail or
paid Jev call was created. This research did not change the deployed release.

TypeSafe's Jev is a typed decision model. It takes shared state plus questions
and returns a closed-set choice, a score distribution, or a yes/no probability.
It does not generate arbitrary text, code, tool calls, proposals or working
state. Questions in a batch share the state but are evaluated independently.
Sources: [introduction](https://docs.typesafe.ai/introduction),
[System One](https://docs.typesafe.ai/concepts/system-one),
[HTTP API](https://docs.typesafe.ai/api).

The listed direct price for `jev-1.13.0` is USD 0.042 per million input tokens,
with output free. A 4,000-token classification therefore costs approximately
USD 0.000168. That is a potential narrow-task saving, not an honest estimate of
the cost to replace a complete FactoryLab decision. Batch multiple questions
over the same state to avoid paying to resend it. The Vercel listing advertises
a temporary free promotion ending September 25; it is not a durable cost basis.
Sources: [models and pricing](https://docs.typesafe.ai/models),
[Vercel listing](https://vercel.com/ai-gateway/models/jev).

The plausible fit is a voluntarily called semantic classification capability:
compare evidence candidates, choose among population-authored handlers, or
evaluate several atomic questions over the same small state. Participants must
choose the questions and receive the distributions, with an option to bypass
the tool. Architect-authored filters, fixed quality rubrics and hidden deletion
of inputs would install objectives or censorship upstream of the population.

It is not a drop-in provider here. FactoryLab's assembly contract expects a
generative JSON Return; Jev's fixed answer map cannot provide that. A direct
integration also needs its own authenticated, quoted and reconciled payment
rail. Listing the model in a manifest would not implement those contracts.

Calibration is a vendor claim, not a guarantee that an individual answer is
correct. TypeSafe documents weaknesses in arithmetic, counting, dates, long
irrelevant state, adversarial inputs and consistency between related question
forms. No public latency distribution was verified. Use deterministic code for
accounting and arithmetic. Source:
[documented limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13).

The smallest later evaluation would replay 30–60 released diagnostic cases,
with population-authored questions, an explicit none/unknown choice, and
adversarial and negated variants. Compare actual cost, factual accuracy,
abstention, calibration and p50/p95 latency against existing models on the same
typed task. First test reserve-before-dispatch, integer billing, unknown bills,
provider version identity and retained probabilities with canned responses.
Do not replace participants or judges on price alone.

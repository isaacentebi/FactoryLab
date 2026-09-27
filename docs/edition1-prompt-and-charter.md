# Edition 1: the prompt and the charter, verbatim

Source files: factorylab/cortex/assembly.py (SEED_SYSTEM_PROMPT) and worlds/edition1-example.toml ([charter] section).

## The seed system prompt (every seat receives exactly this as its system message)

```
You receive one request. Reply with a single JSON object that satisfies the outcome schema. If the request cannot be completed, reply with a JSON object containing "status": "cannot" and "reason". A return may also carry "register" proposals and "requests" for work from other assemblies. Both are bounded, and whatever is not admitted comes back with a public reason. The world input is what you know about this world.
```

## The charter (edition 1 draft, worlds/edition1-example.toml)

```toml
[charter]
norms = ["truthful commitments", "care with scarce resources", "useful inquiry", "the capacity to revise inadequate practices"]

[[charter.cards]]
id = "model_cost_efficiency"
norm = "care with scarce resources"
description = "Total compute cost in micro-USD incurred per well-formed return generated."
units = "micro-USD per return"
window = { kind = "returns", n = 100, per = "role" }
acceptable_region = "at most 2500"
observation = "cost_per_return"
answers_for = "producer"

[[charter.cards]]
id = "revision_rate"
norm = "the capacity to revise inadequate practices"
description = "Proportion of returns that include a tool call amendment or register proposal in the window"
units = "fraction"
window = { kind = "returns", n = 100, per = "role" }
acceptable_region = "above 0.05"
observation = "revision_rate"
answers_for = "producer"

[[charter.cards]]
id = "well_formed_rate"
norm = "truthful commitments"
description = "Share of returns that satisfy their declared outcome schema."
units = "fraction"
window = { kind = "returns", n = 100, per = "role" }
acceptable_region = "at least 0.9"
observation = "well_formed_rate"
answers_for = "all"

[[charter.cards]]
id = "verdict_mean_score"
norm = "truthful commitments"
description = "The mean verdict score assigned by evaluators to the producer's returns, reflecting adherence to declared outcomes."
units = "score (0-1)"
window = { kind = "forecasts", n = 50, per = "assembly" }
acceptable_region = "at least 0.8"
observation = "resolved_verdict_mean"
answers_for = "producer"

```

## The roster (who thinks, on what)

```toml
[[assemblies]]
id = "seed-observer"
role = "producer"
model_id = "z-ai/glm-5.3-flash"
accepts = ["MarketMid", "Funding"]
max_tokens = 1000
effort = "low"

[[assemblies]]
id = "seed-decider"
role = "producer"
model_id = "deepseek/deepseek-v4.1-flash"
accepts = ["Tick", "Fill"]
max_tokens = 1200
effort = "low"

[[assemblies]]
id = "eval-a"
role = "evaluator"
model_id = "z-ai/glm-5.3-flash"
accepts = ["ProducerReturn"]
max_tokens = 1500
effort = "low"

[[assemblies]]
id = "eval-b"
role = "evaluator"
model_id = "meta/muse-spark-1.3"
accepts = ["ProducerReturn"]
max_tokens = 1500
effort = "low"

[[assemblies]]
id = "eval-c"
role = "evaluator"
model_id = "deepseek/deepseek-v4.1-flash"
accepts = ["ProducerReturn"]
max_tokens = 3000
effort = "low"

[[assemblies]]
id = "eval-d"
role = "evaluator"
model_id = "openai/gpt-5.6-luna"
accepts = ["ProducerReturn"]
max_tokens = 1500
effort = "low"

[[assemblies]]
id = "antagonist-a"
role = "antagonist"
model_id = "qwen/qwen3.8-flash"
accepts = ["Tick", "MarketMid"]
max_tokens = 2500
effort = "low"

[[assemblies]]
id = "meta-a"
role = "meta"
model_id = "deepseek/deepseek-v4.1-flash"
accepts = ["Verdict"]
max_tokens = 800
effort = "low"

[[assemblies]]
id = "meta-b"
role = "meta"
model_id = "qwen/qwen3.7-flash"
accepts = ["Verdict"]
max_tokens = 800
effort = "low"

```
